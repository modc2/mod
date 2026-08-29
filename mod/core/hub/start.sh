#!/usr/bin/env bash
# hub api — loopback catalog service. bash start.sh [port]
cd "$(dirname "$0")"
exec python3 -m uvicorn api:app --host 127.0.0.1 --port "${1:-50520}"
