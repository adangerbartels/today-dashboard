#!/usr/bin/env python3
"""ToDo dashboard: local todos + in-progress Jira + GitHub PRs needing attention.

Standard library only.

    python3 server.py            # http://127.0.0.1:8787
    python3 server.py --port 9000
"""

import argparse
import json
import mimetypes
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import config
from . import fixtures
from . import store
from .sources import catercow, gcal, github, gmail, google_auth, jira, slack
from .sources.http_json import ApiError

STATIC_DIR = Path(__file__).resolve().parent / "static"
MAX_BODY_BYTES = 64 * 1024

CFG = config.load()
CACHE = store.TTLCache(CFG["server"]["cache_ttl_seconds"])
TODO_ID_RE = re.compile(r"^[0-9a-f]{6,32}$")


def _now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --- feed assembly --------------------------------------------------------


def _load_jira():
    if not config.jira_configured(CFG):
        return {**fixtures.jira(), "configured": False}
    try:
        return {**jira.fetch(CFG["jira"]), "configured": True}
    except ApiError as exc:
        return {"items": [], "configured": True, "error": str(exc)}


def _load_github(seen):
    if not config.github_configured(CFG):
        return {**fixtures.github(seen), "configured": False}
    try:
        return {**github.fetch(CFG["github"], seen), "configured": True}
    except ApiError as exc:
        return {"items": [], "configured": True, "error": str(exc)}


def _load_gcal():
    if not config.google_configured(CFG):
        return {**fixtures.gcal(), "configured": False}
    try:
        result = gcal.fetch(CFG["google"])
    except ApiError as exc:
        return {"items": [], "configured": True, "error": str(exc)}

    # Every calendar failing isn't a partial result, it's an outage — promote it
    # so the lane shows an error instead of an innocent "nothing left today".
    if result.get("errors") and not result["items"]:
        return {**result, "configured": True, "error": result["errors"][0]}
    return {**result, "configured": True}


def _load_gmail():
    if not config.google_configured(CFG):
        return {**fixtures.gmail(), "configured": False}
    try:
        return {**gmail.fetch(CFG["google"]), "configured": True}
    except ApiError as exc:
        return {"items": [], "count": 0, "configured": True, "error": str(exc)}


def _load_catercow():
    if not config.catercow_configured(CFG):
        return {**fixtures.catercow(), "configured": False}
    try:
        return {
            **catercow.fetch(CFG["catercow"], CFG["google"]),
            "configured": True,
        }
    except ApiError as exc:
        return {"items": [], "pending": 0, "configured": True, "error": str(exc)}


def _load_slack():
    if not config.slack_configured(CFG):
        return {**fixtures.slack(), "configured": False}
    try:
        return {
            **slack.fetch(CFG["slack"], persist_slack_rotation),
            "configured": True,
        }
    except ApiError as exc:
        return {"items": [], "total": 0, "configured": True, "error": str(exc)}


# --- settings (in-app setup wizard) ---------------------------------------

# Editable fields per source. Secrets are write-only: they go in, never come back.
SOURCE_FIELDS = {
    "jira": ["base_url", "email", "api_token", "jql"],
    "github": ["token", "extra_query", "orgs"],
    # Google's refresh_token is set by the OAuth callback, never submitted here.
    "google": ["client_id", "client_secret", "gmail_query", "calendar_ids"],
    "slack": ["token", "channels", "refresh_token", "client_id", "client_secret"],
    "catercow": [
        "cookie", "base_url", "orders_path", "selected_pattern",
        "email_query", "lunch_days", "horizon_days",
    ],
}

# Fields carrying a list of strings rather than a scalar.
LIST_FIELDS = {("github", "orgs"), ("google", "calendar_ids"), ("slack", "channels")}

# Lists of integers (weekday numbers), and bounded scalar integers.
INT_LIST_FIELDS = {("catercow", "lunch_days")}
INT_FIELDS = {("catercow", "horizon_days"): (1, 60)}


def _as_int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def persist_slack_rotation(updated):
    """Called the moment Slack hands us a new token pair.

    Refresh tokens are single-use, so this writes through immediately rather
    than waiting for the request to finish — a crash in between would otherwise
    leave the stored refresh token already spent and unusable.
    """
    try:
        config.save_section("slack", updated)
        reload_config()
    except OSError:
        pass  # in-memory cfg still carries the new token for this request


