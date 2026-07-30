"""Fetch GitHub pull requests that need the user's attention.

One GraphQL round-trip gets both feeds (review-requested and my-own PRs) plus
the review decision, CI rollup and mergeability that the REST API would need a
request per PR to assemble.
"""

from .http_json import ApiError, request_json

GRAPHQL_URL = "https://api.github.com/graphql"

PR_FRAGMENT = """
fragment PR on PullRequest {
  id
  number
  title
  url
  isDraft
  createdAt
  updatedAt
  additions
  deletions
  changedFiles
  reviewDecision
  mergeable
  repository { nameWithOwner }
  author { login }
  labels(first: 3) { nodes { name color } }
  comments(last: 20) { nodes { createdAt author { login } } }
  reviews(last: 20) { nodes { createdAt state author { login } } }
  commits(last: 1) { nodes { commit { statusCheckRollup { state } } } }
}
"""

QUERY = (
    """
query($review: String!, $mine: String!, $n: Int!) {
  viewer { login }
  reviewRequested: search(query: $review, type: ISSUE, first: $n) {
    issueCount
    nodes { ... on PullRequest { ...PR } }
  }
  mine: search(query: $mine, type: ISSUE, first: $n) {
    issueCount
    nodes { ... on PullRequest { ...PR } }
  }
}
"""
    + PR_FRAGMENT
)

# Highest weight wins for sorting; also drives which chip renders first.
REASON_WEIGHT = {
    "review-requested": 100,
    "ci-failing": 80,
    "changes-requested": 70,
    "conflicts": 60,
    "ready-to-merge": 50,
    "new-activity": 40,
}

REASON_LABEL = {
    "review-requested": "Review requested",
    "ci-failing": "CI failing",
    "changes-requested": "Changes requested",
    "conflicts": "Conflicts",
    "ready-to-merge": "Ready to merge",
    "new-activity": "New activity",
}

FAILING_STATES = {"FAILURE", "ERROR"}


USER_URL = "https://api.github.com/user"

# Orgs are capped at 100; anyone in more than that has bigger problems.
DISCOVERY_QUERY = """
query {
  viewer {
    login
    name
    organizations(first: 100) {
      totalCount
      nodes { login name }
    }
  }
  rateLimit { remaining resetAt }
}
"""


def _headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _sso_note(headers):
    """GitHub reports SAML-SSO trouble in X-GitHub-SSO.

    `partial-results` on a 200 is the important one: the request succeeded but
    silently omitted orgs this token isn't authorized for.
    """
    if not headers:
        return None
    raw = headers.get("X-GitHub-SSO")
    if not raw:
        return None
    if "partial-results" in raw:
        return "partial"
    if "required" in raw:
        return "required"
    return None


def token_scopes(token):
    """Classic-token scopes from X-OAuth-Scopes.

    Returns a list for a classic token, or None when the header is absent —
    which is how a fine-grained token presents, and worth telling the user
    about, because fine-grained tokens only ever cover a single owner.
    """
    try:
        _, headers = request_json(USER_URL, headers=_headers(token), with_headers=True)
    except ApiError:
        return None, None

    raw = headers.get("X-OAuth-Scopes")
    if raw is None:
        return None, _sso_note(headers)
    return [scope.strip() for scope in raw.split(",") if scope.strip()], _sso_note(headers)


def discover(token):
    """Who the token belongs to and which orgs it can actually see."""
    payload, headers = request_json(
        GRAPHQL_URL,
        method="POST",
        headers=_headers(token),
        body={"query": DISCOVERY_QUERY},
        with_headers=True,
    )

    data = payload.get("data")
    if not data or not data.get("viewer"):
        errors = payload.get("errors") or []
        message = (errors[0].get("message") if errors else None) or "Empty response"
        raise ApiError(f"GitHub GraphQL: {message}")

    viewer = data["viewer"]
    orgs_node = viewer.get("organizations") or {}
    orgs = [
        {"login": node.get("login"), "name": node.get("name")}
        for node in (orgs_node.get("nodes") or [])
        if node and node.get("login")
    ]
    orgs.sort(key=lambda org: (org["login"] or "").lower())

    return {
        "login": viewer.get("login"),
        "name": viewer.get("name"),
        "orgs": orgs,
        "org_total": orgs_node.get("totalCount") or len(orgs),
        "rate_remaining": (data.get("rateLimit") or {}).get("remaining"),
        "sso": _sso_note(headers),
    }


