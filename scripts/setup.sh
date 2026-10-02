#!/usr/bin/env bash
# Scraping Eagle · setup
# Creates an isolated environment with the fetch engine and a browser, and links the `scrape` command.
# Idempotent: safe to run as many times as needed.
#
#   bash setup.sh                 install whatever is missing
#   bash setup.sh --upgrade       upgrade the engine, yt-dlp, feedparser and the browser
#   bash setup.sh --no-browsers   HTTP tier only (no Chromium, ~40 MB)
#   bash setup.sh --no-link       do not create the ~/.local/bin/scrape link
set -euo pipefail

HOME_DIR="${SCRAPING_EAGLE_HOME:-$HOME/.scraping-eagle}"
VENV="$HOME_DIR/venv"
SKILL_DIR="$(cd -P "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN_DIR="${SCRAPING_EAGLE_BIN:-$HOME/.local/bin}"
MIN_ENGINE="0.4.15"

UPGRADE=0; LINK=1; BROWSERS=1
for arg in "$@"; do
  case "$arg" in
    --upgrade) UPGRADE=1 ;;
    --no-link) LINK=0 ;;
    --no-browsers) BROWSERS=0 ;;
    -h|--help) sed -n '2,10p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $arg" >&2; exit 64 ;;
  esac
done

say() { printf '[setup] %s\n' "$*"; }

mkdir -p "$HOME_DIR"
chmod 700 "$HOME_DIR"

# 1) virtual environment ------------------------------------------------------
if [ ! -x "$VENV/bin/python" ]; then
  if command -v uv >/dev/null 2>&1; then
    # 3.13 is the newest Python the engine declares support for; uv downloads it if missing.
    say "Creating venv with uv (Python 3.13) in $VENV"
    uv venv --quiet --python 3.13 "$VENV" || uv venv --quiet --python 3.12 "$VENV"
  else
    PY=""
    for c in python3.13 python3.12 python3.11 python3.10; do
      command -v "$c" >/dev/null 2>&1 && { PY="$c"; break; }
    done
    [ -n "$PY" ] || { echo "Python 3.10–3.13 or uv (https://docs.astral.sh/uv/) is required" >&2; exit 1; }
    say "Creating venv with $PY in $VENV"
    "$PY" -m venv "$VENV"
  fi
else
  say "venv already exists: $VENV"
fi

pip_install() {
  if command -v uv >/dev/null 2>&1; then
    uv pip install --quiet --python "$VENV/bin/python" "$@"
  else
    "$VENV/bin/python" -m pip install --quiet "$@"
  fi
}

# 2) packages -----------------------------------------------------------------
# The CLI relies on engine internals: do not cross a minor version without running the tests.
PKGS=("scrapling[all]>=$MIN_ENGINE,<0.5" "yt-dlp[default]" "feedparser")
if [ "$UPGRADE" -eq 1 ]; then
  say "Upgrading packages"
  pip_install --upgrade "${PKGS[@]}"
else
  say "Installing packages"
  pip_install "${PKGS[@]}"
fi

# 3) browser ------------------------------------------------------------------
# Called through the interpreter: console scripts carry an absolute path and break if the folder moves.
engine_install() {
  "$VENV/bin/python" -c "import sys; from scrapling.cli import main; sys.argv = ['scrapling', 'install', *sys.argv[1:]]; main()" "$@"
}
if [ "$BROWSERS" -eq 1 ]; then
  say "Installing Chromium for the browser tiers (downloads only if missing)"
  if [ "$UPGRADE" -eq 1 ]; then
    engine_install --force
  else
    engine_install
  fi
fi

# 4) link on PATH -------------------------------------------------------------
if [ "$LINK" -eq 1 ]; then
  mkdir -p "$BIN_DIR"
  TARGET="$SKILL_DIR/scripts/scrape"
  if [ -e "$BIN_DIR/scrape" ] && [ ! -L "$BIN_DIR/scrape" ]; then
    say "WARNING: $BIN_DIR/scrape exists and is not a link; leaving it alone."
  else
    ln -sfn "$TARGET" "$BIN_DIR/scrape"
    say "Command linked: $BIN_DIR/scrape -> $TARGET"
    case ":$PATH:" in *":$BIN_DIR:"*) ;; *) say "WARNING: $BIN_DIR is not on PATH; use the full path." ;; esac
  fi
fi

# 5) stamp --------------------------------------------------------------------
"$VENV/bin/python" - "$HOME_DIR/setup.json" "$BROWSERS" <<'PY'
import json, sys, time
from importlib.metadata import version, PackageNotFoundError

def v(name):
    try:
        return version(name)
    except PackageNotFoundError:
        return None

info = {
    "installed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    "python": sys.version.split()[0],
    "engine": v("scrapling"),
    "yt_dlp": v("yt-dlp"),
    "feedparser": v("feedparser"),
    "browsers": sys.argv[2] == "1",
}
with open(sys.argv[1], "w") as f:
    json.dump(info, f, indent=2)
print("[setup] OK · " + " · ".join(f"{k}={val}" for k, val in info.items() if k != "installed_at"))
PY
