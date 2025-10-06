#!/usr/bin/env bash
set -euo pipefail

# Bootstrap the monorepo: initialize and update submodules, set up direnv

REPO_ROOT="$(cd "$(dirname "$0")"/.. && pwd)"
cd "$REPO_ROOT"


if ! command -v nix >/dev/null 2>&1; then
  sudo apt-get install nix-bin
fi

if ! command -v direnv >/dev/null 2>&1; then
  sudo apt-get install direnv
fi

if [ ! -f .envrc ]; then
  echo "use flake" > .envrc
  echo "watch_file flake.nix" >> .envrc
  echo "watch_file flake.lock" >> .envrc
  echo "[info] Wrote .envrc. Remember to run: direnv allow"
fi

if [ ! -f flake.nix ]; then
  echo "[warn] flake.nix not found. Please commit the flake before bootstrapping."
else
  echo "[info] Ensuring flake inputs are realized (this may download dependencies)..."
  sudo nix flake --extra-experimental-features 'nix-command flakes' show>/dev/null || true
fi

# Initialize submodules if needed
if [ -f .gitmodules ]; then
  echo "[info] Initializing and updating submodules..."
  git submodule update --init --recursive
else
  echo "[warn] .gitmodules not found. Add submodules with scripts/update-submodules.sh then re-run bootstrap."
fi

echo "[done] Bootstrap complete."
