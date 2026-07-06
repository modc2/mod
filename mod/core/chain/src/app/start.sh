#!/usr/bin/env bash
# Chain Hub app launcher (pm2 / standalone).
# Honors $PORT (default 8801), $NEXT_PUBLIC_API_URL, and $DEV (1=dev, 0=start).
set -e
cd "$(dirname "$0")"
PORT="${PORT:-8801}"
if [ ! -d node_modules ]; then
  npm install --no-audit --no-fund
fi
if [ "${DEV:-1}" = "1" ]; then
  exec npx next dev -p "$PORT"
else
  npx next build
  exec npx next start -p "$PORT"
fi
