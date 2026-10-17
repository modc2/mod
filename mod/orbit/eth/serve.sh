#!/usr/bin/env bash
# Launch the eth module under pm2: API (eth-api) + console (eth-app).
# Usage: ./serve.sh [--no-app] [--no-api] [stop|restart|logs|status]
set -e
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

API_PORT=${ETH_API_PORT:-50730}
APP_PORT=${ETH_APP_PORT:-50731}

only=""
action="start"
for arg in "$@"; do
  case $arg in
    --no-app) only="eth-api" ;;
    --no-api) only="eth-app" ;;
    stop|restart|logs|status) action="$arg" ;;
  esac
done

if ! command -v pm2 >/dev/null 2>&1; then
  echo "pm2 not found — install with: npm i -g pm2" >&2
  exit 1
fi

case "$action" in
  stop)    exec pm2 stop    ecosystem.config.js ;;
  restart) exec pm2 restart ecosystem.config.js ;;
  status)  exec pm2 status ;;
  logs)    exec pm2 logs "${only:-eth-api}" ;;
esac

if [ -n "$only" ]; then
  pm2 start ecosystem.config.js --only "$only"
else
  pm2 start ecosystem.config.js
fi
pm2 save >/dev/null 2>&1 || true

echo "→ API   http://localhost:$API_PORT       (pm2: eth-api)"
echo "→ APP   http://localhost:$APP_PORT/eth   (pm2: eth-app)"
echo "  logs:   pm2 logs eth-api | eth-app"
echo "  stop:   ./serve.sh stop"
