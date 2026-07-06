#!/usr/bin/env bash
# Start the LocalFS app (Next.js + Python bridge).
# Usage: ./start.sh [dev|build|start]
set -euo pipefail

cd "$(dirname "$0")"

MODE="${1:-dev}"

if [ ! -d node_modules ]; then
  echo "[localfs-app] installing dependencies…"
  npm install --no-audit --no-fund
fi

case "$MODE" in
  dev)
    exec npm run dev
    ;;
  build)
    npm run build
    ;;
  start)
    npm run build
    exec npm run start
    ;;
  *)
    echo "unknown mode: $MODE (use dev | build | start)" >&2
    exit 1
    ;;
esac
