#!/bin/bash
# Bring taox up with plain docker. One exposed port (3870):
#   /         → Next.js app
#   /app      → Next.js app (alias)
#   /api/...  → Rust API
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

NAME=taox
IMAGE=taox:latest
PORT="${TAOX_PORT:-3870}"

docker build -t "$IMAGE" .

if docker ps -a --format '{{.Names}}' | grep -qx "$NAME"; then
    docker rm -f "$NAME" >/dev/null
fi

# Persist generated deposit keys (and order store) across rebuilds. Keys are
# 0600 secrets — the host directory should be readable only by the operator.
DATA_DIR="${TAOX_DATA_DIR:-$HOME/.taox}"
mkdir -p "$DATA_DIR"
chmod 700 "$DATA_DIR" 2>/dev/null || true

docker run -d \
    --name "$NAME" \
    --restart unless-stopped \
    -p ${PORT}:3870 \
    -v "$DATA_DIR":/root/.taox \
    -e APP_PORT=3870 \
    -e API_PORT=8870 \
    -e APP_INTERNAL_PORT=3871 \
    -e TAOX_ETH_DEPOSIT="${TAOX_ETH_DEPOSIT:-}" \
    -e TAOX_SOL_DEPOSIT="${TAOX_SOL_DEPOSIT:-}" \
    -e TAOX_ETH_RPC="${TAOX_ETH_RPC:-}" \
    -e TAOX_SOL_RPC="${TAOX_SOL_RPC:-}" \
    -e TAOX_TAO_RPC="${TAOX_TAO_RPC:-}" \
    "$IMAGE"

echo ""
echo "  App:   http://localhost:${PORT}/"
echo "  API:   http://localhost:${PORT}/api/health"
echo "  Alias: http://localhost:${PORT}/app"
