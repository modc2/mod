#!/bin/bash

# ---- Docker mode ----
if [ "$1" = "--docker" ]; then
    echo "=== mod stop (docker) ==="
    CONTAINER_NAME="${2:-mod}"

    if docker ps -q -f name="$CONTAINER_NAME" | grep -q .; then
        echo "[+] Stopping container: $CONTAINER_NAME"
        docker kill "$CONTAINER_NAME"
        docker rm "$CONTAINER_NAME"
        echo "[ok] container stopped"
    else
        echo "[ok] container $CONTAINER_NAME not running"
    fi
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
