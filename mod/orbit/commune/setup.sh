#!/usr/bin/env bash
# Install commune (github.com/commune-ai/commune).
#
# Usage:
#   bash setup.sh                # install to ~/commune
#   bash setup.sh /path/to/dir   # install to /path/to/dir
#   COMMUNE_REPO=... bash setup.sh   # override source repo

set -euo pipefail

REPO="${COMMUNE_REPO:-https://github.com/commune-ai/commune}"
DEST="${1:-$HOME/commune}"

echo "[commune] target: $DEST"
echo "[commune] repo:   $REPO"

# 1. system deps
need() { command -v "$1" >/dev/null 2>&1; }

if ! need git;    then echo "[commune] error: git not installed"    >&2; exit 1; fi
if ! need python3; then echo "[commune] error: python3 not installed" >&2; exit 1; fi
if ! need pip3;   then echo "[commune] error: pip3 not installed"   >&2; exit 1; fi

# 2. clone or update
if [ -d "$DEST/.git" ]; then
  echo "[commune] updating existing checkout..."
  git -C "$DEST" pull --ff-only
else
  echo "[commune] cloning..."
  mkdir -p "$(dirname "$DEST")"
  git clone --depth 1 "$REPO" "$DEST"
fi

# 3. install
echo "[commune] installing python package..."
cd "$DEST"
pip3 install -e . --break-system-packages 2>/dev/null || pip3 install -e .

# 4. verify
if need c; then
  echo "[commune] installed: $(c --version 2>/dev/null || echo ok)"
elif python3 -c "import commune" 2>/dev/null; then
  echo "[commune] installed (python module 'commune' importable)"
else
  echo "[commune] warning: install completed but 'c' / 'commune' not found on PATH" >&2
  exit 1
fi

echo "[commune] done."
