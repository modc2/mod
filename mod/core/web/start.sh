#!/bin/bash
# Build the Rust catalog API + the Next app, then (re)start both under pm2.
# Re-run any time to pick up code changes. Routed by Caddy at modc2.com/web.
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"

echo "[web] building Rust mod-api…"
( cd "$DIR/api" && cargo build --release )

if [ ! -d "$DIR/app/node_modules" ]; then
  echo "[web] installing app deps…"
  ( cd "$DIR/app" && npm install )
fi

if [ "${MOD_WEB_MODE:-prod}" = "prod" ]; then
  echo "[web] building Next app…"
  ( cd "$DIR/app" && NEXT_PUBLIC_BASE_PATH=/web npm run build )
fi

echo "[web] (re)starting pm2 processes…"
pm2 start "$DIR/ecosystem.config.js" --update-env
pm2 save || true

echo "[web] up:  api → http://localhost:${MOD_WEB_API_PORT:-50420}   app → http://localhost:${MOD_WEB_APP_PORT:-3420}/web   gateway → https://modc2.com/web"
