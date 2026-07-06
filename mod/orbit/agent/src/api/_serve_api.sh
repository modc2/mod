#!/usr/bin/env bash
# pm2 entrypoint for the agent API (FastAPI/uvicorn on :50117).
set -e
cd "$(dirname "$0")"
MOD_ROOT="/root/mod/mod"
export PYTHONPATH="$MOD_ROOT:$MOD_ROOT/orbit/agent:$MOD_ROOT/orbit/agent/src"
export PORT="${PORT:-50117}"
exec python3 -m uvicorn api:app --host 0.0.0.0 --port "$PORT"
