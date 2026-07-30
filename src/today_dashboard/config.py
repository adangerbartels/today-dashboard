"""Configuration loading and saving: config.json, overridden by environment variables."""

import json
import os
import tempfile
from pathlib import Path

APP_NAME = "today-dashboard"


def resolve_home():
    """Directory holding config.json, data/ and backups/.

    Never the package directory: an installed copy lives in site-packages, which
    is the wrong place to write a user's tokens.

    1. ``TODAY_HOME`` if set — explicit wins.
    2. ``./config.json`` in the working directory, so a git clone keeps working
       exactly as before.
    3. ``$XDG_CONFIG_HOME/today-dashboard`` (or ``~/.config/...``) otherwise.
    """
    override = os.environ.get("TODAY_HOME")
    if override:
        return Path(override).expanduser().resolve()

    local = Path.cwd() / "config.json"
    if local.is_file():
        return local.parent.resolve()

    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base).expanduser() if base else Path.home() / ".config"
    return (root / APP_NAME).resolve()


HOME = resolve_home()
CONFIG_PATH = HOME / "config.json"
DATA_DIR = HOME / "data"
BACKUP_DIR = HOME / "backups"

DEFAULT_JQL = (
    'assignee = currentUser() AND statusCategory = "In Progress" ORDER BY updated DESC'
)

DEFAULTS = {
    "jira": {
        "base_url": "",
        "email": "",
        "api_token": "",
        "jql": DEFAULT_JQL,
        "max_results": 50,
    },
    "github": {
        "token": "",
        "extra_query": "",
        # Owner logins (orgs, or your own username) to include. Empty = all visible.
        "orgs": [],
        # Drafts aren't asking anything of anyone yet, so they're hidden.
        "include_drafts": False,
        # Written by the setup wizard after a successful check, so the org picker
        # can show every owner — including ones you filtered out — without a
        # network call. Display cache only; refreshed on each connection test.
        "known_owners": [],
        "account": "",
        "max_results": 50,
        # One switch per attention reason; see sources/github.py.
        "rules": {
            "review_requested": True,
            "ci_failing": True,
            "changes_requested": True,
            "conflicts": True,
            "ready_to_merge": True,
            "new_activity": True,
        },
    },
    # Gmail and Calendar share one OAuth connection, so they share a section.
    "google": {
        "client_id": "",
        "client_secret": "",
        "refresh_token": "",
        "account": "",
        "gmail_query": "",
        "gmail_max": 50,
        "calendar_ids": ["primary"],
        "known_calendars": [],
        "include_all_day": True,
        "skip_declined": True,
        "max_events": 20,
    },
    # CaterCow has no API. Selections are inferred from confirmation emails
    # and/or a signed-in page fetched with a session cookie.
    "catercow": {
        "cookie": "",
        "base_url": "https://www.catercow.com",
        "orders_path": "",
        "selected_pattern": "",
        "use_email": True,
        "email_query": "",
        # 0 = Monday. Weekdays a lunch selection is expected.
        "lunch_days": [0, 1, 2, 3],
        "horizon_days": 14,
    },
    "slack": {
        "token": "",
        # Token rotation (xoxe.xoxp- access tokens expire after 12 hours). With
        # these three set, the app renews the token itself.
        "refresh_token": "",
        "client_id": "",
        "client_secret": "",
        "expires_at": 0,
        "channels": [],
        "known_channels": [],
        "account": "",
        "workspace": "",
    },
    "server": {
        "host": "127.0.0.1",
        "port": 8787,
        "cache_ttl_seconds": 60,
    },
}

# env var -> (section, key). Env always wins over the file.
ENV_MAP = {
    "JIRA_BASE_URL": ("jira", "base_url"),
    "JIRA_EMAIL": ("jira", "email"),
    "JIRA_API_TOKEN": ("jira", "api_token"),
    "JIRA_JQL": ("jira", "jql"),
    "GITHUB_TOKEN": ("github", "token"),
    "GITHUB_EXTRA_QUERY": ("github", "extra_query"),
    "GOOGLE_CLIENT_ID": ("google", "client_id"),
    "GOOGLE_CLIENT_SECRET": ("google", "client_secret"),
    "GOOGLE_REFRESH_TOKEN": ("google", "refresh_token"),
    "GMAIL_QUERY": ("google", "gmail_query"),
    "SLACK_TOKEN": ("slack", "token"),
    "CATERCOW_COOKIE": ("catercow", "cookie"),
    "TODAY_HOST": ("server", "host"),
    "TODAY_PORT": ("server", "port"),
}


