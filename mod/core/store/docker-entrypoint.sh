#!/usr/bin/env bash
# Entrypoint: run the store gateway + app under pm2 (pm2-runtime keeps the
# container in the foreground and supervises both processes).
set -e

API_PORT="${STORE_API_PORT:-50152}"
APP_PORT="${STORE_APP_PORT:-50151}"

echo "── mod store (pm2) ─────────────────────────────────────────"
echo "  API : 0.0.0.0:${API_PORT}  (pm2: store-api)"
echo "  APP : 0.0.0.0:${APP_PORT}  (pm2: store-app)"
echo "  data: /data"
echo "────────────────────────────────────────────────────────────"

# Persist SQLite indexes + off-chain auth state under /data.
export HOME=/data
mkdir -p /data/.store-mod /data/.filecoin-mod /data/.hippius-mod /data/.mod/store

cd /app
exec pm2-runtime start ecosystem.config.js
