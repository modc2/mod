"""
copytensor.agent.mcp_server — the MCP server, over stdio or HTTP.

One JSON-RPC dispatcher (`handle_message`) serves two transports:

  * stdio — newline-delimited JSON-RPC 2.0, zero dependencies:

        claude mcp add copytensor -- python3 -m src.agent.mcp_server

  * streamable HTTP — the running API mounts the same dispatcher at
    `POST /mcp` (src/api/app.py), which is how the fleet connects:

        claude mcp add --transport http copytensor http://localhost:50150/mcp
        # or through the gateway: https://<host>/api/copytensor/mcp

Scope: `COPYTENSOR_MCP_SCOPE=agent` restricts the tool list to the read-only
set; the default, `all`, adds the ops tools — the copy book and `ct_sync`.
Every tool is a call against the running API on COPYTENSOR_API_URL — start
`m copytensor/serve` first.

APPROVAL: a tool that writes (tools.WRITE_TOOLS) is not executed here. It is
parked with the running API (`POST /agent/approvals`) and this dispatcher
blocks until a human approves it in the console, or declines it — in which
case the tool RESULT is the decline, so the model answers to it instead of
crashing. Set `COPYTENSOR_MCP_APPROVAL=0` to run a client ungated (the
module's own agent always sets it to its run id, and cannot turn it off).
"""
from __future__ import annotations

import json
import os
import sys
import time
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
    "basket back to the console as a card. Nothing here loads a wallet.\n"
    "Writes are human-gated: ct_create_copy, ct_resize_copy, ct_delete_copy, "
    "ct_pause_copy, ct_resume_copy, ct_watch, ct_unwatch and a live ct_sync "
    "pause for an approval in the console before they run, and come back "
    "with the decline reason if the answer is no. Reads and a dry-run sync "
    "never pause. Expect a write to take as long as a human takes."
)


# ── the approval gate ────────────────────────────────────────────

def approval_run() -> Optional[str]:
    """The run this client is parking writes under, or None when ungated."""
    v = os.environ.get("COPYTENSOR_MCP_APPROVAL", "")
    if v in ("0", "off", "false", "no"):
        return None
    # An MCP client over HTTP has no run of its own; its cards still show up
    # in the console's pending list, just labelled as coming from outside.
    return v or "mcp"


# One chunk of the long poll. Shorter than tools.TIMEOUT_SEC so the HTTP
# call always returns an answer rather than a read timeout.
_WAIT_CHUNK_SEC = 25.0


def gate(name: str, args: Dict, run_id: str) -> tuple:
    """(allowed, note). Blocks on the human; fails CLOSED on any error."""
    try:
        parked = tools._request("POST", "/agent/approvals", body={
            "run_id": run_id, "tool": name, "args": args,
            "summary": tools.describe(name, args), "risk": tools.risk_of(name, args)})
        approval_id = parked["id"]
        deadline = time.time() + float(parked.get("ttl") or 600) + 5
    except Exception as e:
        return False, f"could not reach the approval queue ({e}) — nothing was run"
    while time.time() < deadline:
        try:
            row = tools._request("GET", f"/agent/approvals/{approval_id}/wait",
                                 params={"timeout": _WAIT_CHUNK_SEC},
                                 timeout=_WAIT_CHUNK_SEC + 20)
        except Exception as e:
            return False, f"lost the approval queue while waiting ({e})"
        state = row.get("state")
        if state == "approved":
            return True, row.get("note") or ""
        if state == "declined":
            return False, row.get("note") or "no reason given"
    return False, "the approval request expired"


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
        run_id = approval_run()
        if run_id and tools.needs_approval(name, args):
            ok, note = gate(name, args, run_id)
            if not ok:
                return {"jsonrpc": "2.0", "id": id_, "result": {
                    "content": [{"type": "text", "text":
                                 f"DECLINED by the human: {note}. Nothing ran. "
                                 f"Do not retry this call — ask them what to "
                                 f"change, or propose something else."}],
                    "isError": False}}
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