def _merge(base, override):
    out = dict(base)
    for key, value in (override or {}).items():
        if key.startswith("_"):
            continue
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load():
    cfg = json.loads(json.dumps(DEFAULTS))  # deep copy

    if CONFIG_PATH.exists():
        try:
            cfg = _merge(cfg, json.loads(CONFIG_PATH.read_text()))
        except (json.JSONDecodeError, OSError) as exc:
            raise SystemExit(f"Could not read {CONFIG_PATH}: {exc}")

    for env_key, (section, key) in ENV_MAP.items():
        raw = os.environ.get(env_key)
        if raw:
            cfg[section][key] = raw

    try:
        cfg["server"]["port"] = int(cfg["server"]["port"])
    except (TypeError, ValueError):
        cfg["server"]["port"] = DEFAULTS["server"]["port"]

    cfg["jira"]["base_url"] = (cfg["jira"]["base_url"] or "").rstrip("/")
    if not (cfg["jira"]["jql"] or "").strip():
        cfg["jira"]["jql"] = DEFAULT_JQL

    return cfg


def jira_configured(cfg):
    """Cloud wants email + API token; Server/DC accepts a bare PAT."""
    jira = cfg["jira"]
    return bool(jira["base_url"] and jira["api_token"])


def github_configured(cfg):
    return bool(cfg["github"]["token"])


def google_configured(cfg):
    """Needs the OAuth client *and* a completed consent."""
    google = cfg["google"]
    return bool(google["client_id"] and google["client_secret"] and google["refresh_token"])


def slack_configured(cfg):
    return bool(cfg["slack"]["token"])


def catercow_configured(cfg):
    """Either extractor counts: a cookie, or email inference via Google."""
    catercow = cfg["catercow"]
    return bool(catercow["cookie"]) or bool(
        catercow.get("use_email", True) and google_configured(cfg)
    )


# --- saving (used by the in-app setup wizard) ------------------------------

# Which env vars shadow each source, so the UI can warn that a save won't apply.
SOURCE_ENV = {
    "jira": ["JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN", "JIRA_JQL"],
    "github": ["GITHUB_TOKEN", "GITHUB_EXTRA_QUERY"],
    "google": [
        "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN",
        "GMAIL_QUERY",
    ],
    "slack": ["SLACK_TOKEN"],
    "catercow": ["CATERCOW_COOKIE"],
}

SECRET_KEYS = {
    ("jira", "api_token"),
    ("github", "token"),
    ("google", "client_secret"),
    ("google", "refresh_token"),
    ("slack", "token"),
    ("slack", "refresh_token"),
    ("slack", "client_secret"),
    # A session cookie is a bearer credential for the whole account.
    ("catercow", "cookie"),
}


def mask(secret):
    """A hint that proves a token is stored without disclosing it."""
    if not secret:
        return None
    return "•" * 8 + secret[-4:] if len(secret) > 8 else "•" * 8


def env_overrides(section=None):
    """Env vars currently set that would win over config.json."""
    keys = SOURCE_ENV.get(section, list(ENV_MAP)) if section else list(ENV_MAP)
    return [key for key in keys if os.environ.get(key)]


def read_file():
    """Raw config.json, so saving one section can't clobber the rest."""
    if not CONFIG_PATH.exists():
        return {}
    try:
        raw = json.loads(CONFIG_PATH.read_text())
        return raw if isinstance(raw, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _write_private(payload):
    """Atomic, owner-only (0600) — the file holds API tokens."""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    # The directory holds tokens; keep it owner-only too, not just the file.
    try:
        os.chmod(CONFIG_PATH.parent, 0o700)
    except OSError:
        pass
    fd, tmp = tempfile.mkstemp(dir=str(CONFIG_PATH.parent), prefix=".config-", suffix=".json")
    try:
        os.chmod(tmp, 0o600)
        with os.fdopen(fd, "w") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
        os.replace(tmp, CONFIG_PATH)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    # os.replace preserves the temp file's mode, but be explicit for pre-existing files.
    os.chmod(CONFIG_PATH, 0o600)


SECTIONS = ("jira", "github", "google", "slack", "catercow")


def save_section(section, values):
    """Merge `values` into one section of config.json, leaving everything else alone."""
    if section not in SECTIONS:
        raise ValueError(f"Unknown section: {section}")

    raw = read_file()
    current = raw.get(section)
    raw[section] = {**current, **values} if isinstance(current, dict) else dict(values)
    _write_private(raw)
    return CONFIG_PATH
