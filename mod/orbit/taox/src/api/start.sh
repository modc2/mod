#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"
PORT="${TAOX_PORT:-${PORT:-8870}}"

cd "$DIR"

BIN="$DIR/target/release/taox-api"
if [ ! -f "$BIN" ]; then
    echo "Building release binary..."
    cargo build --release || exit 1
fi

export TAOX_PORT="$PORT"
exec "$BIN"
