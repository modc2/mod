#!/usr/bin/env bash
# pm2 entrypoint for the agent Next app (standalone server on :3117, basePath /agent).
set -e
cd "$(dirname "$0")"
export PORT="${PORT:-3117}"
export HOSTNAME="0.0.0.0"
export NEXT_PUBLIC_BASE_PATH="/agent"
export NEXT_PUBLIC_API_URL="/api/agent"
# Self-heal: a wiped or half-finished .next would otherwise crash-loop pm2.
# Standalone builds don't include static assets — they must be copied in.
if [ ! -f .next/standalone/server.js ]; then
  rm -rf .next
  npm run build
  cp -r .next/static .next/standalone/.next/static
  [ -d public ] && cp -r public .next/standalone/public
fi
exec node .next/standalone/server.js
