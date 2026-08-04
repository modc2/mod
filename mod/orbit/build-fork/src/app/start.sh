#!/bin/bash
cd "$(dirname "$0")"

PORT="${1:-8895}"
API_PORT="${2:-8894}"

# Kill anything on the port
lsof -ti:"$PORT" | xargs kill -9 2>/dev/null || true
# Wait for port to free
for i in $(seq 1 10); do
    lsof -ti:"$PORT" &>/dev/null || break
    sleep 0.3
done

# Install deps if needed
if [ ! -d node_modules ]; then
    echo "Installing dependencies..."
    npm install || exit 1
fi

export NEXT_PUBLIC_API_PORT="$API_PORT"
echo "Starting app on port $PORT (API port: $API_PORT, fetches via /api/build-fork)..."
exec npx next dev -p "$PORT"
