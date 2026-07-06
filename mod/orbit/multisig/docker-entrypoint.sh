#!/bin/bash
set -e

API_PORT=50100
APP_PORT=3100
GATEWAY_PORT="${GATEWAY_PORT:-3100}"
MODULE="multisig"

echo "── $MODULE docker ──"

# ── Start Rust API ──
PORT=$API_PORT /app/bin/multisig &
API_PID=$!
echo "API starting on :$API_PORT (pid $API_PID)"

for i in $(seq 1 30); do
    if curl -sf http://localhost:$API_PORT/health > /dev/null 2>&1; then
        echo "API ready"; break
    fi
    sleep 1
done

# ── Start Next.js ──
cd /app/app
NEXT_PUBLIC_API_URL="/api/$MODULE" \
NEXT_PUBLIC_BASE_PATH="/$MODULE" \
PORT=$APP_PORT \
npx next start -p $APP_PORT &
APP_PID=$!
echo "App starting on :$APP_PORT (pid $APP_PID)"
cd /app

for i in $(seq 1 30); do
    if curl -sf http://localhost:$APP_PORT > /dev/null 2>&1; then
        echo "App ready"; break
    fi
    sleep 1
done

# ── Caddyfile ──
cat > /app/Caddyfile <<EOF
{
    admin off
}

:${GATEWAY_PORT} {
    @api path /api/$MODULE /api/$MODULE/*
    handle @api {
        uri strip_prefix /api/$MODULE
        reverse_proxy localhost:${API_PORT}
    }
    handle /* {
        reverse_proxy localhost:${APP_PORT}
    }
}
EOF

caddy run --config /app/Caddyfile &
CADDY_PID=$!
echo "Gateway on :$GATEWAY_PORT (pid $CADDY_PID)"

echo ""
echo "  API:     http://localhost:$API_PORT/health"
echo "  App:     http://localhost:$APP_PORT"
echo "  Gateway: http://localhost:$GATEWAY_PORT"
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
