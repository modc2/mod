#!/usr/bin/env bash
# encrypt console — zero-dep node server (see server.js). `m encrypt/serve_app`
# runs this under pm2; this script is the manual path.
cd "$(dirname "$0")"
export PORT="${APP_PORT:-${PORT:-50381}}"
export API_PORT="${API_PORT:-50380}"
export BASE_PATH="${BASE_PATH:-/encrypt}"
exec node server.js
