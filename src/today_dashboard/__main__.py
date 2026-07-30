"""Allows ``python -m today_dashboard``."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
