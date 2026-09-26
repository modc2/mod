#!/usr/bin/env bash
# Launch the logo module under pm2: API (logo-api) + console (logo-app).
# Usage: ./serve.sh [--no-app] [--no-api] [stop|restart|logs|status]
set -e
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

API_PORT=${LOGO_API_PORT:-50760}
APP_PORT=${LOGO_APP_PORT:-50761}

only=""
action="start"
for arg in "$@"; do
  case $arg in
    --no-app) only="logo-api" ;;
    --no-api) only="logo-app" ;;
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
  logs)    exec pm2 logs "${only:-logo-api}" ;;
esac

if [ -n "$only" ]; then
  pm2 start ecosystem.config.js --only "$only"
else
  pm2 start ecosystem.config.js
fi
pm2 save >/dev/null 2>&1 || true

echo "-> API   http://localhost:$API_PORT        (pm2: logo-api)"
echo "-> APP   http://localhost:$APP_PORT/logo   (pm2: logo-app)"
echo "  logs:   pm2 logs logo-api | logo-app"
echo "  stop:   ./serve.sh stop"
