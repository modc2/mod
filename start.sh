#!/bin/bash
set -e

MOD_DIR="${MOD_DIR:-$HOME/mod}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---- Docker mode ----
if [ "$1" = "--docker" ]; then
    echo "=== mod start (docker) ==="
    # One path only: docker-compose. The hand-rolled `docker run` that used to
    # live here published :3000 (the host's own gateway port) and mounted the
    # host's ~/.mod, so the sandbox fought the fleet for both the port and the
    # registry. See docker-compose.yml for what it publishes instead.
    docker network inspect modnet >/dev/null 2>&1 || docker network create modnet

    cd "$SCRIPT_DIR" || exit 1
    docker compose build
    docker compose up -d

    echo "[ok] sandbox running"
    docker compose ps
    echo
    echo "  shell   : docker exec -it mod bash    (or: m docker/enter mod)"
    echo "  run a mod: m docker/serve <mod>"
    exit 0
fi

# ---- Local mode ----
echo "=== mod start ==="

# Preflight
if ! command -v m &> /dev/null; then
    echo "[!] mod not installed. Run ./setup.sh first."
    exit 1
fi

if ! command -v pm2 &> /dev/null; then
    echo "[!] pm2 not installed. Run ./setup.sh first."
    exit 1
fi

# Start API + App + Gateway
echo "[+] Starting API + App + Gateway..."
m app/serve
echo "[ok] API (:8000) + App (:3001) + Gateway (:3000) started"

echo ""
echo "=== mod running ==="
pm2 status
