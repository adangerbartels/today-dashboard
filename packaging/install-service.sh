#!/usr/bin/env bash
#
# Install Today as a systemd *user* service that starts at boot.
#
#   ./packaging/install-service.sh              install and start
#   ./packaging/install-service.sh --port 9000  on a different port
#   ./packaging/install-service.sh --uninstall  stop, disable, remove
#
# A user service is the right shape here: the config holds your personal API
# tokens under your own home directory, and the server only ever binds
# localhost. Nothing needs to run as root.
#
# The one catch is that user services normally stop when you log out. Starting
# at boot requires "lingering" to be enabled for your account, which is the only
# step that needs sudo. Without it the service still works, but only while
# you're logged in.

set -euo pipefail

UNIT_NAME="today.service"
HOST="127.0.0.1"
PORT="8787"
UNINSTALL=0

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE="$REPO/packaging/today.service"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNIT_PATH="$UNIT_DIR/$UNIT_NAME"

while [ $# -gt 0 ]; do
  case "$1" in
    --port) PORT="$2"; shift 2 ;;
    --host) HOST="$2"; shift 2 ;;
    --uninstall) UNINSTALL=1; shift ;;
    -h|--help) sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

die() { echo "error: $*" >&2; exit 1; }

command -v systemctl >/dev/null || die "systemd not found; see the README for other init systems"
systemctl --user is-system-running >/dev/null 2>&1 \
  || die "no systemd user session (is XDG_RUNTIME_DIR set? try logging in normally)"

if [ "$UNINSTALL" = 1 ]; then
  systemctl --user disable --now "$UNIT_NAME" 2>/dev/null || true
  rm -f "$UNIT_PATH"
  systemctl --user daemon-reload
  echo "Removed $UNIT_PATH"
  echo "Your config and data were left alone. Lingering, if you enabled it, is"
  echo "still on: turn it off with  sudo loginctl disable-linger $USER"
  exit 0
fi

[ -f "$TEMPLATE" ] || die "missing $TEMPLATE"

# Prefer an installed `today`; fall back to running from this checkout.
if command -v today >/dev/null 2>&1; then
  PYTHON="$(command -v today)"
  EXEC=""
else
  command -v python3 >/dev/null || die "python3 not found"
  # Resolve to the real interpreter, not a version-manager shim. A pyenv or asdf
  # shim would leave a long-running service silently following whatever the
  # global version happens to be, and break outright if the manager moved.
  PYTHON="$(python3 -c 'import sys; print(sys.executable)')"
  [ -x "$PYTHON" ] || PYTHON="$(command -v python3)"
  EXEC="$REPO/today.py"
fi
echo "Interpreter: $PYTHON"

# Where config.json, data/ and backups/ live. An existing config next to the
# checkout wins, so an established setup keeps working untouched.
if [ -f "$REPO/config.json" ]; then
  TODAY_HOME="$REPO"
else
  TODAY_HOME="${XDG_CONFIG_HOME:-$HOME/.config}/today-dashboard"
fi
mkdir -p "$TODAY_HOME"
chmod 700 "$TODAY_HOME"

mkdir -p "$UNIT_DIR"
sed -e "s|__PYTHON__|$PYTHON|g" \
    -e "s|__EXEC__|$EXEC|g" \
    -e "s|__DIR__|$REPO|g" \
    -e "s|__HOME__|$TODAY_HOME|g" \
    -e "s|__HOST__|$HOST|g" \
    -e "s|__PORT__|$PORT|g" \
    "$TEMPLATE" > "$UNIT_PATH"
# Collapse the double space left when EXEC is empty (installed-command case).
sed -i 's|ExecStart=\(.*\)  serve|ExecStart=\1 serve|' "$UNIT_PATH"

systemctl --user daemon-reload
systemctl --user enable --now "$UNIT_NAME"

# Survive logout and start at boot. The only privileged step.
LINGER="$(loginctl show-user "$USER" --property=Linger --value 2>/dev/null || echo no)"
if [ "$LINGER" != "yes" ]; then
  echo
  echo "Enabling lingering so the service starts at boot and outlives logout."
  if sudo -n loginctl enable-linger "$USER" 2>/dev/null; then
    echo "  done"
  elif sudo loginctl enable-linger "$USER"; then
    echo "  done"
  else
    echo "  SKIPPED — could not enable lingering."
    echo "  The service runs now, but will stop when you log out and will not"
    echo "  start at boot until you run:  sudo loginctl enable-linger $USER"
  fi
fi

echo
systemctl --user --no-pager --lines=0 status "$UNIT_NAME" || true
echo
echo "Today is running at http://$HOST:$PORT"
echo "  logs      journalctl --user -u $UNIT_NAME -f"
echo "  restart   systemctl --user restart $UNIT_NAME"
echo "  stop      systemctl --user stop $UNIT_NAME"
echo "  remove    $REPO/packaging/install-service.sh --uninstall"
