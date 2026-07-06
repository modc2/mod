#!/bin/bash
PORT="${PORT:-50091}"
pids=$(lsof -ti:"$PORT" 2>/dev/null)
if [ -n "$pids" ]; then
    echo "$pids" | xargs kill -9 2>/dev/null
    echo "API stopped (port $PORT)"
else
    echo "API not running (port $PORT)"
fi
