#!/bin/bash
# Serve the loopback REST API that backs the web app (pm2: zcash-api).
#
# This is the third of the module's three ports and the one the app actually
# talks to: :50148 is the mod-protocol front door (owner-gated by the shared
# gate, which blocks even read-only explorer calls), :50149 is the Next app,
# and this is api.py on :8930 — reads open, spending behind the bearer token
# in ~/.mod/zcash/server.secret.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

# ZCASH_REST_PORT wins over an inherited PORT on purpose. Whoever restarts this
# (pm2 --update-env, the app's /api route) may be carrying the *app's* PORT in
# its environment, and api.py binding :50149 crash-loops against the front end.
export PORT="${ZCASH_REST_PORT:-8930}"
export PYTHONPATH="$DIR${PYTHONPATH:+:$PYTHONPATH}"
export ZCASH_API_HOST="${ZCASH_API_HOST:-127.0.0.1}"

exec python3 api.py
