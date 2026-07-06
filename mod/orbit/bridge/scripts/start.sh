#!/bin/bash
# Bridge — start API + App from config.json
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MOD_ROOT="$(cd "$DIR/../../.." && pwd)"
CONFIG="$DIR/config.json"

API_PORT=$(python3 -c "import json; print(json.load(open('$CONFIG')).get('port', 8840))")
APP_PORT=$(python3 -c "import json; print(json.load(open('$CONFIG')).get('app_port', 8841))")
API_URL=$(python3 -c "import json; print(json.load(open('$CONFIG')).get('urls', {}).get('api', 'http://localhost:$API_PORT'))")
APP_URL=$(python3 -c "import json; print(json.load(open('$CONFIG')).get('urls', {}).get('app', 'http://localhost:$APP_PORT'))")

export NEXT_PUBLIC_API_URL="$API_URL"
export PYTHONPATH="$MOD_ROOT:$DIR"

lsof -ti:$API_PORT | xargs kill -9 2>/dev/null || true

echo "Bridge API  $API_URL"
python3 -m uvicorn api:app --host 0.0.0.0 --port "$API_PORT" --app-dir "$DIR/api" --reload &

echo "Bridge App  $APP_URL"
cd "$DIR/app"
npx next dev -p "$APP_PORT" &

wait
