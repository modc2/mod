#!/bin/bash
# Build + launch the claude module under pm2 (as root, for cross-module editing).
#
#   ./serve.sh                 # prod: build the Rust binary + Next app, then pm2 start
#   CLAUDE_MODE=dev ./serve.sh # dev: skip the Next build, run `next dev`
#
# Run this as root so claude-api can edit sibling modules. Cross-module writes
# still require a per-operation sudo signature from the owner key — pm2/root only
# grants the *capability*; sudo.rs enforces *authorization*.
set -euo pipefail
cd "$(dirname "$0")"

MODE="${CLAUDE_MODE:-prod}"
API_DIR="src/api"
APP_DIR="src/app"

echo "▸ building claude-api (release)…"
( cd "$API_DIR" && cargo build --release )

echo "▸ installing app deps…"
( cd "$APP_DIR" && npm install --no-audit --no-fund >/dev/null 2>&1 || npm install )

if [ "$MODE" = "prod" ]; then
  echo "▸ building Next app (prod bundle — avoids ChunkLoadError over the gateway)…"
  # Wipe .next first: a stale partial build trips a /_document PageNotFoundError
  # during page-data collection.
  ( cd "$APP_DIR" && rm -rf .next && NEXT_PUBLIC_BASE_PATH="${NEXT_PUBLIC_BASE_PATH:-/claude}" npx next build )
fi

echo "▸ (re)starting pm2 processes claude-api + claude-app…"
pm2 delete claude-api claude-app >/dev/null 2>&1 || true
CLAUDE_MODE="$MODE" pm2 start ecosystem.config.js

pm2 save >/dev/null 2>&1 || true
echo "✓ claude up — API :${CLAUDE_API_PORT:-8820}  APP :${CLAUDE_APP_PORT:-8823}  (pm2 ls)"
