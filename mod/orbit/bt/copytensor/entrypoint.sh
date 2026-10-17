#!/bin/bash
set -e

PORT=${PORT:-50150}
APP_PORT=${APP_PORT:-3150}

# Supervisor loop: restart the Rust API whenever it exits (Bittensor RPCs
# sometimes drop the WebSocket — we want the API to come right back instead
# of taking down the whole container and waiting for docker's restart).
api_supervisor() {
    cd /app
    while true; do
        echo "starting copytensor-api on port $PORT..."
        ./copytensor-api || true
        echo "copytensor-api exited; restarting in 3s..."
        sleep 3
    done
}

api_supervisor &
SUPERVISOR_PID=$!

# Wait for API to be ready (give the first WS connect ~30s).
for i in $(seq 1 30); do
    if curl -sf "http://localhost:$PORT/health" > /dev/null 2>&1; then
        echo "api ready"
        break
    fi
    sleep 1
done

# Start Next.js frontend
echo "starting frontend on port $APP_PORT..."
cd /app/frontend
NEXT_PUBLIC_API_URL="http://localhost:$PORT" npx next start -p "$APP_PORT" &
NEXT_PID=$!

echo "copytensor running — api=$PORT app=$APP_PORT"

# Stay alive as long as the frontend or supervisor are alive.
wait -n $SUPERVISOR_PID $NEXT_PID
