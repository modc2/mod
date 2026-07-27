#!/bin/bash
PORT="${1:-8821}"

pids=$(lsof -ti:"$PORT" 2>/dev/null || true)
if [ -n "$pids" ]; then
    echo "Stopping App on port $PORT (PIDs: $pids)"
    echo "$pids" | xargs kill 2>/dev/null || true
    sleep 0.5
    # Force kill stragglers
    pids=$(lsof -ti:"$PORT" 2>/dev/null || true)
    [ -n "$pids" ] && echo "$pids" | xargs kill -9 2>/dev/null || true
else
    echo "No App running on port $PORT"
fi
