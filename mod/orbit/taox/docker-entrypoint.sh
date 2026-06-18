#!/bin/bash
set -e

APP_PORT="${APP_PORT:-3870}"           # exposed (Caddy)
API_PORT="${API_PORT:-8870}"           # internal Rust API
APP_INTERNAL_PORT="${APP_INTERNAL_PORT:-3871}"  # internal Next.js

echo "── taox docker ──"

# ── Start Rust API (internal) ──
TAOX_PORT=$API_PORT PORT=$API_PORT /app/bin/taox-api &
API_PID=$!
echo "API on :$API_PORT (pid $API_PID, internal)"

for i in $(seq 1 30); do
    if curl -sf http://localhost:$API_PORT/health > /dev/null 2>&1; then
        echo "API ready"
        break
    fi
    sleep 1
done

# ── Start Next.js (internal) ──
cd /app/src/app
TAOX_API_URL="http://localhost:$API_PORT" \
NEXT_PUBLIC_API_URL="/api/taox" \
NEXT_PUBLIC_BASE_PATH="/taox" \
PORT=$APP_INTERNAL_PORT \
HOSTNAME="0.0.0.0" \
npx next start -p $APP_INTERNAL_PORT -H 0.0.0.0 &
APP_PID=$!
echo "App on :$APP_INTERNAL_PORT (pid $APP_PID, internal)"
cd /app

# ── Start Caddy (the single exposed port) ──
APP_PORT="$APP_PORT" API_PORT="$API_PORT" APP_INTERNAL_PORT="$APP_INTERNAL_PORT" \
caddy run --config /etc/caddy/Caddyfile --adapter caddyfile &
CADDY_PID=$!
echo "Caddy on :$APP_PORT (pid $CADDY_PID, exposed)"

echo ""
echo "  Direct:        http://localhost:$APP_PORT/  (redirects to /taox/)"
echo "  Direct API:    http://localhost:$APP_PORT/api/health"
echo "  Direct app:    http://localhost:$APP_PORT/taox/  (or /app)"
echo "  Gateway app:   http://localhost:3000/taox/"
echo "  Gateway API:   http://localhost:3000/api/taox/health"
echo ""

cleanup() {
    echo "shutting down..."
    kill $CADDY_PID $APP_PID $API_PID 2>/dev/null
    wait $CADDY_PID $APP_PID $API_PID 2>/dev/null
}
trap cleanup SIGTERM SIGINT

wait -n $API_PID $APP_PID $CADDY_PID
EXIT_CODE=$?
echo "process exited with code $EXIT_CODE — stopping all"
cleanup
exit $EXIT_CODE
