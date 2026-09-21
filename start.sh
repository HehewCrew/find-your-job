#!/usr/bin/env bash
# Find Your Job - start the web page. The first run sets everything up.
#   ./start.sh                 (macOS users can double-click start.command instead)
#   ./start.sh --port 9000     (any option of `python -m jobs.ui` passes through)
set -e
cd "$(dirname "$0")"

# --- 1. Python 3.10 or newer -----------------------------------------------------------
PY=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 \
    && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
    PY="$candidate"
    break
  fi
done
if [ -z "$PY" ]; then
  echo "Find Your Job needs Python 3.10 or newer, and this computer does not have it yet."
  echo
  echo "  macOS:          download it from https://www.python.org/downloads/"
  echo "  Ubuntu/Debian:  sudo apt install python3 python3-venv"
  echo "  Fedora:         sudo dnf install python3"
  echo
  echo "Then run this again."
  exit 1
fi

# --- 2. A private environment for the app, created once ------------------------------
if [ ! -x .venv/bin/python ]; then
  echo "Setting up Find Your Job for the first time. This takes a minute..."
  if ! "$PY" -m venv .venv; then
    rm -rf .venv
    echo
    echo "Python could not create its environment."
    echo "On Ubuntu/Debian that usually means: sudo apt install python3-venv"
    exit 1
  fi
fi

# --- 3. Install, again only when pyproject.toml has changed -----------------------------
if ! cmp -s pyproject.toml .venv/fyj-installed.toml; then
  echo "Installing..."
  .venv/bin/python -m pip install --quiet --disable-pip-version-check -e .
  cp pyproject.toml .venv/fyj-installed.toml
fi

# --- 4. Start the page ---------------------------------------------------------------
exec .venv/bin/python -m jobs.ui "$@"
