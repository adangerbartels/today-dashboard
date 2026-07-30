#!/usr/bin/env python3
"""Run Today straight from a git clone, with nothing installed.

The package lives under ``src/`` so that packaging behaves properly, which also
means it isn't importable from a bare checkout. This puts it on the path.

    python3 today.py            # run the dashboard
    python3 today.py where      # print config and data locations
    python3 today.py backup

Installing (``pipx install today-dashboard``) gives you a ``today`` command
instead, and this file becomes unnecessary.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from today_dashboard.cli import main  # noqa: E402  (path set up above)

if __name__ == "__main__":
    sys.exit(main())
