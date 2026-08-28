#!/bin/bash
# Build + launch the build-fork module under pm2 (as root, for cross-module editing).
#
#   ./start.sh                # prod: build the Rust binary + Next app, then pm2 start
#   BUILD_FORK_MODE=dev ./start.sh # dev: skip the Next build, run `next dev`
#
# Run this as root so build-fork-api can edit sibling modules. Cross-module writes
# still require a per-operation sudo signature from the owner key — pm2/root only
# grants the *capability*; sudo.rs enforces *authorization*.
#
# (The per-service src/api/start.sh + src/app/start.sh remain for unauthenticated
#  host-only dev; this top-level script is the real pm2 deployment.)
set -euo pipefail
cd "$(dirname "$0")"

MODE="${BUILD_FORK_MODE:-prod}"
API_DIR="src/api"
APP_DIR="src/app"

echo "▸ building build-fork-api (release)…"
( cd "$API_DIR" && cargo build --release )

echo "▸ installing app deps…"
( cd "$APP_DIR" && npm install --no-audit --no-fund >/dev/null 2>&1 || npm install )

if [ "$MODE" = "prod" ]; then
  echo "▸ building Next app (prod bundle — avoids ChunkLoadError over the gateway)…"
  # build.sh builds into a fresh staging dir and swaps it in, so a rerun against
  # a live server never leaves it serving holes — and the staging dir is empty
  # every time, so the stale-partial-build /_document PageNotFoundError this
  # step used to `rm -rf .next` to avoid can't happen either.
  ( cd "$APP_DIR" && bash build.sh )
fi

echo "▸ (re)starting pm2 processes build-fork-api + build-fork-app…"
pm2 delete build-fork-api build-fork-app >/dev/null 2>&1 || true
BUILD_FORK_MODE="$MODE" pm2 start ecosystem.config.js

pm2 save >/dev/null 2>&1 || true
echo "✓ build-fork up — API :${BUILD_FORK_API_PORT:-8894}  APP :${BUILD_FORK_APP_PORT:-8895}  (pm2 ls)"
