#!/bin/bash
# Run the core app. Invoked directly or by `mod.py serve()` via PM2.
# Env knobs (all optional, sane defaults):
#   PORT                  Next.js port (default 3001 — gateway owns 3000)
#   API_URL_INTERNAL      server-side API base (default http://localhost:8000)
#   NEXT_PUBLIC_API_URL   browser-side API base (default /api, via gateway)
#   MODE                  "dev" (default) or "prod"
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

PORT="${PORT:-3001}"
export API_URL_INTERNAL="${API_URL_INTERNAL:-http://localhost:8000}"
export NEXT_PUBLIC_API_URL="${NEXT_PUBLIC_API_URL:-/api}"
MODE="${MODE:-dev}"

if [ "$MODE" = "prod" ]; then
    exec npm run start -- -p "$PORT"
else
    exec npm run dev -- -p "$PORT"
fi
