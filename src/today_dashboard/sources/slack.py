"""Unread counts for the Slack channels you've marked as important.

Unread state belongs to a *user*, so this wants a user token (`xoxp-`).
`conversations.info` then reports `last_read`, and counting history after that
mark gives both a message count and — more useful — how many mention you.

With a bot token there is no personal read state, so the module falls back to
"messages in the last 24 hours" and says so rather than reporting zero unread.
"""

import threading
import time
from urllib.parse import urlencode

from .http_json import ApiError, request_form, request_json

API = "https://slack.com/api/{}"

# Apps with token rotation enabled issue a short-lived access token prefixed
# "xoxe.xoxp-" (user) or "xoxe.xoxb-" (bot), valid for 12 hours, plus a
# single-use refresh token prefixed "xoxe-".
ROTATING_PREFIXES = ("xoxe.xoxp-", "xoxe.xoxb-")
STATIC_PREFIXES = ("xoxp-", "xoxb-")
REFRESH_PREFIX = "xoxe-"

# Refresh this far before actual expiry, so a fetch can't race the deadline.
REFRESH_MARGIN_SECONDS = 300

# Refresh tokens are revoked once used, so two concurrent refreshes would
# leave one of them holding a dead token. Serialise them.
_refresh_lock = threading.Lock()


def is_rotating(token):
    return bool(token) and token.startswith(ROTATING_PREFIXES)


def token_kind(token):
    """'user' | 'bot' | None — works for rotating and static tokens alike."""
    if not token:
        return None
    stripped = token[len("xoxe."):] if token.startswith("xoxe.") else token
    if stripped.startswith("xoxp-"):
        return "user"
    if stripped.startswith("xoxb-"):
        return "bot"
    return None


def rotation_ready(cfg):
    """Everything needed to renew a rotating token without user involvement."""
    return all((
        cfg.get("refresh_token"),
        cfg.get("client_id"),
        cfg.get("client_secret"),
    ))

# Slack returns 200 with ok:false, so errors need translating by code.
ERRORS = {
    "invalid_auth": "Slack rejected this token",
    "not_authed": "No token was sent",
    "token_revoked": "This token has been revoked",
    "token_expired": (
        "This token has expired. Rotating tokens last 12 hours — add the refresh "
        "token, client ID and client secret so it can renew itself."
    ),
    "account_inactive": "That Slack account is deactivated",
    "no_permission": "This token lacks permission for that call",
    "ratelimited": "Slack is rate-limiting; try again shortly",
}

# Channel-wide mentions count as mentioning you.
BROADCASTS = ("<!here", "<!channel", "<!everyone")

# Joins and leaves aren't messages anyone needs to read.
NOISE_SUBTYPES = {
    "channel_join", "channel_leave", "channel_topic", "channel_purpose",
    "channel_name", "channel_archive", "channel_unarchive", "group_join",
    "group_leave", "bot_add", "bot_remove",
}

FALLBACK_WINDOW_SECONDS = 24 * 3600


def _extract_tokens(payload):
    """Pull the new credentials out of an oauth.v2.access reply.

    User-token apps return them nested under authed_user; bot-token apps at the
    top level. Check both rather than guessing which shape we'll get.
    """
    for node in (payload.get("authed_user") or {}, payload):
        token = node.get("access_token")
        if token:
            return (
                token,
                node.get("refresh_token") or payload.get("refresh_token"),
                node.get("expires_in") or payload.get("expires_in"),
            )
    return None, None, None


