"""
localfs FastAPI backend.

Thin HTTP wrapper around the orbit/localfs Mod class. Exposes put/get/pin/etc
so the static app (or any other client) can drive content-addressable storage
without importing the Python module directly.
"""

import json
import os
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse

# Allow importing the sibling Mod class without installing the package.
_MOD_DIR = Path(__file__).resolve().parent.parent
if str(_MOD_DIR) not in sys.path:
    sys.path.insert(0, str(_MOD_DIR))

from localfs import Mod  # noqa: E402


app = FastAPI(title="localfs", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

mod = Mod()


@app.get("/health")
def health():
    return {"ok": True, "name": "localfs"}


@app.get("/info")
def info():
    return mod.info()


@app.get("/stats")
def stats():
    return mod.stats()


@app.post("/put")
async def put(request: Request):
    ctype = (request.headers.get("content-type") or "").lower()
    if ctype.startswith("application/json"):
        body = await request.json()
        cid = mod.put(body)
    else:
        body = await request.body()
        cid = mod.put(body)
    return {"cid": cid}


@app.get("/get/{cid}")
def get(cid: str):
    try:
        data = mod.get(cid)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    if isinstance(data, (dict, list)):
        return JSONResponse(data)
    if isinstance(data, bytes):
        return PlainTextResponse(data.decode("utf-8", errors="replace"))
    return PlainTextResponse(str(data))


@app.post("/cid")
async def compute_cid(request: Request):
    ctype = (request.headers.get("content-type") or "").lower()
    if ctype.startswith("application/json"):
        body = await request.json()
    else:
        body = await request.body()
    return {"cid": mod.cid(body)}


@app.get("/pins")
def pins():
    return mod.pins()


@app.post("/pin/{cid}")
def pin(cid: str):
    try:
        return mod.pin(cid)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/unpin/{cid}")
def unpin(cid: str):
    return mod.unpin(cid)


@app.delete("/rm/{cid}")
def rm(cid: str):
    return mod.rm(cid)


@app.post("/gc")
def gc(aggressive: bool = False):
    return mod.gc(aggressive=aggressive)
