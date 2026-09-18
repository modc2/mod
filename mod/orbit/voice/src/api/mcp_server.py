"""voice.api.mcp_server — the MCP server, over stdio or HTTP.

One JSON-RPC dispatcher serves both transports:

  * stdio — newline-delimited JSON-RPC 2.0, zero dependencies beyond
    requests:

        claude mcp add voice -- python3 -m src.api.mcp_server

  * streamable HTTP — the running API mounts the same dispatcher at
    POST /mcp (src/api/server.py):

        claude mcp add --transport http voice http://localhost:50980/mcp

Every tool is read-only except voice_transcribe, which is a server-side
convenience (it forwards a file on THIS box to liquidai's /transcribe).
The fully local path is the browser app; the tools say so.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List, Optional, Union

import requests

VOICE_API = os.environ.get("VOICE_API_URL", "http://127.0.0.1:50980")
SERVER_INFO = {"name": "voice", "version": "0.1.0"}
PROTOCOL_VERSION = "2025-06-18"

INSTRUCTIONS = (
    "Local audio-to-text. Inference runs in a visitor's browser tab "
    "(Whisper via transformers.js, or LiquidAI LFM2.5-Audio-1.5B-ONNX via "
    "onnxruntime-web on WebGPU) — the server never sees audio. "
    "voice_engines lists the in-tab engines; voice_models is the liquidai "
    "catalog filtered to what a tab can run; voice_app gives the URL to "
    "open. voice_transcribe is the one server-side path: it sends a file "
    "on this box through liquidai's /transcribe, for when there is no "
    "browser in the loop."
)

TOOLS: List[Dict[str, Any]] = [
    {
        "name": "voice_engines",
        "description": "The in-browser engines the app ships (runtime, "
                       "download size, tasks).",
        "inputSchema": {"type": "object", "properties": {}},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "voice_models",
        "description": "Browser-runnable models from the liquidai catalog. "
                       "kind filters to audio|text|vision|embed.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string",
                         "enum": ["audio", "text", "vision", "embed"]},
            },
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "voice_status",
        "description": "Health of the voice API and whether liquidai's "
                       "catalog is reachable.",
        "inputSchema": {"type": "object", "properties": {}},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "voice_app",
        "description": "URLs for the browser app (the fully local "
                       "transcription path) and this MCP endpoint.",
        "inputSchema": {"type": "object", "properties": {}},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "voice_transcribe",
        "description": "Server-side transcription of an audio file on this "
                       "box, forwarded to liquidai /transcribe. NOT the "
                       "browser path — audio leaves the tab-only story "
                       "here, deliberately and visibly.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string",
                         "description": "Path to a local audio file"},
                "model": {"type": "string",
                          "description": "Optional liquidai model id"},
            },
            "required": ["path"],
        },
        "annotations": {"readOnlyHint": False, "openWorldHint": False},
    },
]


# ── tool bodies ──────────────────────────────────────────────────────

def _get(path: str, **params) -> Any:
    r = requests.get(f"{VOICE_API}{path}", params=params or None, timeout=30)
    return r.json()


def call_tool(name: str, args: Dict[str, Any]) -> Any:
    if name == "voice_engines":
        return _get("/engines")
    if name == "voice_models":
        return _get("/models", **{k: v for k, v in args.items() if v})
    if name == "voice_status":
        return _get("/health")
    if name == "voice_app":
        return {
            "app": f"{VOICE_API}/",
            "gateway_app": "/voice/",
            "mcp": f"{VOICE_API}/mcp",
            "note": "Open the app in a browser — recording, transcription "
                    "and translation all run inside the tab.",
        }
    if name == "voice_transcribe":
        path = os.path.expanduser(args["path"])
        if not os.path.isfile(path):
            return {"error": f"no such file: {path}"}
        with open(path, "rb") as f:
            r = requests.post(
                f"{VOICE_API}/transcribe",
                files={"file": (os.path.basename(path), f)},
                data={"model": args["model"]} if args.get("model") else {},
                timeout=600)
        return r.json()
    raise ValueError(f"unknown tool: {name}")


# ── JSON-RPC dispatcher ──────────────────────────────────────────────

def handle_message(msg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """One request in, one response out. Notifications return None."""
    method = msg.get("method", "")
    msg_id = msg.get("id")
    params = msg.get("params") or {}

    if method.startswith("notifications/"):
        return None

    def ok(result: Any) -> Dict[str, Any]:
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    def err(code: int, message: str) -> Dict[str, Any]:
        return {"jsonrpc": "2.0", "id": msg_id,
                "error": {"code": code, "message": message}}

    try:
        if method == "initialize":
            return ok({
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": SERVER_INFO,
                "instructions": INSTRUCTIONS,
            })
        if method == "ping":
            return ok({})
        if method == "tools/list":
            return ok({"tools": TOOLS})
        if method == "tools/call":
            name = params.get("name", "")
            args = params.get("arguments") or {}
            try:
                result = call_tool(name, args)
                return ok({"content": [{"type": "text",
                                        "text": json.dumps(result,
                                                           indent=2)}],
                           "isError": False})
            except Exception as e:
                return ok({"content": [{"type": "text", "text": str(e)}],
                           "isError": True})
        return err(-32601, f"method not found: {method}")
    except Exception as e:  # never let the transport die on a tool bug
        return err(-32603, f"internal error: {e}")


def handle_batch(body: Union[Dict, List]) -> Optional[Union[Dict, List]]:
    """HTTP entry: accept a single message or a batch."""
    if isinstance(body, list):
        replies = [r for r in (handle_message(m) for m in body)
                   if r is not None]
        return replies or None
    return handle_message(body)


# ── stdio transport ──────────────────────────────────────────────────

def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        reply = handle_batch(msg)
        if reply is not None:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
