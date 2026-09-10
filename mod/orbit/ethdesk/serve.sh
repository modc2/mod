#!/usr/bin/env bash
# Launch the eth module under pm2: API (ethdesk-api) + console (ethdesk-app).
# Usage: ./serve.sh [--no-app] [--no-api] [stop|restart|logs|status]
set -e
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

API_PORT=${ETHDESK_API_PORT:-50750}
APP_PORT=${ETHDESK_APP_PORT:-50751}

only=""
action="start"
for arg in "$@"; do
  case $arg in
    --no-app) only="ethdesk-api" ;;
    --no-api) only="ethdesk-app" ;;
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
  logs)    exec pm2 logs "${only:-ethdesk-api}" ;;
esac

if [ -n "$only" ]; then
  pm2 start ecosystem.config.js --only "$only"
else
  pm2 start ecosystem.config.js
fi
pm2 save >/dev/null 2>&1 || true

echo "→ API   http://localhost:$API_PORT       (pm2: ethdesk-api)"
echo "→ APP   http://localhost:$APP_PORT/ethdesk   (pm2: ethdesk-app)"
echo "  logs:   pm2 logs ethdesk-api | ethdesk-app"
echo "  stop:   ./serve.sh stop"
