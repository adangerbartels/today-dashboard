"""Demo data, used for any source that isn't configured yet.

Timestamps are generated relative to now so the "3h ago" labels stay sensible.
"""

from datetime import datetime, timedelta, timezone

from .sources.github import REASON_LABEL, REASON_WEIGHT


def _ago(**delta):
    stamp = datetime.now(timezone.utc) - timedelta(**delta)
    return stamp.isoformat(timespec="seconds").replace("+00:00", "Z")


def _soon(**delta):
    """A local-time ISO stamp `delta` from now, for demo calendar events."""
    return (datetime.now().astimezone() + timedelta(**delta)).replace(
        second=0, microsecond=0
    )


JIRA_BASE = "https://demo.atlassian.net"

_JIRA_ROWS = [
    ("PLAT-1482", "Streaming token budget leaks on retry", "In Progress", "High", "Bug", {"hours": 2}, None),
    ("PLAT-1461", "Split scheduler config out of the monolith", "In Review", "Medium", "Story", {"hours": 9}, "2026-07-30"),
    ("PLAT-1455", "Backfill audit events for June", "In Progress", "Medium", "Task", {"days": 1, "hours": 4}, None),
    ("WEB-908", "Keyboard nav on the settings drawer", "In Progress", "Low", "Task", {"days": 2}, "2026-08-04"),
    ("PLAT-1440", "Flaky integration suite on ARM runners", "Blocked", "High", "Bug", {"days": 3, "hours": 6}, None),
]


def jira():
    items = []
    for key, summary, status, priority, kind, ago, due in _JIRA_ROWS:
        items.append(
            {
                "id": key,
                "key": key,
                "title": summary,
                "url": f"{JIRA_BASE}/browse/{key}",
                "status": status,
                "status_category": "indeterminate",
                "priority": priority,
                "type": kind,
                "is_subtask": False,
                "project": key.split("-")[0],
                "parent": None,
                "updated_at": _ago(**ago),
                "created_at": _ago(days=21),
                "due_date": due,
            }
        )
    return {"items": items, "jql": "demo data", "demo": True}


_PR_ROWS = [
    {
        "repo": "acme/platform",
        "number": 4821,
        "title": "Add backpressure to the event fan-out worker",
        "author": "priya-n",
        "is_mine": False,
        "reasons": ["review-requested", "new-activity"],
        "checks": "SUCCESS",
        "review_decision": None,
        "mergeable": "MERGEABLE",
        "additions": 214,
        "deletions": 38,
        "changed_files": 9,
        "activity": {"minutes": 22},
        "activity_by": "priya-n",
        "labels": [{"name": "needs-review", "color": "0e8a16"}],
    },
    {
        "repo": "acme/platform",
        "number": 4809,
        "title": "Retry budget: cap exponential backoff at 30s",
        "author": "you",
        "is_mine": True,
        "reasons": ["ci-failing", "new-activity"],
        "checks": "FAILURE",
        "review_decision": "APPROVED",
        "mergeable": "MERGEABLE",
        "additions": 61,
        "deletions": 12,
        "changed_files": 3,
        "activity": {"hours": 1, "minutes": 10},
        "activity_by": "ci-bot",
        "labels": [],
    },
    {
        "repo": "acme/web",
        "number": 1177,
        "title": "Settings drawer: trap focus and restore on close",
        "author": "you",
        "is_mine": True,
        "reasons": ["changes-requested", "new-activity"],
        "checks": "SUCCESS",
        "review_decision": "CHANGES_REQUESTED",
        "mergeable": "MERGEABLE",
        "additions": 128,
        "deletions": 44,
        "changed_files": 6,
        "activity": {"hours": 4},
        "activity_by": "dmitri",
        "labels": [{"name": "a11y", "color": "5319e7"}],
    },
    {
        "repo": "acme/platform",
        "number": 4788,
        "title": "Drop the legacy /v1 scheduler shim",
        "author": "you",
        "is_mine": True,
        "reasons": ["ready-to-merge"],
        "checks": "SUCCESS",
        "review_decision": "APPROVED",
        "mergeable": "MERGEABLE",
        "additions": 18,
        "deletions": 402,
        "changed_files": 11,
        "activity": {"hours": 20},
        "activity_by": "sam-k",
        "labels": [],
    },
    {
        "repo": "acme/infra",
        "number": 331,
        "title": "Pin the ARM runner image to a known-good digest",
        "author": "sam-k",
        "is_mine": False,
        "reasons": ["review-requested"],
        "checks": "PENDING",
        "review_decision": None,
        "mergeable": "UNKNOWN",
        "additions": 7,
        "deletions": 4,
        "changed_files": 1,
        "activity": {"days": 1, "hours": 2},
        "activity_by": "sam-k",
        "labels": [{"name": "infra", "color": "1d76db"}],
    },
    {
        "repo": "acme/web",
        "number": 1162,
        "title": "Migrate the dashboard to the new query client",
        "author": "you",
        "is_mine": True,
        "reasons": ["conflicts", "new-activity"],
        "checks": "SUCCESS",
        "review_decision": None,
        "mergeable": "CONFLICTING",
        "additions": 640,
        "deletions": 511,
        "changed_files": 34,
        "activity": {"days": 2, "hours": 5},
        "activity_by": "priya-n",
        "labels": [],
    },
]