def reload_config():
    global CFG
    CFG = config.load()
    CACHE.clear()
    return CFG


def settings_state():
    """Everything the wizard needs, with no secret values in it."""
    jira, github_cfg = CFG["jira"], CFG["github"]
    google, slack_cfg = CFG["google"], CFG["slack"]
    return {
        "config_path": str(config.CONFIG_PATH),
        "google": {
            "configured": config.google_configured(CFG),
            # The client is set up but consent hasn't been granted yet.
            "client_ready": bool(google["client_id"] and google["client_secret"]),
            "client_id": google["client_id"],
            "secret_hint": config.mask(google["client_secret"]),
            "connected": bool(google["refresh_token"]),
            "account": google.get("account") or "",
            "gmail_query": google["gmail_query"],
            "gmail_query_default": gmail.DEFAULT_QUERY,
            "calendar_ids": list(google.get("calendar_ids") or []),
            "known_calendars": list(google.get("known_calendars") or []),
            "redirect_uri": google_auth.redirect_uri(
                CFG["server"]["host"], CFG["server"]["port"]
            ),
            "env_overrides": config.env_overrides("google"),
        },
        "catercow": {
            "configured": config.catercow_configured(CFG),
            "cookie_hint": config.mask(CFG["catercow"].get("cookie")),
            "base_url": CFG["catercow"].get("base_url") or "",
            "orders_path": CFG["catercow"].get("orders_path") or "",
            "selected_pattern": CFG["catercow"].get("selected_pattern") or "",
            "email_query": CFG["catercow"].get("email_query") or "",
            "email_query_default": catercow.DEFAULT_EMAIL_QUERY,
            "use_email": bool(CFG["catercow"].get("use_email", True)),
            "email_available": config.google_configured(CFG),
            "lunch_days": list(CFG["catercow"].get("lunch_days") or []),
            "horizon_days": CFG["catercow"].get("horizon_days") or 14,
            "env_overrides": config.env_overrides("catercow"),
        },
        "slack": {
            "configured": config.slack_configured(CFG),
            "token_hint": config.mask(slack_cfg["token"]),
            "refresh_hint": config.mask(slack_cfg.get("refresh_token")),
            "client_secret_hint": config.mask(slack_cfg.get("client_secret")),
            "client_id": slack_cfg.get("client_id") or "",
            "rotating": slack.is_rotating(slack_cfg.get("token")),
            "can_renew": slack.rotation_ready(slack_cfg),
            "expires_at": slack_cfg.get("expires_at") or 0,
            "channels": list(slack_cfg.get("channels") or []),
            "known_channels": list(slack_cfg.get("known_channels") or []),
            "account": slack_cfg.get("account") or "",
            "workspace": slack_cfg.get("workspace") or "",
            "env_overrides": config.env_overrides("slack"),
        },
        "jira": {
            "configured": config.jira_configured(CFG),
            "base_url": jira["base_url"],
            "email": jira["email"],
            "jql": jira["jql"],
            "token_hint": config.mask(jira["api_token"]),
            "env_overrides": config.env_overrides("jira"),
        },
        "github": {
            "configured": config.github_configured(CFG),
            "extra_query": github_cfg["extra_query"],
            "orgs": list(github_cfg.get("orgs") or []),
            "known_owners": list(github_cfg.get("known_owners") or []),
            "account": github_cfg.get("account") or "",
            "token_hint": config.mask(github_cfg["token"]),
            "env_overrides": config.env_overrides("github"),
        },
    }


def _clean(value):
    return value.strip() if isinstance(value, str) else ""


