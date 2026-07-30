"""Fetch the user's in-progress Jira issues."""

import base64

from .http_json import ApiError, request_json

FIELDS = [
    "summary",
    "status",
    "priority",
    "updated",
    "created",
    "issuetype",
    "project",
    "duedate",
    "parent",
    "assignee",
]

# Jira Cloud moved search to /search/jql; older Cloud and Server/DC still use /search.
SEARCH_PATHS = ("/rest/api/3/search/jql", "/rest/api/3/search", "/rest/api/2/search")


def _auth_header(cfg):
    email = (cfg["email"] or "").strip()
    token = cfg["api_token"]
    if email:
        raw = f"{email}:{token}".encode("utf-8")
        return {"Authorization": "Basic " + base64.b64encode(raw).decode("ascii")}
    # No email means a Server/DC personal access token.
    return {"Authorization": f"Bearer {token}"}


def _search(base_url, headers, jql, max_results):
    body = {"jql": jql, "maxResults": max_results, "fields": FIELDS}
    last_error = None

    for path in SEARCH_PATHS:
        try:
            return request_json(
                base_url + path, method="POST", headers=headers, body=body
            )
        except ApiError as exc:
            # Only fall through when the endpoint itself is absent.
            if exc.status in (404, 405, 410):
                last_error = exc
                continue
            raise

    # Every known search path was missing — almost always a wrong base_url.
    raise ApiError(
        "No Jira search endpoint responded. Check jira.base_url — it should look "
        "like https://your-org.atlassian.net",
        last_error.status if last_error else None,
    )


def _pick(node, *keys):
    for key in keys:
        if isinstance(node, dict) and node.get(key):
            return node[key]
    return None


def _normalize(issue, base_url):
    fields = issue.get("fields") or {}
    status = fields.get("status") or {}
    category = (status.get("statusCategory") or {}).get("key") or "indeterminate"
    priority = fields.get("priority") or {}
    issue_type = fields.get("issuetype") or {}
    project = fields.get("project") or {}
    parent = fields.get("parent") or {}

    key = issue.get("key") or ""
    return {
        "id": issue.get("id") or key,
        "key": key,
        "title": fields.get("summary") or "(no summary)",
        "url": f"{base_url}/browse/{key}" if key else base_url,
        "status": status.get("name") or "Unknown",
        "status_category": category,
        "priority": priority.get("name"),
        "type": issue_type.get("name"),
        "is_subtask": bool(issue_type.get("subtask")),
        "project": _pick(project, "key") or _pick(project, "name"),
        "parent": {
            "key": parent.get("key"),
            "title": ((parent.get("fields") or {}).get("summary")),
        }
        if parent.get("key")
        else None,
        "updated_at": fields.get("updated"),
        "created_at": fields.get("created"),
        "due_date": fields.get("duedate"),
    }


def fetch(cfg):
    """Return {"items": [...]} or raise ApiError."""
    base_url = cfg["base_url"].rstrip("/")
    headers = _auth_header(cfg)
    max_results = max(1, min(int(cfg.get("max_results") or 50), 100))

    payload = _search(base_url, headers, cfg["jql"], max_results)
    issues = payload.get("issues") or []
    items = [_normalize(issue, base_url) for issue in issues]

    # Sort by most recently touched; Jira honours ORDER BY but a custom JQL may not.
    items.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
    return {"items": items, "jql": cfg["jql"]}


def verify(cfg):
    """Prove the credentials work, then prove the JQL does. Raises ApiError."""
    base_url = cfg["base_url"].rstrip("/")

    me = None
    for path in ("/rest/api/3/myself", "/rest/api/2/myself"):
        try:
            me = request_json(base_url + path, headers=_auth_header(cfg))
            break
        except ApiError as exc:
            if exc.status in (404, 405, 410):
                continue
            raise
    if me is None:
        raise ApiError(
            "Reached the host but found no Jira API. Check the site URL — it "
            "should look like https://your-org.atlassian.net"
        )

    # A valid token with broken JQL is still a broken setup, so check both.
    found = fetch(cfg)

    return {
        "account": me.get("displayName") or me.get("name") or me.get("emailAddress"),
        "email": me.get("emailAddress"),
        "count": len(found["items"]),
    }
