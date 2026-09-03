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

# ── Allocator ──
# The hourly warmup parses thousands of trader-activity pages concurrently;
# with glibc's default one-arena-per-thread the burst spreads across ~150
# arenas that never shrink, pinning RSS at the high-water mark (~11GB
# observed while live data was <200MB). Two arenas keep the burst confined
# and let the post-cycle malloc_trim in main.rs actually return it.
export MALLOC_ARENA_MAX=2

# ── Persistent data dir ──
# Signer keystore, live-engine sessions, and user strats all resolve from
# POLYMARKET_DATA_DIR and fall back to /tmp when unset — which loses wallet
# keys and silently reverts live sessions to DRY RUN after a reboot/tmp
# clean. Pin it to the module's off-tree state dir.
export POLYMARKET_DATA_DIR="${POLYMARKET_DATA_DIR:-$HOME/.mod/polymarket}"
mkdir -p "$POLYMARKET_DATA_DIR"

# ── Scheduled liquidation ("flatten everything") ──
# How often the backend sells EVERY position held in the deposit wallet, in
# hours. OFF (0) by default and deliberately opt-in: a pass sells the whole
# on-chain book at best bid, including positions the copy engine never bought,
# so it must never be something a stock deployment does to a real wallet on a
# timer. Set POLYMARKET_LIQUIDATE_EVERY_HOURS=6 before launch to enable it.
# Even then it only touches wallets with a RUNNING session that has
# auto_execute on (see EngineRegistry::persisted_eoas).
export POLYMARKET_LIQUIDATE_EVERY_HOURS="${POLYMARKET_LIQUIDATE_EVERY_HOURS:-0}"

# ── API log ──
# The backend logs via `tracing` to stdout; without a redirect those lines
# vanish when started in the background. Tee them to a logfile so live-engine
# cycles, fetch/429 errors, and order placements are inspectable after the
# fact (`tail -f src/api/api.log`). RUST_LOG can override the level, e.g.
# `RUST_LOG=polymarket_api=debug bash start.sh`.
LOG="$DIR/api.log"
echo "API logging to $LOG"
exec "$BIN" >> "$LOG" 2>&1