def diagnose(scopes, sso, discovery):
    """Actionable notes about why an org might be invisible. [(level, message)]"""
    notes = []

    if scopes is None:
        notes.append((
            "warn",
            "Couldn't read classic-token scopes, which usually means this is a "
            "fine-grained token. Those are scoped to a single account or "
            "organization — to cover several orgs at once, use a classic token "
            "with repo and read:org.",
        ))
    else:
        if not ({"repo", "public_repo"} & set(scopes)):
            notes.append((
                "warn",
                "This token has no repo scope, so private repositories — most "
                "org work — will be invisible.",
            ))
        elif "repo" not in scopes:
            notes.append((
                "warn",
                "Only public_repo is granted, so private org repositories will "
                "be invisible.",
            ))
        if not ({"read:org", "admin:org"} & set(scopes)):
            notes.append((
                "warn",
                "Without read:org, your organization list can't be read, so "
                "per-org filtering below may be incomplete.",
            ))

    if sso in ("partial", "required"):
        notes.append((
            "warn",
            "Some organizations enforce SAML SSO and this token isn't authorized "
            "for them, so their pull requests are being silently omitted. Open "
            "your token on github.com and use “Configure SSO” to authorize each org.",
        ))

    if not discovery["orgs"]:
        notes.append((
            "info",
            "No organizations are visible to this token. Personal repositories "
            "still work.",
        ))

    return notes


def verify(cfg, seen=None):
    """Prove the token works, discover orgs, and report what the rules surface."""
    discovery = discover(cfg["token"])
    scopes, scope_sso = token_scopes(cfg["token"])

    # Run the real query too: a token can authenticate yet lack repo scope.
    found = fetch(cfg, seen or {})

    sso = discovery["sso"] or scope_sso or found.get("sso")

    return {
        "account": discovery["login"],
        "name": discovery["name"],
        "rate_remaining": discovery["rate_remaining"],
        "count": len(found["items"]),
        "orgs": discovery["orgs"],
        "org_total": discovery["org_total"],
        "scopes": scopes,
        "truncated": found.get("truncated"),
        "total_open": found.get("total_open"),
        "notes": [
            {"level": level, "message": message}
            for level, message in diagnose(scopes, sso, discovery)
        ],
    }


def _query(base, extra):
    extra = (extra or "").strip()
    return f"{base} {extra}".strip() if extra else base


def _rollup_state(pr):
    nodes = ((pr.get("commits") or {}).get("nodes")) or []
    if not nodes:
        return None
    commit = (nodes[0] or {}).get("commit") or {}
    rollup = commit.get("statusCheckRollup")
    return (rollup or {}).get("state")


def _last_activity_by_others(pr, viewer):
    """Most recent comment or review from someone other than the viewer."""
    latest = None
    actor = None

    streams = (
        ((pr.get("comments") or {}).get("nodes")) or [],
        ((pr.get("reviews") or {}).get("nodes")) or [],
    )
    for nodes in streams:
        for node in nodes:
            if not node:
                continue
            login = ((node.get("author") or {}).get("login")) or ""
            if not login or login == viewer:
                continue
            stamp = node.get("createdAt")
            if stamp and (latest is None or stamp > latest):
                latest = stamp
                actor = login
    return latest, actor


