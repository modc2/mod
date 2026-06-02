#!/usr/bin/env bash
# multistore — API via `m serve` (api-only suffix disables next dev auto-spawn)
# plus `next start` directly so we use the pre-built artifacts and surface any
# Next.js errors clearly.
set -e

API_PORT="${API_PORT:-50160}"
APP_PORT="${APP_PORT:-50161}"
APP_DIR="/root/mod/mod/orbit/multistore/app"
LOG_DIR="${LOG_DIR:-/tmp/multistore}"
mkdir -p "$LOG_DIR"

echo "── multistore ───────────────────────────────────────"
echo "  API  : 0.0.0.0:${API_PORT}"
echo "  APP  : 0.0.0.0:${APP_PORT}"
echo "  log  : ${LOG_DIR}"
echo "─────────────────────────────────────────────────────"

cleanup() {
    echo "shutting down…"
    kill ${API_PID:-} ${APP_PID:-} 2>/dev/null || true
    wait 2>/dev/null || true
}
trap cleanup INT TERM

# ── API: m serve with .api suffix skips Next auto-spawn ──
env PYTHONUNBUFFERED=1 \
    m serve port="$API_PORT" mod=multistore.api remote=0 \
    > "$LOG_DIR/api.log" 2>&1 &
API_PID=$!
echo "api  pid=${API_PID}"

# Quick readiness loop
for i in $(seq 1 30); do
    if curl -sf -X POST "http://localhost:${API_PORT}/info" > /dev/null 2>&1; then
        echo "api  ready"
        break
    fi
    sleep 1
done

# ── APP: next start (production) — fail loudly if build missing ──
cd "$APP_DIR"
if [ ! -d ".next" ] || [ ! -f ".next/BUILD_ID" ]; then
    echo "app  building (no .next/BUILD_ID found)…"
    if [ ! -d "node_modules" ]; then
        npm install --no-audit --no-fund > "$LOG_DIR/install.log" 2>&1
    fi
    npm run build > "$LOG_DIR/build.log" 2>&1 || {
        echo "app  build failed — see $LOG_DIR/build.log"
        tail -20 "$LOG_DIR/build.log"
        cleanup
        exit 1
    }
fi

env NEXT_PUBLIC_API_URL="http://localhost:${API_PORT}" \
    npx next start -p "$APP_PORT" -H 0.0.0.0 \
    > "$LOG_DIR/app.log" 2>&1 &
APP_PID=$!
echo "app  pid=${APP_PID}"

# Wait for whichever dies first, propagate exit.
wait -n
EXIT=$?
echo "process exited (${EXIT}); shutting down the other"
cleanup
exit "$EXIT"
