"""Unread Gmail that actually wants a human, with the mass mail filtered out.

`category:primary` is the lever: Gmail has already sorted promotions, social,
updates and forums into their own categories, so asking for primary excludes
bulk mail without maintaining a sender blocklist. Extra exclusions are layered
on for the senders that still slip through.
"""

from . import google_auth
from .http_json import ApiError

LIST_URL = "https://www.googleapis.com/gmail/v1/users/me/messages"
MESSAGE_URL = "https://www.googleapis.com/gmail/v1/users/me/messages/{}"

# Gmail's own categorisation does the heavy lifting; the rest catches
# transactional senders that land in Primary but never expect a reply.
DEFAULT_QUERY = (
    "is:unread in:inbox category:primary "
    "-from:noreply -from:no-reply -from:donotreply -from:notifications"
)

# How many to describe in the lane. The count itself covers more.
PREVIEW_COUNT = 6


def _header(message, name):
    for entry in ((message.get("payload") or {}).get("headers") or []):
        if (entry.get("name") or "").lower() == name.lower():
            return entry.get("value")
    return None


def _sender(raw):
    """'Priya Nair <priya@acme.com>' → ('Priya Nair', 'priya@acme.com')"""
    if not raw:
        return None, None
    value = raw.strip()
    if "<" in value and ">" in value:
        name = value.split("<", 1)[0].strip().strip('"')
        email = value.split("<", 1)[1].split(">", 1)[0].strip()
        return (name or email), email
    return value, value


def _list_unread(cfg, query, limit):
    """Message ids matching the query, plus whether more exist beyond `limit`."""
    payload = google_auth.api(cfg, LIST_URL, {
        "q": query,
        "maxResults": str(limit),
        "includeSpamTrash": "false",
    })
    ids = [m["id"] for m in (payload.get("messages") or []) if m.get("id")]
    return ids, bool(payload.get("nextPageToken"))


def _describe(cfg, message_id):
    """Metadata only — subject and sender. Never touches message bodies."""
    message = google_auth.api(cfg, MESSAGE_URL.format(message_id), {
        "format": "metadata",
        "metadataHeaders": ["From", "Subject", "Date", "List-Unsubscribe"],
    })

    name, email = _sender(_header(message, "From"))
    labels = message.get("labelIds") or []
    return {
        "id": message_id,
        "subject": _header(message, "Subject") or "(no subject)",
        "from_name": name,
        "from_email": email,
        "snippet": message.get("snippet") or "",
        "important": "IMPORTANT" in labels,
        "starred": "STARRED" in labels,
        # A List-Unsubscribe header means bulk mail that slipped past the filter.
        "bulk": bool(_header(message, "List-Unsubscribe")),
        "url": f"https://mail.google.com/mail/u/0/#inbox/{message_id}",
        "received_at": _header(message, "Date"),
    }


def fetch(cfg):
    """Return {"count": n, "items": [...]}. Raises ApiError."""
    query = (cfg.get("gmail_query") or DEFAULT_QUERY).strip() or DEFAULT_QUERY
    limit = max(1, min(int(cfg.get("gmail_max") or 50), 100))

    ids, more = _list_unread(cfg, query, limit)

    previews = []
    for message_id in ids[:PREVIEW_COUNT]:
        try:
            previews.append(_describe(cfg, message_id))
        except ApiError:
            continue  # a message deleted between listing and reading

    # Anything still carrying List-Unsubscribe is bulk that got through.
    bulk_seen = sum(1 for item in previews if item["bulk"])

    return {
        "count": len(ids),
        "count_is_partial": more,
        "items": previews,
        "shown": len(previews),
        "bulk_in_preview": bulk_seen,
        "query": query,
        "inbox_url": "https://mail.google.com/mail/u/0/#inbox",
    }
