#!/bin/bash
# Build the Rust gateway (if needed), install app deps (if needed), then start
# both processes under pm2. Re-run any time to pick up code changes.
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"

echo "[venice] building Rust API…"
( cd "$DIR/src/api" && cargo build --release )

if [ ! -d "$DIR/src/app/node_modules" ]; then
  echo "[venice] installing app deps…"
  ( cd "$DIR/src/app" && npm install )
fi

if [ "${VENICE_MODE:-dev}" = "prod" ]; then
  echo "[venice] building Next app…"
  ( cd "$DIR/src/app" && NEXT_PUBLIC_BASE_PATH=/venice npm run build )
fi

echo "[venice] (re)starting pm2 processes…"
pm2 start "$DIR/ecosystem.config.js" --update-env
pm2 save || true

echo "[venice] up:  api → http://localhost:${VENICE_API_PORT:-50880}   app → http://localhost:${VENICE_APP_PORT:-3880}/venice"