def github(seen=None):
    seen = seen or {}
    items = []
    for row in _PR_ROWS:
        key = f"{row['repo']}#{row['number']}"
        stamp = _ago(**row["activity"])

        reasons = list(row["reasons"])
        baseline = seen.get(key)
        # Demo timestamps are regenerated relative to now on every request, so a
        # real stamp comparison would drift; presence of a baseline is enough here.
        if baseline:
            reasons = [r for r in reasons if r != "new-activity"]
        if not reasons:
            continue

        reasons = sorted(reasons, key=lambda r: -REASON_WEIGHT.get(r, 0))
        items.append(
            {
                "id": key,
                "key": key,
                "repo": row["repo"],
                "number": row["number"],
                "title": row["title"],
                "url": f"https://github.com/{row['repo']}/pull/{row['number']}",
                "author": row["author"],
                "is_mine": row["is_mine"],
                "is_draft": False,
                "review_decision": row["review_decision"],
                "mergeable": row["mergeable"],
                "checks": row["checks"],
                "additions": row["additions"],
                "deletions": row["deletions"],
                "changed_files": row["changed_files"],
                "labels": row["labels"],
                "updated_at": stamp,
                "activity_at": stamp,
                "activity_by": row["activity_by"],
                "seen_at": baseline,
                "reasons": reasons,
                "reason_labels": [REASON_LABEL.get(r, r) for r in reasons],
                "weight": max(REASON_WEIGHT.get(r, 0) for r in reasons),
            }
        )

    items.sort(key=lambda item: item["activity_at"], reverse=True)
    items.sort(key=lambda item: -item["weight"])
    return {"items": items, "viewer": "you", "all_keys": [i["key"] for i in items], "demo": True}


# --- Google Calendar -------------------------------------------------------

# (title, starts in, runs for, location, video, attendees, needs_response)
_EVENT_ROWS = [
    ("Platform standup", {"minutes": -8}, 15, None, True, 9, False),
    ("1:1 with Priya", {"minutes": 52}, 30, None, True, 2, False),
    ("Retry-budget design review", {"hours": 2, "minutes": 10}, 60, "Focus Room 3", True, 6, True),
    ("Incident retro: ARM runners", {"hours": 4}, 45, None, True, 12, False),
]


