#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"
PORT="${PORT:-50630}"

cd "$DIR"

BIN="$DIR/target/release/prerank-api"
if [ ! -f "$BIN" ]; then
    echo "building release binary..."
    cargo build --release || exit 1
fi

export PORT

# The log is the module. Point it at the off-tree state dir so a redeploy
# never lands on a market that starts over from genesis.
export PRERANK_DIR="${PRERANK_DIR:-$HOME/.mod/prerank}"
mkdir -p "$PRERANK_DIR"

LOG="$DIR/api.log"
echo "api logging to $LOG"
exec "$BIN" >> "$LOG" 2>&1
