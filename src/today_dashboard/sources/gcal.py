"""Today's remaining Google Calendar events.

`timeMin=now` is what filters out past events: Google matches events whose *end*
is at or after timeMin, so anything finished drops out while a meeting you're
currently in stays.
"""

from datetime import datetime
from urllib.parse import quote

from . import google_auth
from .http_json import ApiError

EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/{}/events"
CALENDARS_URL = "https://www.googleapis.com/calendar/v3/users/me/calendarList"


def _now_local():
    return datetime.now().astimezone()


def _end_of_today(now):
    return now.replace(hour=23, minute=59, second=59, microsecond=0)


def _parse(node, fallback_tz):
    """A Google start/end block → (aware datetime, is_all_day)."""
    if not isinstance(node, dict):
        return None, False

    if node.get("dateTime"):
        raw = node["dateTime"]
        # Google sends UTC as a trailing "Z", which fromisoformat only accepts
        # from 3.11. Normalising keeps this working on older interpreters.
        if raw.endswith(("Z", "z")):
            raw = raw[:-1] + "+00:00"
        try:
            return datetime.fromisoformat(raw), False
        except ValueError:
            return None, False

    if node.get("date"):
        try:
            day = datetime.fromisoformat(node["date"])
            return day.replace(tzinfo=fallback_tz), True
        except ValueError:
            return None, True

    return None, False


def _video_link(event):
    if event.get("hangoutLink"):
        return event["hangoutLink"]
    for entry in ((event.get("conferenceData") or {}).get("entryPoints") or []):
        if entry.get("entryPointType") == "video" and entry.get("uri"):
            return entry["uri"]
    return None


def _self_attendee(event):
    for person in (event.get("attendees") or []):
        if person.get("self"):
            return person
    return None


def _normalize(event, calendar, now):
    tz = now.tzinfo
    start, all_day = _parse(event.get("start"), tz)
    end, _ = _parse(event.get("end"), tz)
    if not start:
        return None

    me = _self_attendee(event)
    attendees = [p for p in (event.get("attendees") or []) if not p.get("resource")]

    # An all-day event technically spans "now", but it isn't a meeting you're
    # sitting in — flagging it live would badge a company holiday as "Now".
    in_progress = bool(end and start <= now < end) and not all_day
    minutes_until = None if in_progress else max(0, round((start - now).total_seconds() / 60))

    return {
        "id": event.get("id"),
        "title": event.get("summary") or "(no title)",
        "url": event.get("htmlLink"),
        "start_at": start.isoformat(timespec="minutes"),
        "end_at": end.isoformat(timespec="minutes") if end else None,
        "all_day": all_day,
        "in_progress": in_progress,
        "minutes_until": minutes_until,
        "duration_minutes": round((end - start).total_seconds() / 60) if end else None,
        "location": event.get("location"),
        "video_url": _video_link(event),
        "tentative": event.get("status") == "tentative"
        or (me or {}).get("responseStatus") == "tentative",
        "needs_response": (me or {}).get("responseStatus") == "needsAction",
        "attendee_count": len(attendees),
        "organizer": ((event.get("organizer") or {}).get("displayName")
                      or (event.get("organizer") or {}).get("email")),
        "calendar": calendar,
        "_sort": start,
        "_end": end,
    }


def _fetch_calendar(cfg, calendar_id, now, horizon, limit):
    payload = google_auth.api(
        cfg,
        EVENTS_URL.format(quote(calendar_id, safe="")),
        {
            "timeMin": now.isoformat(timespec="seconds"),
            "timeMax": horizon.isoformat(timespec="seconds"),
            "singleEvents": "true",       # expand recurring into instances
            "orderBy": "startTime",
            "maxResults": str(limit),
        },
    )
    return payload.get("items") or []


def fetch(cfg):
    """Return {"items": [...]} for the rest of today. Raises ApiError."""
    now = _now_local()
    horizon = _end_of_today(now)
    limit = max(1, min(int(cfg.get("max_events") or 20), 100))
    calendar_ids = [c for c in (cfg.get("calendar_ids") or ["primary"]) if c]
    skip_declined = cfg.get("skip_declined", True)
    include_all_day = cfg.get("include_all_day", True)

    items = []
    errors = []
    for calendar_id in calendar_ids[:10]:
        try:
            raw = _fetch_calendar(cfg, calendar_id, now, horizon, limit)
        except ApiError as exc:
            # One unreadable calendar shouldn't blank the whole lane.
            errors.append(f"{calendar_id}: {exc}")
            continue

        for event in raw:
            if event.get("status") == "cancelled":
                continue

            me = _self_attendee(event)
            if skip_declined and (me or {}).get("responseStatus") == "declined":
                continue

            item = _normalize(event, calendar_id, now)
            if not item:
                continue
            if item["all_day"] and not include_all_day:
                continue
            # timeMin should already exclude finished events; enforce it here too
            # so clock skew or a cached response can't leak the past back in.
            if item["_end"] and item["_end"] < now:
                continue
            items.append(item)

    items.sort(key=lambda item: item["_sort"])
    for item in items:
        del item["_sort"]
        del item["_end"]

    # Stable, so within each group the chronological order above survives:
    # what you're in now, then all-day context, then what's coming up.
    items.sort(key=lambda item: (not item["in_progress"], not item["all_day"]))

    return {
        "items": items[:limit],
        "as_of": now.isoformat(timespec="minutes"),
        "day_ends": horizon.isoformat(timespec="minutes"),
        "errors": errors,
    }


def list_calendars(cfg):
    """For the wizard's calendar picker."""
    payload = google_auth.api(cfg, CALENDARS_URL, {"maxResults": "250"})
    calendars = [
        {
            "id": item.get("id"),
            "name": item.get("summaryOverride") or item.get("summary") or item.get("id"),
            "primary": bool(item.get("primary")),
            "selected": bool(item.get("selected", True)),
        }
        for item in (payload.get("items") or [])
        if item.get("id") and item.get("accessRole") in ("owner", "writer", "reader", "freeBusyReader")
    ]
    calendars.sort(key=lambda cal: (not cal["primary"], (cal["name"] or "").lower()))
    return calendars
