#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"
PORT="${PORT:-50631}"
API_PORT="${API_PORT:-50630}"

cd "$DIR"

[ ! -d node_modules ] && npm install --no-audit --no-fund

export PRERANK_API_URL="${PRERANK_API_URL:-http://127.0.0.1:$API_PORT}"
export NEXT_PUBLIC_BASE_PATH="${NEXT_PUBLIC_BASE_PATH:-/prerank}"

# Production mode, not `next dev`: dev exposes an HMR websocket that dies on
# unmasked frames from internet scanners and takes the process with it.
if [ ! -f "$DIR/.next/BUILD_ID" ]; then
  bash "$DIR/build.sh"
fi
exec npx next start -p "$PORT"