def resolve_values(source, submitted):
    """Overlay submitted fields onto the live config.

    A blank secret means "keep what's stored", so the user can edit the JQL
    without re-pasting a token they can no longer read.
    """
    if source not in SOURCE_FIELDS:
        raise ValueError(f"Unknown source: {source}")

    resolved = dict(CFG[source])
    for field in SOURCE_FIELDS[source]:
        if field not in submitted:
            continue

        if (source, field) in INT_LIST_FIELDS:
            raw = submitted[field]
            if isinstance(raw, list):
                resolved[field] = sorted({
                    n for n in (_as_int(entry) for entry in raw[:20]) if n is not None
                })
            continue

        if (source, field) in LIST_FIELDS:
            raw = submitted[field]
            if isinstance(raw, list):
                resolved[field] = [
                    _clean(entry) for entry in raw[:100] if _clean(entry)
                ]
            continue

        if (source, field) in INT_FIELDS:
            bounds = INT_FIELDS[(source, field)]
            value = _as_int(submitted[field])
            if value is not None:
                resolved[field] = max(bounds[0], min(value, bounds[1]))
            continue

        value = _clean(submitted[field])
        if not value and (source, field) in config.SECRET_KEYS:
            continue  # keep the stored secret
        resolved[field] = value

    if source == "jira":
        resolved["base_url"] = resolved["base_url"].rstrip("/")
        if not resolved["jql"]:
            resolved["jql"] = config.DEFAULT_JQL

    return resolved


def validate_values(source, values):
    """Cheap checks before we spend a network round-trip. Returns an error string."""
    if source == "jira":
        base_url = values["base_url"]
        if not base_url:
            return "Site URL is required"
        if not re.match(r"^https?://[^\s/]+", base_url):
            return "Site URL must start with https:// and include a hostname"
        if not values["api_token"]:
            return "API token is required"
    elif source == "google":
        if not values["client_id"]:
            return "Client ID is required"
        if not values["client_secret"]:
            return "Client secret is required"
        if not values["client_id"].endswith(".apps.googleusercontent.com"):
            return "That doesn't look like a Google client ID — it should end in .apps.googleusercontent.com"
        if not values.get("refresh_token"):
            return "Save the client, then press Connect to sign in with Google"
    elif source == "slack":
        token = values["token"]
        if not token:
            return "Token is required"
        # xoxe.xoxp- / xoxe.xoxb- are rotating tokens; xoxe- alone is a *refresh*
        # token, which is not usable as a bearer credential.
        if token.startswith(slack.REFRESH_PREFIX) and not slack.is_rotating(token):
            return (
                "That's a refresh token (xoxe-…), not an access token. The access "
                "token starts with xoxe.xoxp- — put the refresh token in the Token "
                "rotation section instead."
            )
        if not slack.token_kind(token):
            return (
                "Slack access tokens start with xoxp- or xoxb- (or xoxe.xoxp- / "
                "xoxe.xoxb- when token rotation is on)"
            )
        refresh = values.get("refresh_token") or ""
        if refresh and not refresh.startswith(slack.REFRESH_PREFIX):
            return "A Slack refresh token starts with xoxe-"
    elif source == "catercow":
        if not values["lunch_days"]:
            return "Pick at least one weekday that expects a lunch selection"
        base = values["base_url"]
        if base and not re.match(r"^https?://[^\s/]+", base):
            return "Site URL must start with https:// and include a hostname"
        pattern = values.get("selected_pattern") or ""
        if pattern:
            try:
                re.compile(pattern)
            except re.error as exc:
                return f"selected_pattern isn't a valid regex: {exc}"
        # Neither extractor available means nothing to check against.
        if not values["cookie"] and not (
            values.get("use_email", True) and config.google_configured(CFG)
        ):
            return (
                "Add a session cookie, or connect Google so confirmation emails "
                "can be read"
            )
    else:
        if not values["token"]:
            return "Token is required"
    return None


def verify_source(source, values):
    """Live credential check. Returns (result, error_message)."""
    try:
        if source == "jira":
            return jira.verify(values), None
        if source == "google":
            return verify_google(values), None
        if source == "slack":
            return slack.verify(values, persist_slack_rotation), None
        if source == "catercow":
            return verify_catercow(values), None
        return github.verify(values, store.get_seen()), None
    except ApiError as exc:
        return None, str(exc)
    except Exception as exc:  # noqa: BLE001 - a bad response shouldn't 500 the wizard
        return None, f"Unexpected error: {exc}"


