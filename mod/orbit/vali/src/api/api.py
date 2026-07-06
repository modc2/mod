"""
Vali API

FastAPI backend for the Vali validator module.
Exposes epoch, scoring, results, network, and voting endpoints.
"""

import sys
import time
import traceback
from pathlib import Path
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Ensure mod is importable
MODULE_DIR = Path(__file__).parent.parent.parent  # ~/mod/mod/orbit/vali
SRC_DIR = Path(__file__).parent.parent             # ~/mod/mod/orbit/vali/src
MOD_ROOT = MODULE_DIR.parent.parent.parent         # ~/mod (repo root, so `import mod` finds the framework)

# MOD_ROOT must be first so `import mod` resolves to the framework, not src/mod.py
for p in [str(SRC_DIR), str(MODULE_DIR), str(MOD_ROOT)]:
    if p in sys.path:
        sys.path.remove(p)
# Insert in reverse order so MOD_ROOT ends up at index 0
for p in [str(SRC_DIR), str(MODULE_DIR), str(MOD_ROOT)]:
    sys.path.insert(0, p)

from vali.vali import Vali

app = FastAPI(title="Vali API", description="Validator module — scoring, epochs, scoreboards")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Singleton validator ──────────────────────────────────────────

_vali: Optional[Vali] = None


def get_vali() -> Vali:
    global _vali
    if _vali is None:
        try:
            _vali = Vali(network='local', loop=False, verbose=True)
        except Exception as e:
            raise HTTPException(status_code=503, detail=f"Vali init failed: {e}")
    return _vali


# ── Request models ───────────────────────────────────────────────

class EpochRequest(BaseModel):
    search: Optional[str] = None
    key: Optional[str] = None
    debug: bool = False


class ScoreRequest(BaseModel):
    module: dict


class VoteRequest(BaseModel):
    results: list


class NetworkRequest(BaseModel):
    network: Optional[str] = None
    tempo: Optional[int] = None
    search: Optional[str] = None
    subnet: Optional[str] = None


# ── Endpoints ────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "module": "vali", "time": time.time()}


@app.get("/status")
async def status():
    try:
        v = get_vali()
        return {
            "result": {
                "name": "vali",
                "network": getattr(v, 'network', 'unknown'),
                "subnet": getattr(v, 'subnet', 0),
                "epochs": getattr(v, 'epochs', 0),
                "tempo": getattr(v, 'tempo', 60),
                "batch_size": getattr(v, 'batch_size', 12),
                "timeout": getattr(v, 'timeout', 32),
                "modules": len(getattr(v, 'mods', [])),
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/epoch")
async def epoch(req: EpochRequest):
    try:
        v = get_vali()
        kwargs = {}
        if req.search:
            kwargs['search'] = req.search
        if req.key:
            kwargs['key'] = req.key
        kwargs['debug'] = req.debug
        result = v.epoch(**kwargs)
        if hasattr(result, 'to_dict'):
            result = result.to_dict(orient='records')
        return {"result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"{e}\n{traceback.format_exc()}")


@app.get("/results")
async def results(
    by: str = Query("score", description="Sort by field"),
    ascending: bool = Query(True, description="Sort ascending"),
    max_age: int = Query(10000, description="Max age of results in seconds"),
    page: Optional[int] = Query(None, description="Page number"),
):
    try:
        v = get_vali()
        result = v.results(by=by, ascending=ascending, max_age=max_age, to_dict=True, page=page)
        if hasattr(result, 'to_dict'):
            result = result.to_dict(orient='records')
        return {"result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/score")
async def score_module(req: ScoreRequest):
    try:
        v = get_vali()
        result = v.forward(req.module)
        return {"result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/vote")
async def vote(req: VoteRequest):
    try:
        v = get_vali()
        result = v.vote(req.results)
        return {"result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/network")
async def get_network():
    try:
        v = get_vali()
        return {
            "result": {
                "network": getattr(v, 'network', 'unknown'),
                "subnet": getattr(v, 'subnet', 0),
                "tempo": getattr(v, 'tempo', 60),
                "search": getattr(v, 'search', None),
                "modules": len(getattr(v, 'mods', [])),
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/network")
async def set_network(req: NetworkRequest):
    try:
        v = get_vali()
        kwargs = {}
        if req.network:
            kwargs['network'] = req.network
        if req.tempo:
            kwargs['tempo'] = req.tempo
        if req.search:
            kwargs['search'] = req.search
        if req.subnet:
            kwargs['subnet'] = req.subnet
        result = v.set_network(**kwargs)
        return {"result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/modules")
async def modules():
    try:
        v = get_vali()
        mods = getattr(v, 'mods', [])
        return {"result": mods}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/refresh")
async def refresh_results():
    try:
        v = get_vali()
        result = v.refresh_results()
        return {"result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
