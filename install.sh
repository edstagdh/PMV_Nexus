#!/usr/bin/env bash
# PMV Nexus -- install on Linux / macOS (2.26.0). Basic, in the console:
# Python packages in .venv, the Playwright Firefox (site logins), a check for ffmpeg,
# then a new library (folders + port) or one from a backup zip, and optionally a
# systemd user service so it runs in the background and starts with the computer.
# Safe to run again (updates the packages). Windows: use the Setup.exe from Releases.
set -euo pipefail
cd "$(dirname "$0")"
APP_DIR="$(pwd)"

say() { printf '\n%s\n' "$*"; }
ask() { local a; read -r -p "$1" a || true; printf '%s' "$a"; }

say "PMV Nexus - install"

# ---- 1. Python 3.10+ ---------------------------------------------------------
PY=""
for c in python3.12 python3.13 python3.11 python3.10 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
    PY="$c"; break
  fi
done
if [ -z "$PY" ]; then
  say "Python 3.10 or newer wasn't found. Install it, then run ./install.sh again:"
  echo "  Debian/Ubuntu:  sudo apt install python3 python3-venv python3-pip"
  echo "  Fedora:         sudo dnf install python3"
  echo "  macOS:          brew install python@3.12"
  exit 1
fi
echo "[1/5] Python: $("$PY" --version)"

# ---- 2. .venv + packages ------------------------------------------------------
if [ ! -x .venv/bin/python ]; then
  echo "[2/5] Creating the Python environment (.venv) ..."
  "$PY" -m venv .venv || { say "Creating .venv failed. On Debian/Ubuntu: sudo apt install python3-venv"; exit 1; }
else
  echo "[2/5] Using the existing .venv"
fi
VPY="$APP_DIR/.venv/bin/python"
"$VPY" -m pip install --disable-pip-version-check --quiet --upgrade pip
echo "      Installing the Python packages - a few minutes the first time ..."
"$VPY" -m pip install --disable-pip-version-check --quiet -r requirements.txt

# ---- 3. Playwright's Firefox (into runtime/, used by run.py) --------------------
echo "[3/5] Installing the browser used for site logins ..."
export PLAYWRIGHT_BROWSERS_PATH="$APP_DIR/runtime/ms-playwright"
if ! "$VPY" -m playwright install firefox; then
  echo "      It failed. Some systems need extra libraries: sudo $VPY -m playwright install-deps firefox"
fi

# ---- 4. ffmpeg -------------------------------------------------------------------
if command -v ffmpeg >/dev/null 2>&1; then
  echo "[4/5] ffmpeg found"
else
  echo "[4/5] ffmpeg was NOT found. Re-encoding, previews and some downloads need it:"
  echo "      sudo apt install ffmpeg   (or dnf / pacman / brew install ffmpeg). The app starts without it."
fi

# ---- 5. New library or from a backup ---------------------------------------------
say "[5/5] Set up from a backup? A .zip from the Backups page of an earlier install brings back the"
echo "      library, users, playlists and settings. Videos aren't in backups: copy the video folder yourself."
BACKUP="$(ask '      Backup zip (path, or just Enter for a new library): ')"
BACKUP="${BACKUP%\"}"; BACKUP="${BACKUP#\"}"; BACKUP="${BACKUP%\'}"; BACKUP="${BACKUP#\'}"
if [ -n "$BACKUP" ]; then
  "$VPY" -m Tools.first_install --check-backup "$BACKUP"
  # Restores it now: asks for its password if it has one, then for the folders.
  "$VPY" run.py --restore "$BACKUP" --no-start
else
  "$VPY" -m Tools.first_install --folders
  PORT="$(ask '      Port for the app (Enter = 8676): ')"
  "$VPY" -m Tools.first_install --port "${PORT:-8676}"
fi

# ---- How it runs -------------------------------------------------------------------
SERVICE=""
if command -v systemctl >/dev/null 2>&1 && [ "$(uname)" = "Linux" ]; then
  say "Run it in the background as a systemd user service (starts with the computer)?"
  SERVICE="$(ask '      [y/N]: ')"
fi
if [[ "$SERVICE" =~ ^[Yy] ]]; then
  UNIT_DIR="$HOME/.config/systemd/user"
  mkdir -p "$UNIT_DIR"
  cat > "$UNIT_DIR/pmv-nexus.service" <<EOF
[Unit]
Description=PMV Nexus
After=network-online.target

[Service]
WorkingDirectory=$APP_DIR
ExecStart=$VPY -u $APP_DIR/run.py
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
EOF
  systemctl --user daemon-reload
  systemctl --user enable --now pmv-nexus.service
  # Without this, user services stop when you log out (and don't start at boot).
  loginctl enable-linger "$USER" 2>/dev/null || echo "      (to keep it running while logged out: sudo loginctl enable-linger $USER)"
  say "Installed and running as a service: systemctl --user status pmv-nexus"
  echo "Log: journalctl --user -u pmv-nexus -f"
  exit 0
fi

say "Installed. Start it any time with:  .venv/bin/python run.py"
START="$(ask 'Start it now? [Y/n]: ')"
if [[ ! "$START" =~ ^[Nn] ]]; then
  exec "$VPY" run.py --open-browser
fi