def verify_google(values):
    """One check covering both Google lanes, since they share the connection."""
    # Fail loudly if the grant itself is dead. Everything below swallows errors
    # into notes, which would otherwise let a revoked sign-in report "Connected".
    google_auth.access_token(values)

    account = google_auth.account_email(values)

    calendars, notes = [], []
    try:
        calendars = gcal.list_calendars(values)
    except ApiError as exc:
        notes.append(("warn", f"Couldn't list calendars — {exc}"))

    events = mail_count = 0
    try:
        events = len(gcal.fetch(values)["items"])
    except ApiError as exc:
        notes.append(("warn", f"Calendar unavailable — {exc}"))
    try:
        mail_count = gmail.fetch(values)["count"]
    except ApiError as exc:
        notes.append(("warn", f"Gmail unavailable — {exc}"))

    return {
        "account": account,
        "calendars": calendars,
        "count": events,
        "mail_count": mail_count,
        "notes": [{"level": level, "message": message} for level, message in notes],
    }


def verify_catercow(values):
    """Run the real extraction and report what each source contributed."""
    found = catercow.fetch(values, CFG["google"])

    notes = [{"level": "warn", "message": text} for text in found.get("warnings", [])]

    if "email" in found["sources"] and not found["selected"] and not found.get("scanned"):
        notes.append({
            "level": "info",
            "message": (
                "No CaterCow confirmation emails matched. Check the search under "
                "Advanced — the default expects subjects like “Your meal selection "
                "on Monday 8/3 is confirmed”."
            ),
        })

    if values.get("cookie") and "cookie" not in found["sources"]:
        notes.append({
            "level": "warn",
            "message": "The cookie didn't contribute any dates — see the note above.",
        })

    return {
        "account": None,
        "count": found["pending"],
        "sources": found["sources"],
        "selected": found["selected"],
        "scanned": found.get("scanned", 0),
        "notes": notes,
    }


def build_payload(force=False):
    if not force:
        cached = CACHE.get("feeds")
        if cached:
            # Todos are cheap and change locally, so never serve them stale.
            return {**cached, "todos": store.list_todos()}

    seen = store.get_seen()
    # Independent network calls; run them together rather than in series.
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {
            "jira": pool.submit(_load_jira),
            "github": pool.submit(_load_github, seen),
            "gcal": pool.submit(_load_gcal),
            "gmail": pool.submit(_load_gmail),
            "slack": pool.submit(_load_slack),
            "catercow": pool.submit(_load_catercow),
        }
        results = {name: future.result() for name, future in futures.items()}

    if results["github"].get("configured") and not results["github"].get("error"):
        store.prune_seen(results["github"].get("all_keys") or [])

    feeds = {
        **results,
        "fetched_at": _now_iso(),
        "demo": not all((
            config.jira_configured(CFG),
            config.github_configured(CFG),
            config.google_configured(CFG),
            config.slack_configured(CFG),
            config.catercow_configured(CFG),
        )),
    }
    CACHE.set("feeds", feeds)
    return {**feeds, "todos": store.list_todos()}


