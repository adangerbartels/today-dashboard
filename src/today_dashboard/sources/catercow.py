"""Which upcoming lunch days have no CaterCow selection yet.

CaterCow has no public API, so "selected" is inferred rather than queried. Two
extractors, either or both:

1. **Email** (default, and what actually works today). Confirmation subjects read
   "Your meal selection on Monday 8/3 is confirmed", which is stable and
   parseable. Only Gmail *metadata* is read — subjects and dates, never bodies.

2. **Session cookie** (optional). Fetches a logged-in page and pulls dates out of
   it. CaterCow's authenticated HTML isn't something we can see from here, so the
   pattern is configurable and `probe()` reports what a fetch actually returned.

Absence of evidence is the signal: a lunch day with no confirmation is treated as
not selected, which is exactly the question being asked.
"""

import re
from datetime import date, datetime, timedelta

from . import google_auth
from .http_json import ApiError, request_json

GMAIL_LIST = "https://www.googleapis.com/gmail/v1/users/me/messages"
GMAIL_MSG = "https://www.googleapis.com/gmail/v1/users/me/messages/{}"

DEFAULT_EMAIL_QUERY = 'from:catercow.com subject:"meal selection"'
DEFAULT_BASE_URL = "https://www.catercow.com"

# "Your meal selection on Monday 8/3 is confirmed"
CONFIRM_RE = re.compile(
    r"meal selection\s+on\s+"
    r"(?:(?P<weekday>[A-Za-z]{3,9})\s*,?\s+)?"
    r"(?P<month>\d{1,2})[/-](?P<day>\d{1,2})"
    r"(?:[/-](?P<year>\d{2,4}))?",
    re.I,
)

# Words that mean "this day is handled" when scraping a page.
DEFAULT_HTML_PATTERN = (
    r"(?P<month>\d{1,2})/(?P<day>\d{1,2})[^<]{0,80}?"
    r"(?:confirmed|selected|your\s+selection|ordered)"
)

WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday",
                 "Friday", "Saturday", "Sunday")

MAX_MESSAGES = 100


def _header(message, name):
    for entry in ((message.get("payload") or {}).get("headers") or []):
        if (entry.get("name") or "").lower() == name.lower():
            return entry.get("value")
    return None


def _parse_email_date(raw):
    """RFC 2822 Date header → date, for inferring the year."""
    if not raw:
        return None
    try:
        from email.utils import parsedate_to_datetime

        parsed = parsedate_to_datetime(raw)
        return parsed.date() if parsed else None
    except (TypeError, ValueError):
        return None


def _resolve_year(month, day, reference, explicit=None):
    """Subjects carry no year, so pick the one nearest the message date.

    A confirmation for 1/5 sent in December means next January, not last.
    """
    if explicit:
        year = int(explicit)
        if year < 100:
            year += 2000
        try:
            return date(year, month, day)
        except ValueError:
            return None

    reference = reference or date.today()
    best = None
    # ±2 years so 2/29 still resolves to the nearest leap year rather than being
    # dropped — a missed date would wrongly read as "not selected". Nearest-wins
    # means the wider candidates never beat an adjacent year for normal dates.
    for candidate in range(reference.year - 1, reference.year + 3):
        try:
            attempt = date(candidate, month, day)
        except ValueError:
            continue
        if best is None or abs((attempt - reference).days) < abs((best - reference).days):
            best = attempt
    return best


def _from_email(google_cfg, query, limit=MAX_MESSAGES):
    """Selected dates parsed from confirmation subjects. (dates, scanned)"""
    listing = google_auth.api(google_cfg, GMAIL_LIST, {
        "q": query,
        "maxResults": str(limit),
        # Confirmations are usually read and archived, so search all mail.
        "includeSpamTrash": "true",
    })

    ids = [m["id"] for m in (listing.get("messages") or []) if m.get("id")]
    found = {}

    for message_id in ids:
        try:
            message = google_auth.api(google_cfg, GMAIL_MSG.format(message_id), {
                "format": "metadata",
                "metadataHeaders": ["Subject", "Date"],
            })
        except ApiError:
            continue

        subject = _header(message, "Subject") or ""
        match = CONFIRM_RE.search(subject)
        if not match:
            continue

        sent = _parse_email_date(_header(message, "Date"))
        when = _resolve_year(
            int(match.group("month")), int(match.group("day")), sent, match.group("year")
        )
        if when:
            # Duplicated confirmations collapse naturally.
            found[when] = subject

    return found, len(ids)


def _fetch_page(cfg):
    """Raw HTML from a logged-in CaterCow page. Raises ApiError."""
    import urllib.error
    import urllib.request

    base = (cfg.get("base_url") or DEFAULT_BASE_URL).rstrip("/")
    path = cfg.get("orders_path") or "/"
    url = base + (path if path.startswith("/") else "/" + path)

    request = urllib.request.Request(url, headers={
        "Cookie": cfg["cookie"],
        "User-Agent": "todo-dashboard/1.0",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
    })

    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            final_url = response.geturl()
            body = response.read()
            if response.headers.get("Content-Encoding") == "gzip":
                import gzip
                body = gzip.decompress(body)
            html = body.decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise ApiError(
                "CaterCow rejected the session cookie — it has probably expired. "
                "Copy a fresh one from your browser.", exc.code
            ) from exc
        raise ApiError(f"CaterCow returned HTTP {exc.code} for {url}", exc.code) from exc
    except urllib.error.URLError as exc:
        raise ApiError(f"Could not reach CaterCow: {exc.reason}") from exc
    except TimeoutError as exc:
        raise ApiError("CaterCow timed out") from exc

    # Devise bounces signed-out requests to the sign-in page with a 200.
    if re.search(r"/users/sign_in|sign\s*in|log\s*in", final_url, re.I) or (
        re.search(r'name="user\[password\]"|id="new_user"', html)
    ):
        raise ApiError(
            "That cookie isn't signed in — CaterCow served the login page. Copy the "
            "session cookie again from a logged-in browser tab."
        )

    return html, final_url


