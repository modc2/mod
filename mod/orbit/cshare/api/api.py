"""cshare API — the Agent Protocol hub over HTTP.

Serves the protocol card (including at /.well-known/agent.json), the unified
artifact index, and resolve/share/install by CID. Gateway routes
/cshare/api → :50290 per the mod protocol URL convention.
"""
import os
import sys
import importlib.util
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

MODULE_DIR = Path(__file__).resolve().parent.parent

app = FastAPI(title="cshare — Agent Protocol hub", version="1.0.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

_mod = None


def get_mod():
    global _mod
    if _mod is None:
        spec = importlib.util.spec_from_file_location(
            "_cshare_mod", MODULE_DIR / "mod.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _mod = module.Mod()
    return _mod


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except (KeyError, FileNotFoundError) as e:
        raise HTTPException(404, str(e))
    except (ValueError, PermissionError) as e:
        raise HTTPException(400, str(e))


class ValidateRequest(BaseModel):
    bundle: Any


class ShareRequest(BaseModel):
    kind: str
    id: str


class InstallRequest(BaseModel):
    cid: str


class ForwardRequest(BaseModel):
    action: Optional[str] = None
    params: Dict[str, Any] = {}


@app.get("/")
def root():
    """Null call returns the protocol card (mod protocol convention)."""
    return get_mod().card()


@app.get("/card")
def card():
    return get_mod().card()


@app.get("/.well-known/agent.json")
def well_known():
    return get_mod().card()


@app.get("/kinds")
def kinds():
    return get_mod().kinds()


@app.get("/index")
def index(kind: Optional[str] = None, q: Optional[str] = None,
          tag: Optional[str] = None):
    return _call(get_mod().index, kind=kind, q=q, tag=tag)


@app.get("/resolve/{cid}")
def resolve(cid: str):
    return _call(get_mod().resolve, cid)


@app.post("/validate")
def validate(req: ValidateRequest):
    return get_mod().validate(req.bundle)


@app.post("/share")
def share(req: ShareRequest):
    return _call(get_mod().share, req.kind, req.id)


@app.post("/install")
def install(req: InstallRequest):
    return _call(get_mod().install, req.cid)


@app.post("/publish")
def publish():
    return _call(get_mod().publish)


@app.post("/forward")
def forward(req: ForwardRequest):
    return _call(get_mod().forward, action=req.action, **req.params)


@app.get("/health")
def health():
    return {"status": "ok", "protocol": "agent/1.0"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 50290)))
