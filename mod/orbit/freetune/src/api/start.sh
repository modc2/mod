#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"
PORT="${PORT:-50210}"
cd "$DIR"
export FREETUNE_TRAINER_DIR="${FREETUNE_TRAINER_DIR:-$(cd "$DIR/../.." && pwd)}"
BIN="$DIR/target/release/freetune-api"
if [ ! -f "$BIN" ]; then
  echo "Building release binary..."
  cargo build --release || exit 1
fi
export PORT
LOG="$DIR/api.log"
echo "freetune-api logging to $LOG (trainer_dir=$FREETUNE_TRAINER_DIR)"
exec "$BIN" >> "$LOG" 2>&1
