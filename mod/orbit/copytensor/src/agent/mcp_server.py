"""
copytensor.agent.mcp_server — MCP stdio server over the strat toolbox.

Zero-dependency Model Context Protocol stdio transport (newline-delimited
JSON-RPC 2.0), so any MCP client can read copytensor's boards:

    claude mcp add copytensor -- python3 -m src.agent.mcp_server

or in an mcpServers config:

    {"copytensor": {"command": "python3",
                    "args": ["-m", "src.agent.mcp_server"],
                    "cwd": "/root/mod/mod/orbit/copytensor"}}

It talks to the running API on COPYTENSOR_API_URL — start `m copytensor/serve`
first.
"""
from __future__ import annotations

import json
import sys
import traceback

from . import tools

SERVER_INFO = {"name": "copytensor", "version": "0.7.0"}
PROTOCOL_VERSION = "2025-06-18"

INSTRUCTIONS = (
    "Bittensor dTAO copy-trading tools. ct_traders is the board strats are "
    "built from — every coldkey copytensor indexes, with portfolio value and "
    "windowed PnL; ct_trader and ct_trader_flows open one up; ct_flows is the "
    "live tape; ct_subnets and ct_market price the network. When you have "
    "picked a basket, call propose_strat to deliver it. Nothing here signs or "
    "stakes: a proposal is a card the human saves and activates by hand."
)


def _reply(id_, result=None, error=None):
    msg = {"jsonrpc": "2.0", "id": id_}
    if error is not None:
        msg["error"] = error
    else:
        msg["result"] = result
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def handle(msg: dict):
    method = msg.get("method")
    id_ = msg.get("id")

    if method == "initialize":
        client_ver = (msg.get("params") or {}).get("protocolVersion")
        _reply(id_, {
            "protocolVersion": client_ver or PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
            "instructions": INSTRUCTIONS,
        })
    elif method == "ping":
        _reply(id_, {})
    elif method == "tools/list":
        _reply(id_, {"tools": tools.list_tools()})
    elif method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        try:
            result = tools.call_tool(name, args)
            _reply(id_, {
                "content": [{"type": "text",
                             "text": json.dumps(result, indent=2, default=str)}],
                "isError": False,
            })
        except Exception as e:
            _reply(id_, {
                "content": [{"type": "text", "text": f"{type(e).__name__}: {e}"}],
                "isError": True,
            })
    elif id_ is not None:
        _reply(id_, error={"code": -32601, "message": f"method not found: {method}"})
    # notifications (no id) are ignored


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
            handle(msg)
        except Exception:
            traceback.print_exc(file=sys.stderr)
            if msg.get("id") is not None:
                _reply(msg["id"], error={"code": -32603, "message": "internal error"})


if __name__ == "__main__":
    main()
