"""
Arweave API — FastAPI server exposing the ArweaveClient and serving the static app.

Run via the module:
    m arweave/serve

Or directly:
    uvicorn api:app --port 50151
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

MODULE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(MODULE_DIR))

from arweave import ArweaveClient  # noqa: E402


app = FastAPI(
    title="Arweave Mod API",
    description="Store and retrieve data on Arweave via a public gateway, with optional wallet uploads.",
    version="0.1.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

client = ArweaveClient()
APP_DIR = MODULE_DIR / "app"


# ─── API routes (under /api) ────────────────────────────────────────────────


@app.get("/api/health")
def health():
    return {"ok": True, "service": "arweave"}


@app.get("/api/info")
def info():
    cfg_path = MODULE_DIR / "config.json"
    cfg = json.loads(cfg_path.read_text()) if cfg_path.exists() else {}
    return {
        "name": cfg.get("name", "arweave"),
        "version": cfg.get("version"),
        "description": cfg.get("description"),
        "endpoints": list((cfg.get("endpoints") or {}).keys()),
        "gateway": client.gateway,
        "wallet": bool(client.wallet_path),
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


@app.get("/api/get/{txid}")
def get(txid: str, raw: bool = False):
    try:
        content = client._fetch(txid)
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


@app.delete("/api/rm/{txid}")
def rm(txid: str):
    return client.rm(txid)


@app.get("/api/tx/{txid}")
def tx(txid: str):
    return client.tx(txid)


@app.get("/api/price/{num_bytes}")
def price(num_bytes: int):
    return client.price(num_bytes)


@app.get("/api/network")
def network():
    return client.info()


# ─── App (static frontend) ──────────────────────────────────────────────────

if APP_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(APP_DIR)), name="static")

    @app.get("/", response_class=HTMLResponse)
    def index():
        index_html = APP_DIR / "index.html"
        if index_html.exists():
            return HTMLResponse(index_html.read_text())
        return HTMLResponse("<h1>arweave</h1><p>app/index.html missing</p>")
