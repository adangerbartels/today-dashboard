"""Back up and restore config.json.

The file holds live API tokens, so snapshots are written next to it with
owner-only permissions and never leave this machine. Driven by ``today backup``.
"""

import json
import os
import shutil
import stat
from datetime import datetime
from pathlib import Path

from .config import BACKUP_DIR, CONFIG_PATH, HOME

CONFIG = CONFIG_PATH
ROOT = HOME
KEEP = 20


def _private(path):
    os.chmod(path, 0o600)


def _summarize(path):
    """What's in a snapshot, without printing any secret."""
    try:
        raw = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        return f"unreadable ({exc})"

    parts = []
    for section, values in sorted(raw.items()):
        if section.startswith("_") or not isinstance(values, dict):
            continue
        secrets = [
            key for key in ("api_token", "token", "refresh_token", "client_secret")
            if values.get(key)
        ]
        parts.append(f"{section}{'✓' if secrets else '·'}")
    return " ".join(parts) or "empty"


def snapshots():
    if not BACKUP_DIR.is_dir():
        return []
    return sorted(BACKUP_DIR.glob("config-*.json"), reverse=True)


def take():
    if not CONFIG.exists():
        print(f"Nothing to back up: {CONFIG} does not exist.")
        return 1

    BACKUP_DIR.mkdir(exist_ok=True)
    os.chmod(BACKUP_DIR, 0o700)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = BACKUP_DIR / f"config-{stamp}.json"

    # Identical to the newest snapshot? Don't add noise.
    existing = snapshots()
    if existing and existing[0].read_bytes() == CONFIG.read_bytes():
        print(f"Already backed up — {existing[0].name} is identical.")
        return 0

    shutil.copy2(CONFIG, target)
    _private(target)
    print(f"Saved {target.relative_to(ROOT)}  ({_summarize(target)})")

    for stale in snapshots()[KEEP:]:
        stale.unlink()
        print(f"Pruned {stale.name}")
    return 0


def show():
    found = snapshots()
    if not found:
        print("No snapshots yet. Run: python3 backup_config.py")
        return 0

    print(f"{len(found)} snapshot(s) in {BACKUP_DIR.relative_to(ROOT)}/\n")
    for path in found:
        mode = stat.S_IMODE(path.stat().st_mode)
        flag = "" if mode == 0o600 else f"  ⚠ mode {oct(mode)}"
        print(f"  {path.name}  {_summarize(path)}{flag}")
    return 0


def restore(which):
    found = snapshots()
    if not found:
        print("No snapshots to restore from.")
        return 1

    source = Path(which) if which else found[0]
    if not source.is_absolute():
        source = ROOT / source
    if not source.is_file():
        print(f"No such snapshot: {source}")
        return 1

    # Never discard the current config silently — snapshot it first.
    if CONFIG.exists() and CONFIG.read_bytes() != source.read_bytes():
        print("Backing up the current config before overwriting it…")
        take()

    shutil.copy2(source, CONFIG)
    _private(CONFIG)
    print(f"Restored {source.name} → config.json  ({_summarize(CONFIG)})")
    print("Restart the server (or press Test & save in the wizard) to pick it up.")
    return 0

