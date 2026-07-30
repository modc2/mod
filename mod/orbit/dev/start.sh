#!/bin/bash
# Build + launch the build module under pm2 (as root, for cross-module editing).
#
#   ./start.sh                # prod: build the Rust binary + Next app, then pm2 start
#   DEV_MODE=dev ./start.sh # dev: skip the Next build, run `next dev`
#
# Run this as root so dev-api can edit sibling modules. Cross-module writes
# still require a per-operation sudo signature from the owner key — pm2/root only
# grants the *capability*; sudo.rs enforces *authorization*.
#
# (The per-service src/api/start.sh + src/app/start.sh remain for unauthenticated
#  host-only dev; this top-level script is the real pm2 deployment.)
set -euo pipefail
cd "$(dirname "$0")"

MODE="${DEV_MODE:-prod}"
API_DIR="src/api"
APP_DIR="src/app"

echo "▸ building dev-api (release)…"
( cd "$API_DIR" && cargo build --release )

echo "▸ installing app deps…"
( cd "$APP_DIR" && npm install --no-audit --no-fund >/dev/null 2>&1 || npm install )

if [ "$MODE" = "prod" ]; then
  echo "▸ building Next app (prod bundle — avoids ChunkLoadError over the gateway)…"
  # Wipe .next first: a stale partial build trips a /_document PageNotFoundError
  # during page-data collection.
  ( cd "$APP_DIR" && rm -rf .next && NEXT_PUBLIC_BASE_PATH="${NEXT_PUBLIC_BASE_PATH:-/dev}" npx next build )
fi

echo "▸ (re)starting pm2 processes dev-api + dev-app…"
pm2 delete dev-api dev-app >/dev/null 2>&1 || true
DEV_MODE="$MODE" pm2 start ecosystem.config.js

pm2 save >/dev/null 2>&1 || true
echo "✓ build up — API :${DEV_API_PORT:-8870}  APP :${DEV_APP_PORT:-8871}  (pm2 ls)"
