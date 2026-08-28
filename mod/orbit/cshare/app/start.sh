#!/usr/bin/env bash
cd "$(dirname "$0")"
export PORT="${APP_PORT:-${PORT:-50291}}"
export BASE_PATH="/cshare"
export API_URL="${API_URL:-http://localhost:50290}"
exec node server.js
