#!/usr/bin/env bash
# start.sh — build Rust API + Next.js app, then launch under pm2
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
API_DIR="$DIR/src/api"
APP_DIR="$DIR/src/app"
LOGS_DIR="$DIR/logs"

mkdir -p "$LOGS_DIR"

# ── 1. Build Rust API ─────────────────────────────────────────────────────
echo "==> Building Rust API..."
cd "$API_DIR"
cargo build --release 2>&1

# ── 2. Install and build Next.js app ─────────────────────────────────────
echo "==> Installing Next.js dependencies..."
cd "$APP_DIR"
if [ ! -d node_modules ]; then
  npm install 2>&1
fi

echo "==> Building Next.js app..."
npm run build 2>&1

# ── 3. Start under pm2 ───────────────────────────────────────────────────
echo "==> Starting services under pm2..."
cd "$DIR"
pm2 start ecosystem.config.js --env production 2>/dev/null \
  || pm2 restart ecosystem.config.js 2>/dev/null \
  || true

pm2 save 2>/dev/null || true

echo ""
echo "wingman running:"
echo "  API:     http://localhost:50830/"
echo "  App:     http://localhost:50831/wingman"
echo "  Gateway: /wingman  (proxied from :50831)"
echo ""
echo "pm2 status:"
pm2 list --no-color 2>/dev/null | grep wingman || true
