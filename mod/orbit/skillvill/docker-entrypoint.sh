#!/bin/bash
set -e

API_PORT=50140
GATEWAY_PORT="${GATEWAY_PORT:-3140}"
MODULE="skillvill"

echo "── $MODULE docker ──"

# ── Start m serve ──
m serve port=$API_PORT key=$MODULE remote=0 mod=$MODULE &
API_PID=$!
echo "API starting on :$API_PORT (pid $API_PID)"

for i in $(seq 1 30); do
    if curl -sf http://localhost:$API_PORT/health > /dev/null 2>&1; then
        echo "API ready"; break
    fi
    sleep 1
done

# ── Caddyfile ──
cat > /tmp/Caddyfile <<EOF
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
        reverse_proxy localhost:${API_PORT}
    }
}
EOF

caddy run --config /tmp/Caddyfile &
CADDY_PID=$!
echo "Gateway on :$GATEWAY_PORT (pid $CADDY_PID)"

echo ""
echo "  API:     http://localhost:$API_PORT/health"
echo "  Gateway: http://localhost:$GATEWAY_PORT"
echo ""

cleanup() {
    echo "shutting down..."
    kill $CADDY_PID $API_PID 2>/dev/null
    wait $CADDY_PID $API_PID 2>/dev/null
}
trap cleanup SIGTERM SIGINT

wait -n $API_PID $CADDY_PID
EXIT_CODE=$?
echo "process exited with code $EXIT_CODE — stopping all"
cleanup
exit $EXIT_CODE
