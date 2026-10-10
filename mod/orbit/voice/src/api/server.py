"""voice.api.server — the thin server behind a tab-only app.

Three jobs, deliberately nothing more:

  1. Serve the static app (src/app) — the place inference actually happens.
  2. Answer "what can a tab run": /models proxies the liquidai catalog
     filtered to runtime=browser, so the roster tracks HuggingFace without
     this module hardcoding a model list.
  3. Speak MCP at POST /mcp (same dispatcher as the stdio transport).

Errors come back as 200 with an "error" key or as 4xx — never 5xx bodies,
because Cloudflare strips those on the public gateway.
"""

import os
import time
from typing import Optional

import requests
from fastapi import FastAPI, Request, UploadFile, File, Form
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from . import mcp_server

LIQUIDAI_API = os.environ.get("LIQUIDAI_API", "http://127.0.0.1:50460")
APP_DIR = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "app")
STARTED = time.time()

app = FastAPI(title="voice", version="0.1.0")

# The engines the static app actually ships. Sizes are the Q4/default
# variants; the app shows the same numbers before a download starts.
ENGINES = [
    {
        "id": "whisper-tiny",
        "label": "WHISPER TINY",
        "runtime": "transformers.js (WebGPU, WASM fallback)",
        "repo": "onnx-community/whisper-tiny",
        "download_mb": 40,
        "tasks": ["transcribe (100 languages)", "translate to English"],
        "default": False,
    },
    {
        "id": "whisper-base",
        "label": "WHISPER BASE",
        "runtime": "transformers.js (WebGPU, WASM fallback)",
        "repo": "onnx-community/whisper-base",
        "download_mb": 80,
        "tasks": ["transcribe (100 languages)", "translate to English"],
        "default": True,
    },
    {
        "id": "whisper-small",
        "label": "WHISPER SMALL",
        "runtime": "transformers.js (WebGPU, WASM fallback)",
        "repo": "onnx-community/whisper-small",
        "download_mb": 250,
        "tasks": ["transcribe (100 languages)", "translate to English"],
        "default": False,
    },
    {
        "id": "lfm-audio",
        "label": "LFM2.5 AUDIO 1.5B",
        "runtime": "onnxruntime-web (WebGPU required)",
        "repo": "LiquidAI/LFM2.5-Audio-1.5B-ONNX",
        "download_mb": 1900,
        "tasks": ["transcribe (English)"],
        "default": False,
        "note": "The liquidai catalog's browser-runnable audio model. "
                "Q4 encoder + decoder + embedding table.",
    },
    {
        "id": "lfm-translate",
        "label": "LFM2.5 350M (translation post-pass)",
        "runtime": "transformers.js (WebGPU required)",
        "repo": "LiquidAI/LFM2.5-350M-ONNX",
        "download_mb": 300,
        "tasks": ["translate transcript to any target language"],
        "default": False,
        "note": "Every shipped quant uses GatherBlockQuantized, which "
                "onnxruntime-web only implements on WebGPU — no WASM "
                "fallback. Whisper's translate-to-English still works "
                "everywhere.",
    },
]


@app.get("/health")
def health():
    liquidai = {"url": LIQUIDAI_API, "reachable": False}
    try:
        r = requests.get(f"{LIQUIDAI_API}/health", timeout=3)
        liquidai["reachable"] = r.status_code == 200
    except Exception as e:
        liquidai["error"] = str(e)
    return {
        "ok": True,
        "module": "voice",
        "uptime_s": int(time.time() - STARTED),
        "engines": [e["id"] for e in ENGINES],
        "liquidai": liquidai,
        "inference": "in the visitor's browser — this server never sees audio",
    }


@app.get("/engines")
def engines():
    return {"engines": ENGINES, "count": len(ENGINES)}


@app.get("/models")
def models(kind: Optional[str] = None, q: Optional[str] = None):
    """Browser-runnable models, live from the liquidai catalog."""
    params = {"runtime": "browser"}
    if kind:
        params["kind"] = kind
    if q:
        params["q"] = q
    try:
        r = requests.get(f"{LIQUIDAI_API}/models", params=params, timeout=30)
        return JSONResponse(r.json(), status_code=200)
    except Exception as e:
        return JSONResponse(
            {"error": f"liquidai catalog unreachable: {e}",
             "hint": "m liquidai/serve",
             "models": []},
            status_code=200)


@app.post("/transcribe")
async def transcribe(file: UploadFile = File(...),
                     model: Optional[str] = Form(None)):
    """Server-side convenience path — forwards to liquidai /transcribe.

    The browser app never calls this; it exists for MCP clients on this box.
    liquidai will answer honestly about which repos its server runtime can
    actually transcribe with.
    """
    blob = await file.read()
    data = {"model": model} if model else {}
    try:
        r = await run_in_threadpool(
            lambda: requests.post(
                f"{LIQUIDAI_API}/transcribe",
                files={"file": (file.filename or "audio.wav", blob)},
                data=data, timeout=600))
        return JSONResponse(r.json(), status_code=200)
    except Exception as e:
        return JSONResponse(
            {"error": f"liquidai /transcribe failed: {e}",
             "hint": "the fully local path is the app itself — open / "
                     "in a browser"},
            status_code=200)


# ── MCP: one dispatcher, two transports (stdio lives in mcp_server) ──

@app.post("/mcp")
async def mcp_endpoint(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"jsonrpc": "2.0", "id": None,
                             "error": {"code": -32700,
                                       "message": "parse error"}},
                            status_code=400)
    replies = await run_in_threadpool(mcp_server.handle_batch, body)
    if replies is None:  # notification(s) only
        return JSONResponse(None, status_code=202)
    return JSONResponse(replies)


@app.get("/mcp")
def mcp_get():
    return {"transport": "streamable-http",
            "connect": "claude mcp add --transport http voice "
                       "http://localhost:50980/mcp",
            "tools": [t["name"] for t in mcp_server.TOOLS]}


# Static app last so API routes win. html=True serves index.html at /.
app.mount("/", StaticFiles(directory=APP_DIR, html=True), name="app")
