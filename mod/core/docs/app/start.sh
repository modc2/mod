#!/usr/bin/env bash
cd "$(dirname "$0")"
export PORT="${APP_PORT:-${PORT:-50191}}"
export BASE_PATH="/docs"
exec node server.js
