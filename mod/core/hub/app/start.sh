#!/usr/bin/env bash
cd "$(dirname "$0")"
export PORT="${APP_PORT:-${PORT:-50521}}"
export BASE_PATH="/hub"
exec node server.js
