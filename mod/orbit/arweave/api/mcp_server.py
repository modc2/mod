"""
arweave mcp — Model Context Protocol endpoint (Streamable HTTP transport).

POST /mcp (also /api/mcp locally, and modc2.com/arweave/api/mcp through the
gateway) speaks JSON-RPC 2.0 so any MCP client (Claude Code, IDEs, agent
frameworks) can drive the module as tools: store data, fetch by tx id, list
the local index, read tx records and quote storage prices.

Self-contained by design, like core/store's: the JSON-RPC handling is
hand-rolled (no `mcp` package) and every tool wraps the same ArweaveClient
the REST routes use, so behaviour matches the HTTP API exactly. Plain JSON
responses (no SSE): one request per POST, one response back.

Tool failures (bad args, gateway misses, 4xx) come back as *successful*
JSON-RPC results with isError=true, per the MCP spec, so the calling model
can read the message and correct course instead of hitting a dead transport.

Client registration (Claude Code):
    claude mcp add --transport http arweave http://localhost:50153/mcp
"""
from __future__ import annotations

import inspect
import json
from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse

router = APIRouter()

SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
DEFAULT_PROTOCOL_VERSION = "2025-03-26"

INSTRUCTIONS = (
    "Arweave permanent storage. Reads (arweave_get, arweave_tx, arweave_price, "
    "arweave_network, arweave_status) hit the public gateway and need no auth. "
    "arweave_put stores data: with a wallet configured on the server it is "
    "uploaded to Arweave and returns a real 43-char tx id; without one it is "
    "kept on this box under a local `sha256-...` id (the result says which — "
    "check `backend`). arweave_list/arweave_rm manage the local index only; "
    "nothing on Arweave can be deleted."
)

# Wired by api.api after it constructs the shared client (keeps one index
# connection path and lets tests swap the client).
_client: Any = None
_config: Callable[[], Dict[str, Any]] = lambda: {}


def bind(client: Any, config: Callable[[], Dict[str, Any]]) -> None:
    global _client, _config
    _client, _config = client, config


def _req(args: dict, key: str) -> str:
    v = str(args.get(key) or "").strip()
    if not v:
        raise ValueError(f"{key} required")
    return v


# ── tool handlers ────────────────────────────────────────────────────────────

def _t_status(args):
    return _client.status()


def _t_info(args):
    cfg = _config()
    return {
        "name": cfg.get("name", "arweave"),
        "version": cfg.get("version"),
        "description": cfg.get("description"),
        "gateway": _client.gateway,
        "wallet_configured": bool(_client.wallet_path),
        "urls": cfg.get("urls", {}),
        "tools": sorted(TOOLS),
    }


def _t_put(args):
    data = args.get("data")
    if data is None or data == "":
        raise ValueError("data required (string or JSON object/array)")
    name = str(args.get("name") or "") or None
    if isinstance(data, str) and args.get("as_json"):
        try:
            data = json.loads(data)
        except json.JSONDecodeError as e:
            raise ValueError(f"data is not valid JSON: {e}")
    return _client.put(data, name=name)


def _t_get(args):
    txid = _req(args, "txid")
    max_chars = int(args.get("max_chars") or 20000)
    raw = _client._fetch(txid)
    out: Dict[str, Any] = {"txid": txid, "size": len(raw)}
    try:
        text = raw.decode()
    except UnicodeDecodeError:
        out["encoding"] = "binary"
        out["sha256"] = __import__("hashlib").sha256(raw).hexdigest()
        out["hint"] = f"binary content; download raw via GET /api/get/{txid}?raw=1"
        return out
    try:
        out["json"] = json.loads(text)
        out["encoding"] = "json"
    except json.JSONDecodeError:
        out["encoding"] = "text"
        out["truncated"] = len(text) > max_chars
        out["text"] = text[:max_chars]
    return out


def _t_list(args):
    limit = max(1, min(int(args.get("limit") or 100), 1000))
    return {"items": _client.list(limit=limit)}


def _t_rm(args):
    return _client.rm(_req(args, "txid"))


def _t_tx(args):
    return _client.tx(_req(args, "txid"))


def _t_price(args):
    n = int(args.get("num_bytes") or 0)
    if n <= 0:
        raise ValueError("num_bytes must be a positive integer")
    out = _client.price(n)
    if "winston" in out:
        try:
            out["ar"] = int(out["winston"]) / 1e12
        except (TypeError, ValueError):
            pass
    return out


def _t_network(args):
    return _client.info()


_TXID = {"type": "string", "description": "Arweave tx id (43 chars) or local sha256-… id"}

