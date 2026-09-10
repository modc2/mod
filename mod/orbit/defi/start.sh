#!/usr/bin/env bash
# Build both halves of the defi module, then run them under pm2.
#
#   ./start.sh                run it
#   ./start.sh --build-only   compile without starting
#
# The Next build is done in place, so it takes a lock: two concurrent builds
# under a live `next start` leave the app serving half a build tree, and the
# symptom (blank tabs) looks nothing like the cause.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
API_DIR="$DIR/src/api"
APP_DIR="$DIR/src/app"
BUILD_ONLY="${1:-}"

echo "── building the Rust API"
cargo build --release --offline --manifest-path "$API_DIR/Cargo.toml" 2>&1 | tail -3

echo "── building the Next.js app"
(
  flock 9
  cd "$APP_DIR"
  [ -d node_modules ] || npm install --no-audit --no-fund
  NEXT_PUBLIC_BASE_PATH="${NEXT_PUBLIC_BASE_PATH:-/defi}" npx next build 2>&1 | tail -6
) 9>"$APP_DIR/.build.lock"

if [ "$BUILD_ONLY" = "--build-only" ]; then
  echo "── built (not started)"
  exit 0
fi

echo "── (re)starting under pm2"
if command -v pm2 >/dev/null 2>&1; then
  pm2 delete defi-api defi-app >/dev/null 2>&1 || true
  pm2 start "$DIR/ecosystem.config.js"
  pm2 save >/dev/null 2>&1 || true
  pm2 status defi-api defi-app || true
else
  echo "pm2 not found — running in the background with nohup"
  DEFI_MODULE_DIR="$DIR" DEFI_BLOCKS_DIR="$API_DIR/blocks" \
    nohup "$API_DIR/target/release/defi-api" 50500 >/tmp/defi-api.log 2>&1 &
  (cd "$APP_DIR" && NEXT_PUBLIC_BASE_PATH=/defi nohup npx next start -p 50501 -H 0.0.0.0 >/tmp/defi-app.log 2>&1 &)
fi

echo "── api  http://localhost:50500/health"
echo "── app  http://localhost:50501/defi"
