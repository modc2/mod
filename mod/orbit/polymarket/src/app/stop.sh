#!/bin/bash
PORT="${PORT:-3091}"
pids=$(lsof -ti:"$PORT" 2>/dev/null)
if [ -n "$pids" ]; then
    echo "$pids" | xargs kill -9 2>/dev/null
    echo "App stopped (port $PORT)"
else
    echo "App not running (port $PORT)"
fi
