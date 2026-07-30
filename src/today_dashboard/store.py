"""Local JSON persistence for hand-written todos and PR "last seen" baselines.

Small enough that a file per concern beats a database. Writes are atomic
(temp file + os.replace) so a crash mid-save can't truncate the file.
"""

import json
import os
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone

from .config import DATA_DIR

_LOCK = threading.Lock()

TODOS_FILE = DATA_DIR / "todos.json"
SEEN_FILE = DATA_DIR / "seen.json"


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read(path, fallback):
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return fallback


def _write(path, payload):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(DATA_DIR), prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(payload, handle, indent=2)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


# --- local todos ----------------------------------------------------------


def list_todos():
    with _LOCK:
        todos = _read(TODOS_FILE, [])
    return todos if isinstance(todos, list) else []


def add_todo(title, link=None, origin=None):
    title = (title or "").strip()
    if not title:
        raise ValueError("title is required")

    todo = {
        "id": uuid.uuid4().hex[:12],
        "title": title[:500],
        "done": False,
        "created_at": _now(),
        "completed_at": None,
        "link": link or None,
        "origin": origin or None,
    }
    with _LOCK:
        todos = _read(TODOS_FILE, [])
        if not isinstance(todos, list):
            todos = []
        todos.insert(0, todo)
        _write(TODOS_FILE, todos)
    return todo


def update_todo(todo_id, patch):
    with _LOCK:
        todos = _read(TODOS_FILE, [])
        for todo in todos:
            if todo.get("id") != todo_id:
                continue
            if "done" in patch:
                todo["done"] = bool(patch["done"])
                todo["completed_at"] = _now() if todo["done"] else None
            if "title" in patch:
                new_title = (patch["title"] or "").strip()
                if new_title:
                    todo["title"] = new_title[:500]
            _write(TODOS_FILE, todos)
            return todo
    return None


def delete_todo(todo_id):
    with _LOCK:
        todos = _read(TODOS_FILE, [])
        remaining = [t for t in todos if t.get("id") != todo_id]
        if len(remaining) == len(todos):
            return False
        _write(TODOS_FILE, remaining)
    return True


def clear_completed():
    with _LOCK:
        todos = _read(TODOS_FILE, [])
        remaining = [t for t in todos if not t.get("done")]
        removed = len(todos) - len(remaining)
        if removed:
            _write(TODOS_FILE, remaining)
    return removed


# --- PR "seen" baselines --------------------------------------------------
# Maps "owner/repo#123" -> ISO timestamp of when the user last acknowledged it.
# Anything another person did after that timestamp counts as new activity.


def get_seen():
    with _LOCK:
        seen = _read(SEEN_FILE, {})
    return seen if isinstance(seen, dict) else {}


def mark_seen(keys):
    stamp = _now()
    with _LOCK:
        seen = _read(SEEN_FILE, {})
        if not isinstance(seen, dict):
            seen = {}
        for key in keys:
            seen[key] = stamp
        _write(SEEN_FILE, seen)
    return stamp


def prune_seen(live_keys):
    """Drop baselines for PRs that have dropped out of the feed (merged/closed)."""
    live = set(live_keys)
    with _LOCK:
        seen = _read(SEEN_FILE, {})
        if not isinstance(seen, dict):
            return
        kept = {k: v for k, v in seen.items() if k in live}
        if len(kept) != len(seen):
            _write(SEEN_FILE, kept)


# --- tiny TTL cache ------------------------------------------------------


class TTLCache:
    """Keeps the UI's 60s auto-refresh from hammering the Jira/GitHub APIs."""

    def __init__(self, ttl_seconds):
        self.ttl = max(0, int(ttl_seconds))
        self._entries = {}
        self._lock = threading.Lock()

    def get(self, key):
        if self.ttl == 0:
            return None
        with self._lock:
            entry = self._entries.get(key)
        if not entry:
            return None
        value, stamp = entry
        if time.monotonic() - stamp > self.ttl:
            return None
        return value

    def set(self, key, value):
        with self._lock:
            self._entries[key] = (value, time.monotonic())

    def clear(self):
        with self._lock:
            self._entries.clear()
