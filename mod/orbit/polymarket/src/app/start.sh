#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"
PORT="${PORT:-3091}"
API_PORT="${API_PORT:-50091}"

cd "$DIR"

[ ! -d node_modules ] && npm install --no-audit --no-fund

export NEXT_PUBLIC_API_URL="http://localhost:$API_PORT"
export NEXT_PUBLIC_BASE_PATH="/polymarket"
# Production mode (next start), NOT dev: `next dev` exposes an HMR websocket that
# crashes with an uncaughtException (WS_ERR_EXPECTED_MASK) when internet scanners
# send unmasked frames → endless pm2 restart loop (saw 33k). Prod has no HMR
# socket. Build on first run / when .next is missing.
if [ ! -f "$DIR/.next/BUILD_ID" ]; then
  bash "$DIR/build.sh"
fi
exec npx next start -p "$PORT"
