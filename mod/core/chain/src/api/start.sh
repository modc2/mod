#!/usr/bin/env bash
# Chain Hub API launcher (pm2 / standalone). Honors $PORT (default 8800).
set -e
cd "$(dirname "$0")"
PORT="${PORT:-8800}"
exec python3 -m uvicorn api:app --host 0.0.0.0 --port "$PORT" --app-dir .
