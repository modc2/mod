#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"
PORT="${PORT:-50091}"

cd "$DIR"

# build if no binary
BIN="$DIR/target/release/polymarket-api"
if [ ! -f "$BIN" ]; then
    echo "Building release binary..."
    cargo build --release || exit 1
fi

export PORT

# ── API log ──
# The backend logs via `tracing` to stdout; without a redirect those lines
# vanish when started in the background. Tee them to a logfile so live-engine
# cycles, fetch/429 errors, and order placements are inspectable after the
# fact (`tail -f src/api/api.log`). RUST_LOG can override the level, e.g.
# `RUST_LOG=polymarket_api=debug bash start.sh`.
LOG="$DIR/api.log"
echo "API logging to $LOG"
exec "$BIN" >> "$LOG" 2>&1