# --- HTTP ----------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    server_version = "todo-dashboard"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        if "--verbose" in sys.argv:
            super().log_message(fmt, *args)

    # -- helpers

    def _send(self, status, body=b"", content_type="application/json", extra=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        # Local-only tool, but there's no reason to be embeddable or sniffable.
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status, payload):
        self._send(status, json.dumps(payload).encode("utf-8"))

    def _error(self, status, message):
        self._json(status, {"error": message})

    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise ValueError("Invalid Content-Length")
        if length <= 0:
            return {}
        if length > MAX_BODY_BYTES:
            raise ValueError("Request body too large")
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON: {exc.msg}")
        if not isinstance(payload, dict):
            raise ValueError("Expected a JSON object")
        return payload

    # -- static

    def _serve_static(self, path):
        name = "index.html" if path in ("/", "") else path.lstrip("/")
        target = (STATIC_DIR / name).resolve()

        # Refuse anything that escapes static/ via .. or a symlink.
        if not target.is_file() or STATIC_DIR.resolve() not in target.parents:
            return self._error(404, "Not found")

        content_type, _ = mimetypes.guess_type(target.name)
        if target.suffix in (".html", ".js", ".css", ".svg"):
            content_type = {
                ".html": "text/html; charset=utf-8",
                ".js": "text/javascript; charset=utf-8",
                ".css": "text/css; charset=utf-8",
                ".svg": "image/svg+xml",
            }[target.suffix]
        self._send(200, target.read_bytes(), content_type or "application/octet-stream")

    # -- routes

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
        path = urlparse(self.path).path

        if path == "/api/items":
            force = "refresh=1" in (urlparse(self.path).query or "")
            try:
                return self._json(200, build_payload(force=force))
            except Exception as exc:  # noqa: BLE001 - never take the server down
                return self._error(500, f"Unexpected error: {exc}")

        if path == "/api/config":
            return self._json(
                200,
                {
                    "jira_configured": config.jira_configured(CFG),
                    "github_configured": config.github_configured(CFG),
                    "jira_base_url": CFG["jira"]["base_url"],
                    "rules": CFG["github"]["rules"],
                },
            )

        if path == "/api/settings":
            return self._json(200, settings_state())

        if path == google_auth.CALLBACK_PATH:
            return self._google_callback(urlparse(self.path).query)

        if path.startswith("/api/"):
            return self._error(404, "Unknown endpoint")

        return self._serve_static(path)

    do_HEAD = do_GET

    def do_POST(self):  # noqa: N802
        path = urlparse(self.path).path
        try:
            body = self._read_json()
        except ValueError as exc:
            return self._error(400, str(exc))

        if path == "/api/todos":
            try:
                todo = store.add_todo(
                    body.get("title"), body.get("link"), body.get("origin")
                )
            except ValueError as exc:
                return self._error(400, str(exc))
            return self._json(201, todo)

        if path == "/api/todos/clear-completed":
            return self._json(200, {"removed": store.clear_completed()})

        if path == "/api/seen":
            keys = body.get("keys")
            if not isinstance(keys, list) or not all(isinstance(k, str) for k in keys):
                return self._error(400, "keys must be a list of strings")
            stamp = store.mark_seen(keys[:500])
            CACHE.clear()  # reasons depend on the baseline we just moved
            return self._json(200, {"seen_at": stamp})

        if path in ("/api/settings/test", "/api/settings/save"):
            return self._handle_settings_write(path.rsplit("/", 1)[1], body)

        if path == "/api/settings/disconnect":
            source = body.get("source")
            if source not in SOURCE_FIELDS:
                return self._error(400, f"source must be one of {list(SOURCE_FIELDS)}")

            # Discovery caches belonged to that credential, so drop them too.
            cleared = {
                "jira": {"api_token": ""},
                "github": {"token": "", "known_owners": [], "account": ""},
                "google": {"refresh_token": "", "account": "", "known_calendars": []},
                "slack": {"token": "", "known_channels": [], "account": "", "workspace": ""},
                "catercow": {"cookie": ""},
            }[source]

            if source == "google":
                google_auth.revoke(CFG["google"])

            config.save_section(source, cleared)
            reload_config()
            return self._json(200, {"ok": True, "settings": settings_state()})

        if path == "/api/google/connect":
            return self._google_connect(body)

        if path == "/api/catercow/probe":
            # Structural report on a cookie fetch, for tuning selected_pattern
            # without anyone having to paste a whole authenticated page.
            submitted = body.get("values")
            if not isinstance(submitted, dict):
                return self._error(400, "values must be an object")
            values = resolve_values("catercow", submitted)
            if not values["cookie"]:
                return self._json(200, {"ok": False, "error": "No cookie to probe with"})
            try:
                return self._json(200, {"ok": True, "report": catercow.probe(values)})
            except ApiError as exc:
                return self._json(200, {"ok": False, "error": str(exc)})

        return self._error(404, "Unknown endpoint")

    # -- Google OAuth

    def _google_connect(self, body):
        """Save the OAuth client, then hand back a consent URL to open."""
        submitted = body.get("values")
        if not isinstance(submitted, dict):
            return self._error(400, "values must be an object")

        values = resolve_values("google", submitted)
        client_id, secret = values["client_id"], values["client_secret"]
        if not client_id or not secret:
            return self._json(200, {
                "ok": False,
                "error": "Client ID and secret are both required before signing in",
            })

        # Persist the client first: the callback arrives as a separate request
        # and needs the secret to exchange the code.
        try:
            config.save_section("google", {
                "client_id": client_id,
                "client_secret": secret,
                "gmail_query": values["gmail_query"],
                "calendar_ids": values["calendar_ids"],
            })
        except OSError as exc:
            return self._error(500, f"Could not write {config.CONFIG_PATH}: {exc}")
        reload_config()

        uri = google_auth.redirect_uri(CFG["server"]["host"], CFG["server"]["port"])
        try:
            started = google_auth.begin(client_id, uri)
        except ApiError as exc:
            return self._json(200, {"ok": False, "error": str(exc)})

        return self._json(200, {
            "ok": True, "auth_url": started["url"], "redirect_uri": uri,
        })

    def _google_callback(self, query):
        """Where Google sends the user back after consent."""
        params = parse_qs(query or "")
        error = (params.get("error") or [None])[0]
        code = (params.get("code") or [None])[0]
        state = (params.get("state") or [None])[0]

        if error:
            detail = (
                "You declined the permission request."
                if error == "access_denied"
                else f"Google reported: {error}"
            )
            return self._oauth_page("Not connected", detail, ok=False)

        if not code or not state:
            return self._oauth_page(
                "Not connected", "Google's reply was missing the sign-in code.", ok=False
            )

        try:
            result = google_auth.complete(
                CFG["google"]["client_id"], CFG["google"]["client_secret"], state, code
            )
        except ApiError as exc:
            return self._oauth_page("Not connected", str(exc), ok=False)

        try:
            config.save_section("google", {"refresh_token": result["refresh_token"]})
        except OSError as exc:
            return self._oauth_page(
                "Not connected", f"Could not write {config.CONFIG_PATH}: {exc}", ok=False
            )
        reload_config()

        email = google_auth.account_email(CFG["google"]) or ""
        if email:
            config.save_section("google", {"account": email})
            reload_config()

        return self._oauth_page(
            "Google connected",
            f"Signed in as {email}. You can close this tab — the dashboard has "
            "already picked it up." if email
            else "You can close this tab — the dashboard has already picked it up.",
            ok=True,
        )

    def _oauth_page(self, heading, detail, ok):
        """Minimal self-contained page; the wizard is where the real UI lives."""
        colour = "#17683c" if ok else "#c62828"
        html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>{escape(heading)}</title>
