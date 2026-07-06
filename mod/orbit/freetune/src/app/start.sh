#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"
PORT="${PORT:-50211}"
API_PORT="${API_PORT:-50210}"
cd "$DIR"
[ ! -d node_modules ] && npm install --no-audit --no-fund
export FREETUNE_API_URL="http://localhost:$API_PORT"
export NEXT_PUBLIC_BASE_PATH="/freetune"
# Production mode (next start) — next dev's HMR websocket crashes on unmasked
# frames from internet scanners (endless restart loop). Build on first run.
if [ ! -f "$DIR/.next/BUILD_ID" ]; then
  npx next build
fi
exec npx next start -p "$PORT"
