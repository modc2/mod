#!/bin/bash
PORT="${TAOX_PORT:-${PORT:-8870}}"
pids=$(lsof -ti:"$PORT" 2>/dev/null)
if [ -n "$pids" ]; then
    echo "$pids" | xargs kill -9 2>/dev/null
    echo "taox-api stopped (port $PORT)"
else
    echo "taox-api not running (port $PORT)"
fi