<meta name="color-scheme" content="light dark">
<style>
  body {{ margin:0; min-height:100vh; display:grid; place-items:center;
    font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
    background:#f6f6f7; color:#17171a; }}
  .card {{ max-width:29rem; padding:28px 30px; background:#fff; border:1px solid #e5e5e8;
    border-radius:12px; box-shadow:0 1px 3px rgba(0,0,0,.06); }}
  h1 {{ margin:0 0 8px; font-size:17px; color:{colour}; }}
  p {{ margin:0; color:#55555f; }}
  @media (prefers-color-scheme: dark) {{
    body {{ background:#0b0b0d; color:#f2f2f4; }}
    .card {{ background:#15151a; border-color:#26262d; }}
    p {{ color:#a4a4af; }}
  }}
</style></head>
<body><div class="card">
  <h1>{escape(heading)}</h1>
  <p>{escape(detail)}</p>
</div></body></html>"""
        return self._send(
            200 if ok else 400, html.encode("utf-8"), "text/html; charset=utf-8"
        )

    def _handle_settings_write(self, mode, body):
        """`test` verifies credentials; `save` verifies then persists them."""
        source = body.get("source")
        if source not in SOURCE_FIELDS:
            return self._error(400, "source must be 'jira' or 'github'")

        submitted = body.get("values")
        if not isinstance(submitted, dict):
            return self._error(400, "values must be an object")

        values = resolve_values(source, submitted)

        # A missing required field can't be overridden — there's nothing to store.
        problem = validate_values(source, values)
        if problem:
            return self._json(200, {"ok": False, "error": problem, "kind": "invalid"})

        # A failed live check can be: offline, or an unusual setup we can't probe.
        skip_check = mode == "save" and bool(body.get("save_anyway"))
        result, error = (None, None) if skip_check else verify_source(source, values)

        if mode == "test" or (error and not skip_check):
            return self._json(
                200,
                {
                    "ok": error is None,
                    "error": error,
                    "kind": "unverified" if error else None,
                    "result": result,
                },
            )

        persisted = {field: values[field] for field in SOURCE_FIELDS[source]}

        # Cache what discovery found, so each picker can still list the entries
        # you filtered out instead of forgetting they exist.
        if source == "github" and result and result.get("orgs") is not None:
            owners = [org["login"] for org in result["orgs"] if org.get("login")]
            account = result.get("account") or ""
            persisted["known_owners"] = ([account] if account else []) + owners
            persisted["account"] = account

        if source == "google" and result:
            if result.get("calendars") is not None:
                persisted["known_calendars"] = [
                    {"id": cal["id"], "name": cal["name"], "primary": cal["primary"]}
                    for cal in result["calendars"] if cal.get("id")
                ]
            persisted["account"] = result.get("account") or ""

        if source == "slack" and result:
            if result.get("channels") is not None:
                persisted["known_channels"] = [
                    {"id": ch["id"], "name": ch["name"], "private": ch["private"]}
                    for ch in result["channels"] if ch.get("id")
                ]
            persisted["account"] = result.get("account") or ""
            persisted["workspace"] = result.get("workspace") or ""
            # Verification may have rotated the token; store what's actually live.
            for key, value in (result.get("credentials") or {}).items():
                persisted[key] = value

        try:
            config.save_section(source, persisted)
        except OSError as exc:
            return self._error(500, f"Could not write {config.CONFIG_PATH}: {exc}")

        reload_config()

        shadowed = config.env_overrides(source)
        return self._json(
            200,
            {
                "ok": True,
                "result": result,
                "saved_to": str(config.CONFIG_PATH),
                "warning": (
                    f"Saved, but {', '.join(shadowed)} in your environment takes "
                    "precedence. Unset it for this to take effect."
                    if shadowed
                    else None
                ),
                "settings": settings_state(),
            },
        )

    def do_PATCH(self):  # noqa: N802
        path = urlparse(self.path).path
        match = re.fullmatch(r"/api/todos/([^/]+)", path)
        if not match:
            return self._error(404, "Unknown endpoint")
        todo_id = match.group(1)
        if not TODO_ID_RE.match(todo_id):
            return self._error(400, "Invalid id")

        try:
            body = self._read_json()
        except ValueError as exc:
            return self._error(400, str(exc))

        todo = store.update_todo(todo_id, body)
        if not todo:
            return self._error(404, "No such todo")
        return self._json(200, todo)

    def do_DELETE(self):  # noqa: N802
        path = urlparse(self.path).path
        match = re.fullmatch(r"/api/todos/([^/]+)", path)
        if not match:
            return self._error(404, "Unknown endpoint")
        todo_id = match.group(1)
        if not TODO_ID_RE.match(todo_id):
            return self._error(400, "Invalid id")

        if not store.delete_todo(todo_id):
            return self._error(404, "No such todo")
        return self._send(204)


SOURCE_STATUS = (
    ("Jira", config.jira_configured),
    ("GitHub", config.github_configured),
    ("Google", config.google_configured),
    ("Slack", config.slack_configured),
    ("CaterCow", config.catercow_configured),
)


def serve(host=None, port=None, verbose=False):
    """Run the dashboard until interrupted."""
    host = host or CFG["server"]["host"]
    port = int(port or CFG["server"]["port"])
    if verbose:
        sys.argv.append("--verbose")  # Handler.log_message checks argv

    config.DATA_DIR.mkdir(parents=True, exist_ok=True)

    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True

    live = [name for name, check in SOURCE_STATUS if check(CFG)]
    demo = [name for name, check in SOURCE_STATUS if not check(CFG)]

    lines = [f"  Today  →  http://{host}:{port}", f"  config    {config.CONFIG_PATH}"]
    if live:
        lines.append(f"  live      {', '.join(live)}")
    if demo:
        lines.append(f"  demo data {', '.join(demo)}  (press s in the browser to set up)")
    print("\n" + "\n".join(lines) + "\n", flush=True)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.", flush=True)
    finally:
        httpd.server_close()


def main(argv=None):
    parser = argparse.ArgumentParser(prog="today serve", description=serve.__doc__)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--verbose", action="store_true", help="log every request")
    args = parser.parse_args(argv)
    serve(args.host, args.port, args.verbose)


if __name__ == "__main__":
    main()
