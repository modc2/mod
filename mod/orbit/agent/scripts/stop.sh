#!/bin/bash
cd "$(dirname "$0")"

API_PORT=$(python3 -c "import json; c=json.load(open('config.json')); print(c.get('api',{}).get('port',50117))" 2>/dev/null || echo 50117)
APP_PORT=$(python3 -c "import json; c=json.load(open('config.json')); print(c.get('app',{}).get('port',3117))" 2>/dev/null || echo 3117)

KILLED=0

# kill API
for pid in $(pgrep -f "uvicorn.*api:app.*$API_PORT" 2>/dev/null); do
  kill "$pid" 2>/dev/null && echo "Killed API (pid $pid)" && KILLED=$((KILLED+1))
done

# kill App
for pid in $(pgrep -f "next.*dev.*$APP_PORT" 2>/dev/null) $(pgrep -f "next.*start.*$APP_PORT" 2>/dev/null); do
  kill "$pid" 2>/dev/null && echo "Killed App (pid $pid)" && KILLED=$((KILLED+1))
done

if [ "$KILLED" -eq 0 ]; then
  echo "No running services found."
else
  echo "Stopped $KILLED process(es)."
fi
