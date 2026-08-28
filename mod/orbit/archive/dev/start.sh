#!/bin/bash
# Build the Rust gateway (if needed), install app deps (if needed), then start
# both processes under pm2. Re-run any time to pick up code changes.
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"

echo "[dev] building Rust API…"
( cd "$DIR/src/api" && cargo build --release )

if [ ! -d "$DIR/src/app/node_modules" ]; then
  echo "[dev] installing app deps…"
  ( cd "$DIR/src/app" && npm install )
fi

if [ "${DEV_MODE:-dev}" = "prod" ]; then
  echo "[dev] building Next app…"
  ( cd "$DIR/src/app" && NEXT_PUBLIC_BASE_PATH=/dev npm run build )
fi

echo "[dev] (re)starting pm2 processes…"
pm2 start "$DIR/ecosystem.config.js" --update-env
pm2 save || true

echo "[dev] up:  api → http://localhost:${DEV_API_PORT:-8870}   app → http://localhost:${DEV_APP_PORT:-8871}/dev"
