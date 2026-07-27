#!/bin/bash
cd "$(dirname "$0")"

PORT="${1:-8820}"

# Kill anything on the port
lsof -ti:"$PORT" | xargs kill -9 2>/dev/null || true
# Wait for port to free
for i in $(seq 1 10); do
    lsof -ti:"$PORT" &>/dev/null || break
    sleep 0.3
done

# Build if no binary
if [ ! -f target/release/claude-jobs ]; then
    echo "Building API (release)..."
    cargo build --release || exit 1
fi

export CLAUDE_JOBS_LOCAL=1
echo "Starting API on port $PORT..."
exec ./target/release/claude-jobs "$PORT"
