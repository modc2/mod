#!/bin/bash
# Entrypoint for dev orbit module. Starts the FastAPI proxy + Next.js app.
# The proxy forwards /api/proxy/{provider}/{path} to the corresponding
# claude/codex backend container over the modnet bridge.
set -e

API_PORT="${API_PORT:-8870}"
APP_PORT="${APP_PORT:-8871}"

PRIVATE_DIR="/home/node/.mod/dev"
mkdir -p "$PRIVATE_DIR"

API_DIR=/app/src/api
APP_DIR=/app/src/app

if [ -f "$API_DIR/main.py" ]; then
    cd "$API_DIR"
    PORT=$API_PORT python3 -m uvicorn main:app --host 0.0.0.0 --port "$API_PORT" &
    API_PID=$!
    echo "dev proxy API on :$API_PORT (pid $API_PID)"
fi

if [ -f "$APP_DIR/package.json" ] && [ -d "$APP_DIR/.next" ]; then
    cd "$APP_DIR"
    NEXT_PUBLIC_API_URL="/api/dev" \
    NEXT_PUBLIC_BASE_PATH="/dev" \
    PORT=$APP_PORT \
    npx next start -p "$APP_PORT" -H 0.0.0.0 &
    APP_PID=$!
    echo "dev next.js app on :$APP_PORT (pid $APP_PID)"
fi

cleanup() {
    echo "shutting down..."
    kill "${APP_PID:-}" "${API_PID:-}" 2>/dev/null || true
    wait 2>/dev/null || true
}
trap cleanup SIGTERM SIGINT

wait