def _from_html(cfg, reference):
    """Selected dates scraped from a page. (dates, matched_count)"""
    html, _ = _fetch_page(cfg)

    pattern = (cfg.get("selected_pattern") or "").strip() or DEFAULT_HTML_PATTERN
    try:
        regex = re.compile(pattern, re.I | re.S)
    except re.error as exc:
        raise ApiError(f"selected_pattern isn't a valid regex: {exc}") from exc

    found = {}
    for match in regex.finditer(html):
        groups = match.groupdict()
        if not groups.get("month") or not groups.get("day"):
            continue
        when = _resolve_year(
            int(groups["month"]), int(groups["day"]), reference, groups.get("year")
        )
        if when:
            found[when] = "page"
    return found, len(found)


def upcoming_lunch_days(lunch_days, horizon_days, today=None):
    """The lunch dates from today forward, within the horizon."""
    today = today or date.today()
    wanted = {int(d) for d in (lunch_days or []) if 0 <= int(d) <= 6}
    if not wanted:
        return []
    return [
        today + timedelta(days=offset)
        for offset in range(max(1, int(horizon_days or 14)))
        if (today + timedelta(days=offset)).weekday() in wanted
    ]


def fetch(cfg, google_cfg=None):
    """Return {"items": [...]} — the upcoming lunch days with no selection."""
    today = date.today()
    selected = {}
    sources = []
    warnings = []
    scanned = 0

    use_email = cfg.get("use_email", True)
    if use_email:
        if google_cfg and google_cfg.get("refresh_token"):
            query = (cfg.get("email_query") or "").strip() or DEFAULT_EMAIL_QUERY
            try:
                found, scanned = _from_email(google_cfg, query)
                selected.update(found)
                sources.append("email")
            except ApiError as exc:
                warnings.append(f"Couldn't read confirmation emails — {exc}")
        else:
            warnings.append(
                "Connect Google to read CaterCow confirmation emails, or add a "
                "session cookie."
            )

    if cfg.get("cookie"):
        try:
            found, _ = _from_html(cfg, today)
            selected.update(found)
            sources.append("cookie")
        except ApiError as exc:
            warnings.append(f"CaterCow page unavailable — {exc}")

    if not sources:
        return {
            "items": [], "selected": [], "sources": [], "warnings": warnings,
            "unconfigured": True, "pending": 0,
        }

    days = upcoming_lunch_days(cfg.get("lunch_days"), cfg.get("horizon_days"), today)
    order_url = (cfg.get("base_url") or DEFAULT_BASE_URL).rstrip("/")

    items = []
    for when in days:
        if when in selected:
            continue
        days_out = (when - today).days
        items.append({
            "id": when.isoformat(),
            "date": when.isoformat(),
            "weekday": WEEKDAY_NAMES[when.weekday()],
            "label": f"{WEEKDAY_NAMES[when.weekday()]} {when.month}/{when.day}",
            "days_out": days_out,
            "is_today": days_out == 0,
            "is_tomorrow": days_out == 1,
            "url": order_url,
        })

    return {
        "items": items,
        "pending": len(items),
        # Selected days inside the horizon, for the "n covered" line.
        "selected": sorted(d.isoformat() for d in selected if d in set(days)),
        "sources": sources,
        "warnings": warnings,
        "scanned": scanned,
        "horizon_days": int(cfg.get("horizon_days") or 14),
    }


def probe(cfg):
    """Report what a cookie fetch actually returns, without dumping the page.

    CaterCow's signed-in HTML can't be inspected from here, so this reports
    structure — never content — to tune `selected_pattern` against.
    """
    html, final_url = _fetch_page(cfg)

    dates = re.findall(r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b", html)
    keywords = {
        word: len(re.findall(word, html, re.I))
        for word in ("confirmed", "selected", "selection", "order", "lunch", "menu")
    }
    embedded = [
        name for name, needle in (
            ("__NEXT_DATA__", "__NEXT_DATA__"),
            ("window.__INITIAL_STATE__", "__INITIAL_STATE__"),
            ("JSON-LD", 'type="application/ld+json"'),
            ("Turbo/Rails", "data-turbo"),
        ) if needle in html
    ]

    default_hits, _ = 0, None
    try:
        default_hits = len(re.findall(DEFAULT_HTML_PATTERN, html, re.I | re.S))
    except re.error:
        pass

    return {
        "final_url": final_url,
        "bytes": len(html),
        "looks_signed_in": "sign_in" not in final_url,
        "date_like_strings": len(dates),
        "distinct_dates": sorted(set(dates))[:40],
        "keyword_counts": keywords,
        "embedded_state": embedded,
        "default_pattern_matches": default_hits,
    }