def refresh_token(cfg, on_rotate=None):
    """Swap the refresh token for a fresh access token.

    Returns the updated credential dict. `on_rotate` is called with it so the
    caller can persist immediately — losing a rotated refresh token means the
    connection can't be renewed again.
    """
    if not rotation_ready(cfg):
        raise ApiError(
            "This Slack token has expired and can't be renewed automatically. Add "
            "the refresh token, client ID and client secret, or paste a new token."
        )

    with _refresh_lock:
        payload = request_form(API.format("oauth.v2.access"), {
            "grant_type": "refresh_token",
            "refresh_token": cfg["refresh_token"],
            "client_id": cfg["client_id"],
            "client_secret": cfg["client_secret"],
        })

        if not payload.get("ok"):
            code = payload.get("error") or "unknown_error"
            if code in ("invalid_refresh_token", "invalid_grant_type"):
                raise ApiError(
                    "Slack rejected the refresh token — it may already have been "
                    "used. Reinstall the app and paste the new tokens."
                )
            raise ApiError(ERRORS.get(code, f"Slack refresh failed: {code}"))

        access, rotated, expires_in = _extract_tokens(payload)
        if not access:
            raise ApiError("Slack's refresh reply contained no access token")

        try:
            ttl = int(expires_in or 43_200)
        except (TypeError, ValueError):
            ttl = 43_200

        updated = {
            "token": access,
            # Slack returns a new refresh token each time; keep the old one only
            # if it somehow didn't.
            "refresh_token": rotated or cfg["refresh_token"],
            "expires_at": int(time.time()) + ttl,
        }

    if on_rotate:
        on_rotate(updated)
    return updated


def ensure_fresh(cfg, on_rotate=None):
    """Return a cfg whose access token is valid now, refreshing if it's due."""
    token = cfg.get("token") or ""
    if not is_rotating(token) or not rotation_ready(cfg):
        return cfg

    expires_at = cfg.get("expires_at") or 0
    try:
        expires_at = int(expires_at)
    except (TypeError, ValueError):
        expires_at = 0

    # Unknown expiry: leave it alone and let a 401 drive the refresh instead of
    # burning a single-use refresh token on a token that may still be good.
    if expires_at and time.time() < expires_at - REFRESH_MARGIN_SECONDS:
        return cfg

    if not expires_at:
        return cfg

    return {**cfg, **refresh_token(cfg, on_rotate)}


def _call(token, method, params=None, timeout=20):
    url = API.format(method)
    if params:
        url = f"{url}?{urlencode(params, doseq=True)}"

    try:
        payload = request_json(
            url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
        )
    except ApiError as exc:
        # Slack answers 500 with an empty body for a rotating token that is the
        # right shape but not valid — usually expired, since they last 12 hours.
        # Left as "HTTP 500" that tells the user nothing.
        if exc.status and exc.status >= 500:
            if is_rotating(token):
                raise ApiError(
                    "Slack rejected this token with a server error, which is what "
                    "it does for a rotating token that has expired or been "
                    "truncated. These last only 12 hours — copy a fresh one, and "
                    "fill in Token rotation so it renews itself.",
                    exc.status,
                ) from exc
            raise ApiError(
                f"Slack returned a server error ({exc.status}). Check the token was "
                "copied in full, then try again.",
                exc.status,
            ) from exc
        raise

    if not payload.get("ok"):
        code = payload.get("error") or "unknown_error"
        if code == "missing_scope":
            needed = payload.get("needed") or "the required scope"
            raise ApiError(f"Slack token is missing a scope: {needed}")
        raise ApiError(ERRORS.get(code, f"Slack error: {code}"))

    return payload


def identity(token):
    """Who and which workspace this token belongs to."""
    payload = _call(token, "auth.test")
    return {
        "user": payload.get("user"),
        "user_id": payload.get("user_id"),
        "team": payload.get("team"),
        "team_id": payload.get("team_id"),
        # Bot tokens report a bot_id and have no personal read state.
        "is_bot": bool(payload.get("bot_id")),
    }


