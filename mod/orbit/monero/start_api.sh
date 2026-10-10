#!/bin/bash
# Serve the loopback REST API that backs the web app (pm2: monero-api).
#
# This is the third of the module's three ports and the one the app actually
# talks to: :50690 is the mod-protocol front door (owner-gated by the shared
# gate, which blocks even read-only explorer calls), :50691 is the Next app,
# and this is api.py on :8940 -- reads open, spending and view-key scanning
# behind the bearer token in ~/.mod/monero/server.secret. The MCP server is
# mounted here too, on /mcp.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

# MONERO_REST_PORT wins over an inherited PORT on purpose. Whoever restarts this
# (pm2 --update-env, the app's /api route) may be carrying the *app's* PORT in
# its environment, and api.py binding :50691 crash-loops against the front end.
export PORT="${MONERO_REST_PORT:-8940}"
export PYTHONPATH="$DIR${PYTHONPATH:+:$PYTHONPATH}"
export MONERO_API_HOST="${MONERO_API_HOST:-127.0.0.1}"

exec python3 api.py
