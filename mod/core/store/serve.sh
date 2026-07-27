#!/usr/bin/env bash
# Launch mod store under pm2: FastAPI gateway (store-api) + Next.js app (store-app).
# Usage: ./serve.sh [--no-app] [--no-api] [--prod] [stop|restart|logs|status]
set -e
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

API_PORT=${STORE_API_PORT:-50152}
APP_PORT=${STORE_APP_PORT:-50151}

only=""
action="start"
for arg in "$@"; do
  case $arg in
    --no-app) only="store-api" ;;
    --no-api) only="store-app" ;;
    --prod)   export STORE_MODE=prod ;;
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
  logs)    exec pm2 logs "${only:-store-api}" ;;
esac

# Ensure node deps before starting the app under pm2.
if [ "$only" != "store-api" ] && [ ! -d "$DIR/app/node_modules" ]; then
  echo "→ installing node deps (first run)…"
  (cd "$DIR/app" && npm install --no-audit --no-fund)
fi

if [ -n "$only" ]; then
  pm2 start ecosystem.config.js --only "$only"
else
  pm2 start ecosystem.config.js
fi
pm2 save >/dev/null 2>&1 || true

echo "→ API   http://localhost:$API_PORT   (pm2: store-api)"
echo "→ APP   http://localhost:$APP_PORT   (pm2: store-app)"
echo "  logs:   pm2 logs store-api | store-app"
echo "  stop:   ./serve.sh stop"