def list_channels(token, limit=1000):
    """Channels this token's user is a member of — the pool to pick from."""
    channels = []
    cursor = None

    while len(channels) < limit:
        params = {
            "types": "public_channel,private_channel",
            "exclude_archived": "true",
            "limit": "200",
        }
        if cursor:
            params["cursor"] = cursor

        payload = _call(token, "users.conversations", params)
        for channel in payload.get("channels") or []:
            if channel.get("id"):
                channels.append({
                    "id": channel["id"],
                    "name": channel.get("name") or channel["id"],
                    "private": bool(channel.get("is_private")),
                    "members": channel.get("num_members"),
                })

        cursor = ((payload.get("response_metadata") or {}).get("next_cursor")) or ""
        if not cursor:
            break

    channels.sort(key=lambda c: (c["name"] or "").lower())
    return channels[:limit]


def _count_since(token, channel_id, oldest, me):
    """Messages after `oldest`, and how many mention me. (count, mentions, latest)"""
    payload = _call(token, "conversations.history", {
        "channel": channel_id,
        "oldest": oldest,
        "inclusive": "false",
        "limit": "100",
    })

    needle = f"<@{me}>" if me else None
    count = mentions = 0
    latest = None
    latest_from = None

    for message in payload.get("messages") or []:
        if message.get("subtype") in NOISE_SUBTYPES:
            continue
        if me and message.get("user") == me:
            continue  # your own messages aren't unread

        count += 1
        text = message.get("text") or ""
        if (needle and needle in text) or any(tag in text for tag in BROADCASTS):
            mentions += 1

        stamp = message.get("ts")
        if stamp and (latest is None or float(stamp) > float(latest)):
            latest = stamp
            latest_from = message.get("user")

    # has_more means we stopped counting before the true total.
    return count, mentions, latest, latest_from, bool(payload.get("has_more"))


def _channel_state(token, channel, me):
    channel_id = channel["id"]

    try:
        info = _call(token, "conversations.info", {"channel": channel_id})
    except ApiError as exc:
        return {**channel, "error": str(exc)}

    node = info.get("channel") or {}
    last_read = node.get("last_read")
    name = node.get("name") or channel.get("name") or channel_id

    # Slack's own tally, when a user token gives us one.
    reported = node.get("unread_count_display")
    if reported is None:
        reported = node.get("unread_count")

    mode = "unread"
    if last_read:
        oldest = last_read
    else:
        # Bot token, or a channel with no read mark: fall back to a time window.
        mode = "recent"
        oldest = f"{time.time() - FALLBACK_WINDOW_SECONDS:.6f}"

    try:
        count, mentions, latest, latest_from, more = _count_since(
            token, channel_id, oldest, me
        )
    except ApiError as exc:
        # Without history we can still show Slack's own number, if it gave one.
        if reported is None:
            return {**channel, "name": name, "error": str(exc)}
        return {
            **channel, "name": name, "count": int(reported), "mentions": 0,
            "mode": mode, "partial": False, "note": str(exc),
        }

    # Prefer Slack's count when it has one — it matches the badge in the app.
    if mode == "unread" and reported is not None:
        try:
            count = int(reported)
        except (TypeError, ValueError):
            pass

    return {
        **channel,
        "name": name,
        "count": count,
        "mentions": mentions,
        "mode": mode,
        "partial": more,
        "latest_ts": latest,
        "latest_from": latest_from,
        "url": f"https://slack.com/app_redirect?channel={channel_id}",
    }


