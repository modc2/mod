#!/usr/bin/env bash
# Launch the lighthouse module under pm2: API (lighthouse-api) + console (lighthouse-app).
# Usage: ./serve.sh [--no-app] [--no-api] [stop|restart|logs|status]
set -e
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

API_PORT=${LIGHTHOUSE_API_PORT:-50680}
APP_PORT=${LIGHTHOUSE_APP_PORT:-50681}

only=""
action="start"
for arg in "$@"; do
  case $arg in
    --no-app) only="lighthouse-api" ;;
    --no-api) only="lighthouse-app" ;;
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
  logs)    exec pm2 logs "${only:-lighthouse-api}" ;;
esac

if [ -n "$only" ]; then
  pm2 start ecosystem.config.js --only "$only"
else
  pm2 start ecosystem.config.js
fi
pm2 save >/dev/null 2>&1 || true

echo "→ API   http://localhost:$API_PORT             (pm2: lighthouse-api)"
echo "→ APP   http://localhost:$APP_PORT/lighthouse  (pm2: lighthouse-app)"
echo "  logs:   pm2 logs lighthouse-api | lighthouse-app"
echo "  stop:   ./serve.sh stop"
