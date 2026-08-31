"""
copytensor.agent.mcp_server — the MCP server, over stdio or HTTP.

One JSON-RPC dispatcher (`handle_message`) serves two transports:

  * stdio — newline-delimited JSON-RPC 2.0, zero dependencies:

        claude mcp add copytensor -- python3 -m src.agent.mcp_server

  * streamable HTTP — the running API mounts the same dispatcher at
    `POST /mcp` (src/api/app.py), which is how the fleet connects:

        claude mcp add --transport http copytensor http://localhost:50150/mcp
        # or through the gateway: https://<host>/api/copytensor/mcp

Scope: `COPYTENSOR_MCP_SCOPE=agent` restricts the tool list to the strat
agent's read-only set (that is how agent.py launches it); the default,
`all`, adds the ops tools — the copy book and `ct_sync`. Every tool is a
call against the running API on COPYTENSOR_API_URL — start
`m copytensor/serve` first.
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from typing import Any, Dict, List, Optional, Union

from . import tools

SERVER_INFO = {"name": "copytensor", "version": "0.9.0"}
PROTOCOL_VERSION = "2025-06-18"

INSTRUCTIONS = (
    "Bittensor dTAO copy-trading. Reads: ct_traders is the board strats are "
    "built from — every coldkey copytensor indexes, with portfolio value and "
    "windowed PnL; ct_trader and ct_trader_flows open one up; ct_flows is the "
    "live tape; ct_subnets and ct_market price the network; ct_backtest "
    "replays a basket. The book: ct_copies lists what is being mirrored, "
    "ct_create_copy / ct_resize_copy / ct_pause_copy start or change a copy, "
    "ct_portfolio is the blended plan, and ct_sync applies it to the chain — "
    "call ct_sync with dry_run=true before a live pass. propose_strat hands a "
    "basket back to the console as a card. Nothing here loads a wallet."
)


def scope() -> str:
    return os.environ.get("COPYTENSOR_MCP_SCOPE", "all")


def handle_message(msg: Dict, scope_: Optional[str] = None) -> Optional[Dict]:
    """One JSON-RPC message in, one reply out (None for notifications)."""
    if not isinstance(msg, dict):
        return {"jsonrpc": "2.0", "id": None,
                "error": {"code": -32600, "message": "invalid request"}}
    method = msg.get("method")
    id_ = msg.get("id")
    if id_ is None:
        return None  # notifications/initialized etc.
    sc = scope_ or scope()

    if method == "initialize":
        client_ver = (msg.get("params") or {}).get("protocolVersion")
        result: Any = {
            "protocolVersion": client_ver or PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
            "instructions": INSTRUCTIONS,
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": tools.list_tools(sc)}
    elif method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        try:
            out = tools.call_tool(name, args, scope=sc)
            result = {
                "content": [{"type": "text",
                             "text": json.dumps(out, indent=2, default=str)}],
                "isError": False,
            }
        except Exception as e:
            result = {
                "content": [{"type": "text", "text": f"{type(e).__name__}: {e}"}],
                "isError": True,
            }
    else:
        return {"jsonrpc": "2.0", "id": id_,
                "error": {"code": -32601, "message": f"method not found: {method}"}}
    return {"jsonrpc": "2.0", "id": id_, "result": result}


def handle_batch(body: Union[Dict, List],
                 scope_: Optional[str] = None) -> Optional[Union[Dict, List]]:
    """A request or a batch; None when there is nothing to answer."""
    if isinstance(body, list):
        replies = [r for r in (handle_message(m, scope_) for m in body)
                   if r is not None]
        return replies or None
    return handle_message(body, scope_)


def schema(scope_: Optional[str] = None) -> Dict:
    """What an operator reads before connecting: transports + tool list."""
    return {
        "server": SERVER_INFO,
        "protocol": PROTOCOL_VERSION,
        "scope": scope_ or scope(),
        "transports": {
            "http": "POST /mcp (streamable HTTP, JSON-RPC 2.0; batches ok)",
            "stdio": "python3 -m src.agent.mcp_server",
        },
        "tools": tools.list_tools(scope_ or scope()),
    }


# ── stdio ────────────────────────────────────────────────────────

def _write(reply: Dict):
    sys.stdout.write(json.dumps(reply) + "\n")
    sys.stdout.flush()


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        try:
            reply = handle_batch(msg)
        except Exception:
            traceback.print_exc(file=sys.stderr)
            reply = None
            if isinstance(msg, dict) and msg.get("id") is not None:
                reply = {"jsonrpc": "2.0", "id": msg["id"],
                         "error": {"code": -32603, "message": "internal error"}}
        if reply is None:
            continue
        for r in (reply if isinstance(reply, list) else [reply]):
            _write(r)


if __name__ == "__main__":
    main()