def fetch(cfg, on_rotate=None):
    """Return {"items": [...]} for the selected channels. Raises ApiError."""
    selected = [c for c in (cfg.get("channels") or []) if c]

    if not selected:
        return {
            "items": [], "channels_selected": 0, "total": 0, "mentions": 0,
            "needs_selection": True,
        }

    cfg = ensure_fresh(cfg, on_rotate)
    token = cfg["token"]

    try:
        me = identity(token)
    except ApiError as exc:
        # Expired mid-flight, or expires_at was unknown: renew once and retry.
        if not (is_rotating(token) and rotation_ready(cfg)):
            raise
        cfg = {**cfg, **refresh_token(cfg, on_rotate)}
        token = cfg["token"]
        me = identity(token)
    known = {c["id"]: c for c in (cfg.get("known_channels") or []) if c.get("id")}

    items = []
    for channel_id in selected[:25]:
        base = known.get(channel_id) or {"id": channel_id, "name": channel_id}
        items.append(_channel_state(token, base, me["user_id"]))

    # Only surface channels with something in them; keep errors visible though.
    live = [item for item in items if item.get("error") or item.get("count")]

    live.sort(key=lambda item: ((item.get("name") or "").lower()))
    live.sort(key=lambda item: (-(item.get("mentions") or 0), -(item.get("count") or 0)))

    # The same scope error repeats per channel; collapse it to one message.
    distinct_errors = []
    for item in items:
        message = item.get("error")
        if message and message not in distinct_errors:
            distinct_errors.append(message)
    readable = len([i for i in items if not i.get("error")])

    return {
        "items": live,
        "errors": distinct_errors,
        # 0 unread and 0 readable mean very different things.
        "readable": readable,
        "channels_selected": len(selected),
        "total": sum(item.get("count") or 0 for item in items),
        "mentions": sum(item.get("mentions") or 0 for item in items),
        "quiet": len([i for i in items if not i.get("error") and not i.get("count")]),
        "mode": "recent" if me["is_bot"] else "unread",
        "is_bot": me["is_bot"],
        "workspace": me["team"],
        "rotating": is_rotating(token),
        # A rotating token with no way to renew will stop working within 12h.
        "expires_at": cfg.get("expires_at") or None,
        "can_renew": rotation_ready(cfg),
    }


def verify(cfg, on_rotate=None):
    """Prove the token works and list channels for the picker."""
    cfg = ensure_fresh(cfg, on_rotate)
    token = cfg["token"]

    try:
        me = identity(token)
    except ApiError:
        if not (is_rotating(token) and rotation_ready(cfg)):
            raise
        cfg = {**cfg, **refresh_token(cfg, on_rotate)}
        token = cfg["token"]
        me = identity(token)

    try:
        channels = list_channels(token)
    except ApiError as exc:
        channels = []
        channel_error = str(exc)
    else:
        channel_error = None

    notes = []
    if is_rotating(token) and not rotation_ready(cfg):
        notes.append((
            "warn",
            "This is a rotating token (xoxe.xoxp-), which Slack expires after 12 "
            "hours. Add the refresh token, client ID and client secret under "
            "Token rotation and it will renew itself — otherwise you'll have to "
            "paste a new token twice a day.",
        ))
    # Surface a missing-scope failure at verify time, not only in the lane.
    if cfg.get("channels"):
        probe = fetch(cfg, on_rotate)
        for message in probe.get("errors", []):
            hint = (
                " Add those scopes to your Slack app, reinstall it, and paste the "
                "new token." if "missing a scope" in message else ""
            )
            notes.append(("warn", f"{message}.{hint}"))

    if me["is_bot"]:
        notes.append((
            "warn",
            "This is a bot token, which has no personal read state — Slack can't "
            "tell it what you've read. Counts fall back to the last 24 hours. Use "
            "a user token (xoxp-) for true unread counts.",
        ))
    if channel_error:
        notes.append(("warn", f"Couldn't list channels — {channel_error}"))
    elif not channels:
        notes.append((
            "info",
            "This token isn't a member of any channels. Join the ones you care "
            "about in Slack, then test again.",
        ))

    found = probe if cfg.get("channels") else None

    return {
        "account": me["user"],
        "workspace": me["team"],
        "is_bot": me["is_bot"],
        "channels": channels,
        "count": (found or {}).get("total", 0),
        "rotating": is_rotating(token),
        "can_renew": rotation_ready(cfg),
        "kind": token_kind(token),
        # Hand back the possibly-rotated credential so the caller persists it.
        "credentials": {
            "token": cfg["token"],
            "refresh_token": cfg.get("refresh_token") or "",
            "expires_at": cfg.get("expires_at") or 0,
        },
        "notes": [{"level": level, "message": message} for level, message in notes],
    }