def _normalize(pr, viewer, seen, rules, from_review_search, include_drafts=False):
    repo = ((pr.get("repository") or {}).get("nameWithOwner")) or "unknown/unknown"
    number = pr.get("number")
    key = f"{repo}#{number}"

    author = ((pr.get("author") or {}).get("login")) or "ghost"
    is_mine = author == viewer
    decision = pr.get("reviewDecision")
    mergeable = pr.get("mergeable")
    rollup = _rollup_state(pr)
    activity_at, activity_by = _last_activity_by_others(pr, viewer)
    is_draft = bool(pr.get("isDraft"))

    # A draft isn't asking anything of anyone yet.
    if is_draft and not include_drafts:
        return None

    reasons = []

    def enabled(reason):
        return rules.get(reason.replace("-", "_"), True)

    if from_review_search and enabled("review-requested"):
        reasons.append("review-requested")

    if is_mine:
        if enabled("ci-failing") and rollup in FAILING_STATES:
            reasons.append("ci-failing")

        if enabled("changes-requested") and decision == "CHANGES_REQUESTED":
            reasons.append("changes-requested")

        if enabled("conflicts") and mergeable == "CONFLICTING":
            reasons.append("conflicts")

        if (
            enabled("ready-to-merge")
            and not is_draft
            and decision == "APPROVED"
            and mergeable == "MERGEABLE"
            and rollup not in FAILING_STATES
        ):
            reasons.append("ready-to-merge")

    # New activity applies to any PR in either feed: someone spoke after you looked.
    if enabled("new-activity") and activity_at:
        baseline = seen.get(key)
        if baseline is None or activity_at > baseline:
            reasons.append("new-activity")

    if not reasons:
        return None

    reasons = sorted(set(reasons), key=lambda r: -REASON_WEIGHT.get(r, 0))

    return {
        "id": pr.get("id") or key,
        "key": key,
        "repo": repo,
        "number": number,
        "title": pr.get("title") or "(no title)",
        "url": pr.get("url"),
        "author": author,
        "is_mine": is_mine,
        "is_draft": is_draft,
        "review_decision": decision,
        "mergeable": mergeable,
        "checks": rollup,
        "additions": pr.get("additions") or 0,
        "deletions": pr.get("deletions") or 0,
        "changed_files": pr.get("changedFiles") or 0,
        "labels": [
            {"name": node.get("name"), "color": node.get("color")}
            for node in (((pr.get("labels") or {}).get("nodes")) or [])
            if node and node.get("name")
        ],
        "updated_at": pr.get("updatedAt"),
        "activity_at": activity_at,
        "activity_by": activity_by,
        "seen_at": seen.get(key),
        "reasons": reasons,
        "reason_labels": [REASON_LABEL.get(r, r) for r in reasons],
        "weight": max(REASON_WEIGHT.get(r, 0) for r in reasons),
    }


def fetch(cfg, seen):
    """Return {"items": [...], "viewer": login} or raise ApiError."""
    token = cfg["token"]
    extra = cfg.get("extra_query")
    limit = max(1, min(int(cfg.get("max_results") or 50), 100))
    rules = cfg.get("rules") or {}

    variables = {
        "review": _query("is:open is:pr review-requested:@me archived:false", extra),
        "mine": _query("is:open is:pr author:@me archived:false", extra),
        "n": limit,
    }

    payload, headers = request_json(
        GRAPHQL_URL,
        method="POST",
        headers=_headers(token),
        body={"query": QUERY, "variables": variables},
        timeout=30,
        with_headers=True,
    )

    data = payload.get("data")
    if not data:
        errors = payload.get("errors") or []
        message = (errors[0].get("message") if errors else None) or "Empty response"
        raise ApiError(f"GitHub GraphQL: {message}")

    viewer = ((data.get("viewer") or {}).get("login")) or ""

    # Empty means every owner the token can see. Case-insensitive: GitHub logins are.
    allowed = {str(owner).lower() for owner in (cfg.get("orgs") or []) if owner}

    # Merge both feeds, remembering which PRs the review-requested search returned.
    merged = {}
    review_ids = set()
    total_open = 0
    truncated = False
    skipped_owners = set()

    for bucket, from_review in (("reviewRequested", True), ("mine", False)):
        result = data.get(bucket) or {}
        nodes = result.get("nodes") or []
        total_open += result.get("issueCount") or 0
        # More matches exist than we asked for, so the feed is an incomplete view.
        if (result.get("issueCount") or 0) > len(nodes):
            truncated = True

        for node in nodes:
            if not node or not node.get("number"):
                continue  # non-PR search hit, or a repo we lost access to

            owner = (((node.get("repository") or {}).get("nameWithOwner")) or "/").split("/")[0]
            if allowed and owner.lower() not in allowed:
                skipped_owners.add(owner)
                continue

            node_id = node.get("id")
            merged[node_id] = node
            if from_review:
                review_ids.add(node_id)

    include_drafts = bool(cfg.get("include_drafts", False))
    drafts_hidden = 0

    items = []
    for node_id, node in merged.items():
        item = _normalize(node, viewer, seen, rules, node_id in review_ids, include_drafts)
        if item:
            items.append(item)
        elif node.get("isDraft"):
            drafts_hidden += 1

    # Stable sort, so the second pass keeps recency order inside each weight tier.
    items.sort(
        key=lambda item: item.get("activity_at") or item.get("updated_at") or "",
        reverse=True,
    )
    items.sort(key=lambda item: -item["weight"])

    all_keys = [
        f"{((n.get('repository') or {}).get('nameWithOwner'))}#{n.get('number')}"
        for n in merged.values()
    ]
    return {
        "items": items,
        "viewer": viewer,
        "all_keys": all_keys,
        "total_open": total_open,
        "truncated": truncated,
        "filtered_owners": sorted(skipped_owners),
        "drafts_hidden": drafts_hidden,
        "sso": _sso_note(headers),
    }
