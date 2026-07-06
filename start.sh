#!/bin/bash
set -e

MOD_DIR="${MOD_DIR:-$HOME/mod}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---- Docker mode ----
if [ "$1" = "--docker" ]; then
    echo "=== mod start (docker) ==="
    IMAGE_NAME="${2:-mod}"
    CONTAINER_NAME="${3:-mod}"

    # Stop existing container if running
    if docker ps -q -f name="$CONTAINER_NAME" | grep -q .; then
        echo "[+] Stopping existing container: $CONTAINER_NAME"
        docker kill "$CONTAINER_NAME" 2>/dev/null || true
        docker rm "$CONTAINER_NAME" 2>/dev/null || true
    elif docker ps -aq -f name="$CONTAINER_NAME" | grep -q .; then
        docker rm "$CONTAINER_NAME" 2>/dev/null || true
    fi

    # Build image
    echo "[+] Building Docker image: $IMAGE_NAME"
    docker build -t "$IMAGE_NAME" "$SCRIPT_DIR"

    # Run container
    echo "[+] Starting container: $CONTAINER_NAME"
    docker run -d --name "$CONTAINER_NAME" \
        -p 3000:3000 \
        -v "$HOME/mod:/root/mod" \
        -v "$HOME/.mod:/root/.mod" \
        "$IMAGE_NAME"

    echo "[ok] container running"
    docker ps -f name="$CONTAINER_NAME"
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
