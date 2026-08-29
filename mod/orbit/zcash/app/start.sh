#!/bin/bash
# Serve the zcash web app (pm2: zcash-app).
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

PORT="${PORT:-50149}"
REST_PORT="${ZCASH_REST_PORT:-8930}"

# src/app/api/[fn]/route.ts forwards ${basePath}/api/* to this origin, so the
# browser only ever talks to the app port and api.py stays bound to loopback.
# That route also restarts the backend if it finds it down, which needs to know
# where the module lives.
export ZCASH_API_ORIGIN="${ZCASH_API_ORIGIN:-http://127.0.0.1:$REST_PORT}"
export ZCASH_MODULE_DIR="${ZCASH_MODULE_DIR:-$(dirname "$DIR")}"
export NEXT_PUBLIC_BASE_PATH="${NEXT_PUBLIC_BASE_PATH:-/zcash}"

# Production mode, never `next dev`: a stray dev server shares this .next/ and
# leaves a dev build behind, after which the served page has no working chunks.
[ -f "$DIR/.next/BUILD_ID" ] || bash "$DIR/build.sh"

exec npx next start -p "$PORT"
