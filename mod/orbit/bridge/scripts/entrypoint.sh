#!/bin/bash
# Bridge container entrypoint — runs API + Next.js app
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MOD_ROOT="${MOD_ROOT:-/root/mod}"

export PYTHONPATH="${MOD_ROOT}:${DIR}:${PYTHONPATH}"

API_PORT="${API_PORT:-8840}"
APP_PORT="${APP_PORT:-8841}"
DEV="${DEV:-0}"

# Persist claims/commitments in ./data/ (mounted from host via .)
mkdir -p "${DIR}/data"
ln -sfn "${DIR}/data" /root/.bridge

# Ensure node_modules exist (named volume may be empty on first run)
if [ ! -f "${DIR}/app/node_modules/.package-lock.json" ]; then
    echo "Installing node dependencies..."
    cd "${DIR}/app" && npm install
fi

echo "Bridge API  http://0.0.0.0:${API_PORT}"
echo "Bridge App  http://0.0.0.0:${APP_PORT}"
echo "Mode: $([ "$DEV" = "1" ] && echo dev || echo prod)"

if [ "$DEV" = "1" ]; then
    python3 -m uvicorn api:app --host 0.0.0.0 --port "${API_PORT}" --app-dir "${DIR}/api" --reload &
    cd "${DIR}/app" && npx next dev -p "${APP_PORT}" &
else
    export NEXT_PUBLIC_API_URL="${NEXT_PUBLIC_API_URL:-https://modc2.com/api/bridge}"
    export NEXT_PUBLIC_APP_URL="${NEXT_PUBLIC_APP_URL:-https://modc2.com/bridge}"

    python3 -m uvicorn api:app --host 0.0.0.0 --port "${API_PORT}" --app-dir "${DIR}/api" &
    # Build if no .next output yet
    if [ ! -d "${DIR}/app/.next" ]; then
        cd "${DIR}/app" && npx next build
    fi
    cd "${DIR}/app" && npx next start -p "${APP_PORT}" &
fi

wait
