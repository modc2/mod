"""
embedcode api - FastAPI gateway over embedcode mod

Endpoints:
    GET  /health          - health check
    GET  /status          - module status
    GET  /collections     - list collections
    GET  /collections/{n} - collection info
    POST /embed           - embed a path
    POST /search          - search embeddings
    DELETE /collections/{n} - delete collection
    POST /forward         - mod protocol entry point

Usage:
    uvicorn api:app --host 0.0.0.0 --port 8920 --reload
"""
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List

app = FastAPI(title="Embedcode API", version="1.0.0",
              description="Code embedding and semantic search API")
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

_mod = None

def get_mod():
    global _mod
    if _mod is None:
        from mod import Mod
        _mod = Mod()
    return _mod


# ── Request models ──

class EmbedRequest(BaseModel):
    path: str
    collection: Optional[str] = None
    chunk_size: int = 64
    overlap: int = 16
    extensions: Optional[List[str]] = None

class SearchRequest(BaseModel):
    query: str
    collection: Optional[str] = None
    path: Optional[str] = None
    top_k: int = 10

class ForwardRequest(BaseModel):
    fn: Optional[str] = None
    params: dict = {}


# ── Health ──

@app.get("/health")
def health():
    return {"status": "ok", "module": "embedcode"}


# ── Status ──

@app.get("/status")
def status():
    return get_mod().status()


# ── Collections ──

@app.get("/collections")
def collections():
    return get_mod().collections()

@app.get("/collections/{name}")
def collection_info(name: str):
    return get_mod().info(collection=name)

@app.delete("/collections/{name}")
def delete_collection(name: str):
    result = get_mod().delete(name)
    if 'error' in result:
        raise HTTPException(status_code=404, detail=result['error'])
    return result


# ── Embed ──

@app.post("/embed")
def embed(req: EmbedRequest):
    try:
        result = get_mod().embed(
            path=req.path,
            collection=req.collection,
            chunk_size=req.chunk_size,
            overlap=req.overlap,
            extensions=req.extensions,
        )
        if 'error' in result:
            raise HTTPException(status_code=400, detail=result['error'])
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Search ──

@app.post("/search")
def search(req: SearchRequest):
    try:
        return get_mod().search(
            query=req.query,
            collection=req.collection,
            path=req.path,
            top_k=req.top_k,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Forward (mod protocol) ──

@app.post("/forward")
def forward(req: ForwardRequest):
    try:
        return get_mod().forward(fn=req.fn, **req.params)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
