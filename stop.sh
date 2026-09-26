#!/bin/bash

# ---- Docker mode ----
if [ "$1" = "--docker" ]; then
    echo "=== mod stop (docker) ==="
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    cd "$SCRIPT_DIR" || exit 1
    docker compose down
    echo "[ok] sandbox stopped"
    exit 0
fi

# ---- Local mode ----
echo "=== mod stop ==="

if [ $# -eq 0 ]; then
    echo "[+] Killing all mod servers..."
    m server/killall
    echo "[+] Killing gateway..."
    pm2 delete gateway 2>/dev/null || true
else
    echo "[+] Killing server: $1..."
    m server/kill "$1"
fi

echo "[ok] stopped"
pm2 status 2>/dev/null || true
