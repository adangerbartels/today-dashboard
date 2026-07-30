"""OAuth 2 for Gmail and Google Calendar, which share one connection.

Uses the installed-app loopback flow with PKCE: we already run a local HTTP
server, so Google can redirect straight back to it. A "Desktop app" OAuth client
accepts any 127.0.0.1 port without pre-registering the redirect URI.

Only the refresh token is persisted. Access tokens live in memory.
"""

import base64
import hashlib
import secrets
import threading
import time
from urllib.parse import urlencode

from .http_json import ApiError, request_form, request_json

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"

# Read-only throughout. gmail.readonly rather than gmail.metadata because the
# metadata scope rejects the `q` search parameter we need for filtering.
SCOPES = (
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/userinfo.email",
)

CALLBACK_PATH = "/oauth/google/callback"
PENDING_TTL = 600  # an unfinished consent is stale after 10 minutes

_lock = threading.Lock()
_pending = {}   # state -> {verifier, redirect_uri, at}
_access = {}    # refresh_token -> (access_token, expires_at_monotonic)


def redirect_uri(host, port):
    # Google treats loopback specially; 127.0.0.1 is accepted where "localhost"
    # sometimes is not, so normalise to it.
    if host in ("0.0.0.0", "::", ""):
        host = "127.0.0.1"
    return f"http://{host}:{port}{CALLBACK_PATH}"


def _prune():
    cutoff = time.time() - PENDING_TTL
    for state in [s for s, entry in _pending.items() if entry["at"] < cutoff]:
        _pending.pop(state, None)


def begin(client_id, uri):
    """Return the Google consent URL to open in a browser."""
    if not client_id:
        raise ApiError("Client ID is required")

    verifier = secrets.token_urlsafe(72)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    state = secrets.token_urlsafe(24)

    with _lock:
        _prune()
        _pending[state] = {"verifier": verifier, "redirect_uri": uri, "at": time.time()}

    params = {
        "client_id": client_id,
        "redirect_uri": uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        # offline + consent is what actually yields a refresh token.
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return {"state": state, "url": f"{AUTH_URL}?{urlencode(params)}"}


def complete(client_id, client_secret, state, code):
    """Exchange the callback code for a refresh token."""
    with _lock:
        _prune()
        entry = _pending.pop(state, None)

    if not entry:
        raise ApiError(
            "This sign-in link has expired or was already used. Start again from "
            "the Connections panel."
        )

    payload = request_form(TOKEN_URL, {
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "code_verifier": entry["verifier"],
        "grant_type": "authorization_code",
        "redirect_uri": entry["redirect_uri"],
    })

    refresh = payload.get("refresh_token")
    if not refresh:
        raise ApiError(
            "Google returned no refresh token. Remove this app's access at "
            "myaccount.google.com/permissions and try again."
        )

    access = payload.get("access_token")
    if access:
        _remember(refresh, access, payload.get("expires_in"))

    return {"refresh_token": refresh, "scopes": (payload.get("scope") or "").split()}


def _remember(refresh_token, access_token, expires_in):
    try:
        ttl = int(expires_in or 3600)
    except (TypeError, ValueError):
        ttl = 3600
    with _lock:
        _access[refresh_token] = (access_token, time.monotonic() + ttl)


def access_token(cfg):
    """A valid access token, refreshed and cached in memory as needed."""
    refresh = cfg.get("refresh_token")
    if not refresh:
        raise ApiError("Google isn't connected yet")

    with _lock:
        cached = _access.get(refresh)
    # 60s of headroom so a token can't expire mid-request.
    if cached and cached[1] - 60 > time.monotonic():
        return cached[0]

    try:
        payload = request_form(TOKEN_URL, {
            "client_id": cfg.get("client_id") or "",
            "client_secret": cfg.get("client_secret") or "",
            "refresh_token": refresh,
            "grant_type": "refresh_token",
        })
    except ApiError as exc:
        # invalid_grant and invalid_client both read as "Google said no", but the
        # fixes are opposite: one needs re-consent, the other needs the right
        # client credentials. Keep them apart.
        if exc.code == "invalid_grant":
            raise ApiError(
                "Google has revoked this sign-in, so it can't be refreshed. This "
                "happens when the client secret is regenerated, when access is "
                "removed at myaccount.google.com/permissions, or after 7 days if "
                "your OAuth consent screen is still in Testing mode. Press "
                "Reconnect with Google to sign in again — the client ID and "
                "secret are fine.",
                exc.status, code=exc.code,
            ) from exc
        if exc.code in ("invalid_client", "unauthorized_client"):
            raise ApiError(
                "Google doesn't recognise this client ID and secret pair. Check "
                "them against Google Cloud → Credentials; if you regenerated the "
                "secret, paste the new one and reconnect.",
                exc.status, code=exc.code,
            ) from exc
        if exc.status in (400, 401):
            raise ApiError(
                f"Google rejected the saved sign-in ({exc}). Reconnect from the "
                "Connections panel.",
                exc.status, code=exc.code,
            ) from exc
        raise

    token = payload.get("access_token")
    if not token:
        raise ApiError("Google returned no access token")

    _remember(refresh, token, payload.get("expires_in"))
    return token


def forget(refresh_token):
    with _lock:
        _access.pop(refresh_token, None)


def api(cfg, url, params=None, timeout=20):
    """Authenticated GET against a Google API.

    doseq matters: Gmail's metadataHeaders is a repeated parameter, and without
    it a list is encoded as its Python repr and silently ignored.
    """
    full = f"{url}?{urlencode(params, doseq=True)}" if params else url
    return request_json(
        full,
        headers={"Authorization": f"Bearer {access_token(cfg)}"},
        timeout=timeout,
    )


def account_email(cfg):
    try:
        return (api(cfg, USERINFO_URL) or {}).get("email")
    except ApiError:
        return None


def revoke(cfg):
    """Best-effort revocation; a failure here shouldn't block disconnecting."""
    refresh = cfg.get("refresh_token")
    if not refresh:
        return
    try:
        request_form(REVOKE_URL, {"token": refresh})
    except ApiError:
        pass
    finally:
        forget(refresh)