TOOLS: Dict[str, Dict[str, Any]] = {
    "arweave_status": {
        "description": "Gateway connectivity, wallet presence, local index size.",
        "inputSchema": {"type": "object", "properties": {}},
        "handler": _t_status,
    },
    "arweave_info": {
        "description": "Module info: version, gateway, URLs and the tool roster.",
        "inputSchema": {"type": "object", "properties": {}},
        "handler": _t_info,
    },
    "arweave_put": {
        "description": (
            "Store data. Uploads to Arweave when the server has a wallet "
            "(returns a 43-char tx id, backend=arweave); otherwise keeps it "
            "locally under a sha256-… id (backend=local) and warns. "
            "Objects/arrays are stored as JSON, strings as text."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "data": {"description": "content: string, or JSON object/array",
                         "anyOf": [{"type": "string"}, {"type": "object"}, {"type": "array"}]},
                "name": {"type": "string", "description": "optional file name tag"},
                "as_json": {"type": "boolean",
                            "description": "when data is a string, parse it as JSON first"},
            },
            "required": ["data"],
        },
        "handler": _t_put,
    },
    "arweave_get": {
        "description": "Fetch content by tx id. JSON is parsed; text is returned "
                       "(truncated to max_chars); binary returns size + sha256.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "txid": _TXID,
                "max_chars": {"type": "integer", "description": "text cap (default 20000)"},
            },
            "required": ["txid"],
        },
        "handler": _t_get,
    },
    "arweave_list": {
        "description": "List tx ids this node has stored (local index, newest first).",
        "inputSchema": {"type": "object", "properties": {
            "limit": {"type": "integer", "description": "max rows (default 100)"}}},
        "handler": _t_list,
    },
    "arweave_rm": {
        "description": "Remove an id from the local index (and its local blob). "
                       "Content already on Arweave is permanent and unaffected.",
        "inputSchema": {"type": "object", "properties": {"txid": _TXID},
                        "required": ["txid"]},
        "handler": _t_rm,
    },
    "arweave_tx": {
        "description": "Raw Arweave transaction record (owner, tags, data_size, reward).",
        "inputSchema": {"type": "object", "properties": {"txid": _TXID},
                        "required": ["txid"]},
        "handler": _t_tx,
    },
    "arweave_price": {
        "description": "Cost to store num_bytes permanently, in winston and AR.",
        "inputSchema": {"type": "object", "properties": {
            "num_bytes": {"type": "integer", "description": "payload size in bytes"}},
            "required": ["num_bytes"]},
        "handler": _t_price,
    },
    "arweave_network": {
        "description": "Gateway /info: network name, height, peers, version.",
        "inputSchema": {"type": "object", "properties": {}},
        "handler": _t_network,
    },
}


# ── JSON-RPC plumbing ────────────────────────────────────────────────────────

def _rpc_result(id_, result) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": id_, "result": result})


def _rpc_error(id_, code: int, message: str, status: int = 200) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": id_,
                         "error": {"code": code, "message": message}},
                        status_code=status)


def _tool_error(id_, message: str) -> JSONResponse:
    return _rpc_result(id_, {"content": [{"type": "text", "text": message}],
                             "isError": True})


async def _call_tool(id_, params: dict) -> JSONResponse:
    name = str(params.get("name") or "")
    tool = TOOLS.get(name)
    if not tool:
        return _rpc_error(id_, -32602, f"unknown tool: {name}")
    args = params.get("arguments") or {}
    if not isinstance(args, dict):
        return _rpc_error(id_, -32602, "arguments must be an object")
    try:
        result = tool["handler"](args)
        if inspect.isawaitable(result):
            result = await result
    except HTTPException as e:
        detail = e.detail if isinstance(e.detail, str) else json.dumps(e.detail)
        return _tool_error(id_, f"{name} failed ({e.status_code}): {detail}")
    except Exception as e:
        return _tool_error(id_, f"{name} failed: {type(e).__name__}: {e}")
    if isinstance(result, dict) and result.get("error"):
        # ArweaveClient reports soft failures as {"error": ...}; surface them.
        return _tool_error(id_, f"{name} failed: {result['error']}")
    out: Dict[str, Any] = {
        "content": [{"type": "text", "text": json.dumps(result, indent=2, default=str)}],
        "isError": False,
    }
    if isinstance(result, dict):
        out["structuredContent"] = result
    return _rpc_result(id_, out)


@router.get("/mcp")
def mcp_get():
    """Streamable HTTP without SSE: nothing to GET — clients must POST."""
    return Response(status_code=405, media_type="text/plain",
                    content="POST JSON-RPC 2.0 messages to this endpoint "
                            "(MCP Streamable HTTP, tools only)")


@router.post("/mcp")
async def mcp_post(request: Request):
    """MCP Streamable HTTP endpoint: one JSON-RPC message per POST."""
    try:
        body = json.loads((await request.body()) or b"")
    except Exception:
        return _rpc_error(None, -32700, "parse error: body is not valid JSON", status=400)
    if not isinstance(body, dict) or not isinstance(body.get("method"), str):
        id_ = body.get("id") if isinstance(body, dict) else None
        return _rpc_error(id_, -32600, "invalid request: expected a JSON-RPC 2.0 "
                                       "object with a method", status=400)
    method, id_, params = body["method"], body.get("id"), body.get("params") or {}
    if id_ is None or method.startswith("notifications/"):
        return Response(status_code=202)
    if method == "initialize":
        client_ver = str(params.get("protocolVersion") or "")
        return _rpc_result(id_, {
            "protocolVersion": client_ver if client_ver in SUPPORTED_PROTOCOL_VERSIONS
            else DEFAULT_PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "arweave",
                           "version": str(_config().get("version") or "0.2.0")},
            "instructions": INSTRUCTIONS,
        })
    if method == "ping":
        return _rpc_result(id_, {})
    if method == "tools/list":
        return _rpc_result(id_, {"tools": [
            {"name": n, "description": t["description"], "inputSchema": t["inputSchema"]}
            for n, t in TOOLS.items()]})
    if method == "tools/call":
        return await _call_tool(id_, params)
    return _rpc_error(id_, -32601, f"method not found: {method}")