def gcal():
    now = datetime.now().astimezone()
    items = []

    for title, offset, minutes, location, video, attendees, needs_response in _EVENT_ROWS:
        start = _soon(**offset)
        end = start + timedelta(minutes=minutes)
        if end < now:
            continue  # the demo respects its own "no past events" rule
        in_progress = start <= now < end
        items.append({
            "id": title.lower().replace(" ", "-"),
            "title": title,
            "url": "https://calendar.google.com/calendar/r",
            "start_at": start.isoformat(timespec="minutes"),
            "end_at": end.isoformat(timespec="minutes"),
            "all_day": False,
            "in_progress": in_progress,
            "minutes_until": None if in_progress else max(0, round((start - now).total_seconds() / 60)),
            "duration_minutes": minutes,
            "location": location,
            "video_url": "https://meet.google.com/demo-abcd-efg" if video else None,
            "tentative": False,
            "needs_response": needs_response,
            "attendee_count": attendees,
            "organizer": "Priya Nair",
            "calendar": "primary",
        })

    items.sort(key=lambda item: item["start_at"])
    items.sort(key=lambda item: (not item["in_progress"], not item["all_day"]))
    return {
        "items": items,
        "as_of": now.isoformat(timespec="minutes"),
        "day_ends": now.replace(hour=23, minute=59).isoformat(timespec="minutes"),
        "errors": [],
        "demo": True,
    }


# --- Gmail -----------------------------------------------------------------

_MAIL_ROWS = [
    ("Priya Nair", "priya@acme.com", "Re: retry budget — one more case", True, False),
    ("Sam Kirby", "sam@acme.com", "ARM runner image pinning, need your call", False, False),
    ("Dana Whitfield", "dana@acme.com", "Q3 platform roadmap review", False, True),
    ("Jules Okafor", "jules@partner.io", "Contract redlines attached", True, False),
]


def gmail():
    items = [
        {
            "id": f"demo-{index}",
            "subject": subject,
            "from_name": name,
            "from_email": email,
            "snippet": "",
            "important": important,
            "starred": starred,
            "bulk": False,
            "url": "https://mail.google.com/mail/u/0/#inbox",
            "received_at": None,
        }
        for index, (name, email, subject, important, starred) in enumerate(_MAIL_ROWS)
    ]
    return {
        "count": len(items),
        "count_is_partial": False,
        "items": items,
        "shown": len(items),
        "bulk_in_preview": 0,
        "query": "demo data",
        "inbox_url": "https://mail.google.com/mail/u/0/#inbox",
        "demo": True,
    }


# --- Slack -----------------------------------------------------------------

_CHANNEL_ROWS = [
    ("C01", "eng-platform", 12, 2, False),
    ("C02", "incidents", 5, 1, False),
    ("C03", "team-web", 7, 0, True),
    ("C04", "design-review", 2, 0, False),
]


def catercow():
    """Demo: the next few lunch days, with one already covered."""
    from .sources.catercow import WEEKDAY_NAMES, upcoming_lunch_days

    today = datetime.now().date()
    days = upcoming_lunch_days([0, 1, 2, 3], 14, today)
    # Pretend the soonest day is already sorted, so the lane isn't all-red.
    pending = days[1:4]

    items = [
        {
            "id": when.isoformat(),
            "date": when.isoformat(),
            "weekday": WEEKDAY_NAMES[when.weekday()],
            "label": f"{WEEKDAY_NAMES[when.weekday()]} {when.month}/{when.day}",
            "days_out": (when - today).days,
            "is_today": (when - today).days == 0,
            "is_tomorrow": (when - today).days == 1,
            "url": "https://www.catercow.com",
        }
        for when in pending
    ]
    return {
        "items": items,
        "pending": len(items),
        "selected": [days[0].isoformat()] if days else [],
        "sources": ["demo"],
        "warnings": [],
        "scanned": 0,
        "horizon_days": 14,
        "demo": True,
    }


def slack():
    items = [
        {
            "id": channel_id,
            "name": name,
            "private": private,
            "count": count,
            "mentions": mentions,
            "mode": "unread",
            "partial": False,
            "latest_ts": None,
            "latest_from": None,
            "url": "https://slack.com/app_redirect?channel=" + channel_id,
        }
        for channel_id, name, count, mentions, private in _CHANNEL_ROWS
    ]
    items.sort(key=lambda item: (-item["mentions"], -item["count"]))
    return {
        "items": items,
        "channels_selected": len(items),
        "total": sum(item["count"] for item in items),
        "mentions": sum(item["mentions"] for item in items),
        "quiet": 2,
        "mode": "unread",
        "is_bot": False,
        "workspace": "Acme",
        "demo": True,
    }
