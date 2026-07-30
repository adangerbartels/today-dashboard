"""Command line entry point: ``today``."""

import argparse
import sys

from . import __version__


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)

    parser = argparse.ArgumentParser(
        prog="today",
        description="A one-screen dashboard of the work that actually needs you.",
    )
    parser.add_argument("--version", action="version", version=f"today {__version__}")
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("serve", help="run the dashboard (default)")
    run.add_argument("--host", default=None, help="default 127.0.0.1")
    run.add_argument("--port", type=int, default=None, help="default 8787")
    run.add_argument("--verbose", action="store_true", help="log every request")

    backup = sub.add_parser("backup", help="snapshot or restore config.json")
    group = backup.add_mutually_exclusive_group()
    group.add_argument("--list", action="store_true", help="show existing snapshots")
    group.add_argument(
        "--restore", nargs="?", const="", metavar="PATH",
        help="restore the newest snapshot, or a specific one",
    )

    sub.add_parser("where", help="print the config and data locations")

    # Bare `today` runs the server, which is what you want 99% of the time.
    if not argv or argv[0].startswith("-") and argv[0] not in ("--version", "-h", "--help"):
        argv = ["serve"] + argv

    args = parser.parse_args(argv)

    if args.command == "backup":
        from . import backup as backup_module

        if args.list:
            return backup_module.show()
        if args.restore is not None:
            return backup_module.restore(args.restore)
        return backup_module.take()

    if args.command == "where":
        from . import config

        print(f"config   {config.CONFIG_PATH}")
        print(f"data     {config.DATA_DIR}")
        print(f"backups  {config.BACKUP_DIR}")
        print(f"exists   {'yes' if config.CONFIG_PATH.is_file() else 'no'}")
        return 0

    from . import server

    server.serve(args.host, args.port, args.verbose)
    return 0


if __name__ == "__main__":
    sys.exit(main())
