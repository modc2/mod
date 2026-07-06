#!/bin/bash
cd "$(dirname "$0")"

# load ports from config.json
API_PORT=$(python3 -c "import json; c=json.load(open('config.json')); print(c.get('api',{}).get('port',50117))" 2>/dev/null || echo 50117)
APP_PORT=$(python3 -c "import json; c=json.load(open('config.json')); print(c.get('app',{}).get('port',3117))" 2>/dev/null || echo 3117)

# stop any existing instances
bash stop.sh 2>/dev/null

LOG_DIR="/tmp/agent"
mkdir -p "$LOG_DIR"

# start API
cd src/api
PYTHONPATH="$(cd ../.. && pwd):$(cd .. && pwd)" \
  python3 -m uvicorn api:app --host 0.0.0.0 --port "$API_PORT" --reload \
  > "$LOG_DIR/api.log" 2>&1 &
API_PID=$!
cd ../..

# start App
cd src/app
if [ ! -d "node_modules" ]; then
  npm install
fi
NEXT_PUBLIC_API_URL="http://localhost:$API_PORT" PORT="$APP_PORT" \
  npx next dev -p "$APP_PORT" \
  > "$LOG_DIR/app.log" 2>&1 &
APP_PID=$!
cd ../..

trap "kill $API_PID $APP_PID 2>/dev/null" EXIT

echo "API:  http://localhost:$API_PORT"
echo "App:  http://localhost:$APP_PORT"
echo "Logs: $LOG_DIR"
echo "Press Ctrl+C to stop."

wait
