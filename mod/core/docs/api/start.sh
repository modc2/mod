#!/usr/bin/env bash
# The docs "api" is its MCP server, over Streamable HTTP.
cd "$(dirname "$0")"
export MCP_PORT="${MCP_PORT:-${PORT:-50192}}"

# The shared nix image doesn't carry the framework's python deps yet, so under
# `m pm/start` the ambient interpreter can't `import mod` — fall back to the
# system one, which has it installed. (Safe from here: api/ has no mod.py.)
PY="${PYTHON:-python3}"
"$PY" -c 'import mod' >/dev/null 2>&1 || PY=/usr/bin/python3
exec "$PY" mcp.py --http
