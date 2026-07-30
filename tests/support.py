"""Shared helpers for the test suite.

Every source module funnels its network access through a small number of
functions, so the tests replace those rather than mocking HTTP. That keeps them
fast, offline and deterministic.
"""

import contextlib
import email.message
from datetime import datetime, timedelta, timezone


def utc(**delta):
    """An ISO-8601 UTC stamp offset from now."""
    stamp = datetime.now(timezone.utc) + timedelta(**delta)
    return stamp.isoformat(timespec="seconds").replace("+00:00", "Z")


def local(**delta):
    """A timezone-aware local datetime offset from now."""
    return (datetime.now().astimezone() + timedelta(**delta)).replace(microsecond=0)


def headers(**pairs):
    """A case-insensitive header object, as urllib returns."""
    message = email.message.Message()
    for key, value in pairs.items():
        message[key.replace("_", "-")] = value
    return message


@contextlib.contextmanager
def patched(obj, **attrs):
    """Temporarily replace attributes on a module, restoring them afterwards."""
    missing = object()
    saved = {name: getattr(obj, name, missing) for name in attrs}
    for name, value in attrs.items():
        setattr(obj, name, value)
    try:
        yield
    finally:
        for name, value in saved.items():
            if value is missing:
                delattr(obj, name)
            else:
                setattr(obj, name, value)


def google_api(responses):
    """Fake ``google_auth.api``: maps a URL substring to a payload or callable."""
    calls = []

    def api(cfg, url, params=None, timeout=20):
        calls.append((url, params or {}))
        for needle, payload in responses.items():
            if needle in url:
                return payload(url, params or {}) if callable(payload) else payload
        raise AssertionError(f"unexpected Google URL: {url}")

    api.calls = calls
    return api


def slack_api(handlers):
    """Fake ``slack.request_json``: maps a method name to a payload or callable."""
    from urllib.parse import parse_qs, urlparse

    calls = []

    def request_json(url, headers=None, timeout=20):
        parsed = urlparse(url)
        method = parsed.path.rsplit("/", 1)[-1]
        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        token = (headers or {}).get("Authorization", "").replace("Bearer ", "")
        calls.append((method, query, token))
        if method not in handlers:
            raise AssertionError(f"unexpected Slack method: {method}")
        payload = handlers[method]
        return payload(query, token) if callable(payload) else payload

    request_json.calls = calls
    return request_json


def pull_request(number, *, repo="acme/web", author="someone", draft=False,
                 checks="SUCCESS", decision=None, mergeable="MERGEABLE",
                 commenter="colleague", node_id=None):
    """A GitHub GraphQL PullRequest node."""
    return {
        "id": node_id or f"pr{number}",
        "number": number,
        "title": f"Change {number}",
        "url": f"https://github.com/{repo}/pull/{number}",
        "isDraft": draft,
        "createdAt": utc(days=-3),
        "updatedAt": utc(hours=-2),
        "additions": 10,
        "deletions": 2,
        "changedFiles": 1,
        "reviewDecision": decision,
        "mergeable": mergeable,
        "repository": {"nameWithOwner": repo},
        "author": {"login": author},
        "labels": {"nodes": []},
        "comments": {"nodes": [{"createdAt": utc(hours=-1), "author": {"login": commenter}}]},
        "reviews": {"nodes": []},
        "commits": {"nodes": [{"commit": {"statusCheckRollup": {"state": checks}}}]},
    }


def github_search(review=(), mine=(), *, review_total=None, mine_total=None, sso=None):
    """A fake ``github.request_json`` returning both search buckets."""
    def request_json(url, **kwargs):
        payload = {"data": {
            "viewer": {"login": "me"},
            "reviewRequested": {
                "issueCount": review_total if review_total is not None else len(review),
                "nodes": list(review),
            },
            "mine": {
                "issueCount": mine_total if mine_total is not None else len(mine),
                "nodes": list(mine),
            },
        }}
        head = headers(**({"X_GitHub_SSO": sso} if sso else {}))
        return (payload, head) if kwargs.get("with_headers") else payload

    return request_json
