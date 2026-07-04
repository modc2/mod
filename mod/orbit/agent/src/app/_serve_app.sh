#!/usr/bin/env bash
# pm2 entrypoint for the agent Next app (standalone server on :3117, basePath /agent).
set -e
cd "$(dirname "$0")"
export PORT="${PORT:-3117}"
export HOSTNAME="0.0.0.0"
export NEXT_PUBLIC_BASE_PATH="/agent"
export NEXT_PUBLIC_API_URL="/api/agent"
exec node .next/standalone/server.js
