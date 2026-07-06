"""
Filecoin API — FastAPI server exposing the FilecoinClient and serving the static app.

Run via the module:
    m filecoin/serve

Or directly:
    uvicorn api:app --port 50150
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response, JSONResponse
from fastapi.staticfiles import StaticFiles

MODULE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(MODULE_DIR))

from filecoin import FilecoinClient  # noqa: E402


app = FastAPI(
    title="Filecoin Mod API",
    description="Store and retrieve data on Filecoin via Lotus + Lighthouse + IPFS gateways.",
    version="0.1.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

client = FilecoinClient()
APP_DIR = MODULE_DIR / "app"


# ─── API routes (under /api) ────────────────────────────────────────────────


@app.get("/api/health")
def health():
    return {"ok": True, "service": "filecoin"}


@app.get("/api/info")
def info():
    cfg_path = MODULE_DIR / "config.json"
    cfg = json.loads(cfg_path.read_text()) if cfg_path.exists() else {}
    return {
        "name": cfg.get("name", "filecoin"),
        "version": cfg.get("version"),
        "description": cfg.get("description"),
        "endpoints": list((cfg.get("endpoints") or {}).keys()),
        "rpc_url": client.rpc_url,
        "gateway": client.gateway,
        "lighthouse": bool(client.lighthouse_key),
    }


@app.get("/api/status")
def status():
    return client.status()


@app.post("/api/put")
async def put(request: Request, file: Optional[UploadFile] = File(None)):
    """Accepts JSON body `{data, name?}` OR a multipart upload field `file`."""
    if file is not None:
        payload = await file.read()
        return client._upload_bytes(payload, file.filename or "upload.bin")

    body: Any
    try:
        body = await request.json()
    except Exception:
        raw = await request.body()
        if not raw:
            raise HTTPException(400, "empty body")
        return client._upload_bytes(raw, "upload.bin")

    if isinstance(body, dict) and "data" in body:
        return client.put(body["data"], name=body.get("name"))
    return client.put(body)


@app.get("/api/get/{cid}")
def get(cid: str, raw: bool = False):
    try:
        content = client._fetch(cid)
    except Exception as e:
        raise HTTPException(404, str(e))
    if raw:
        return Response(content=content, media_type="application/octet-stream")
    try:
        text = content.decode()
    except UnicodeDecodeError:
        return Response(content=content, media_type="application/octet-stream")
    try:
        return JSONResponse(json.loads(text))
    except json.JSONDecodeError:
        return Response(content=text, media_type="text/plain; charset=utf-8")


@app.get("/api/list")
def list_objects(limit: int = 100):
    return {"items": client.list(limit=limit)}


@app.delete("/api/rm/{cid}")
def rm(cid: str):
    return client.rm(cid)


@app.get("/api/deals/{cid}")
def deals(cid: str):
    return client.deals(cid)


@app.get("/api/chain/head")
def chain_head():
    return client.chain_head()


@app.post("/api/rpc")
async def rpc(request: Request):
    body = await request.json()
    method = body.get("method")
    params = body.get("params") or []
    if not method:
        raise HTTPException(400, "missing method")
    return client.rpc(method, params)


# ─── App (static frontend) ──────────────────────────────────────────────────

if APP_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(APP_DIR)), name="static")

    @app.get("/", response_class=HTMLResponse)
    def index():
        index_html = APP_DIR / "index.html"
        if index_html.exists():
            return HTMLResponse(index_html.read_text())
        return HTMLResponse("<h1>filecoin</h1><p>app/index.html missing</p>")
