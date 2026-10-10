"""
modsearch api — semantic search over modules (or any short documents).

    GET  /health                      liveness + which scorer is live
    GET  /search?q=&k=                rank this box's fleet (orbit/ + core/)
    POST /search {query, docs?, k?}   rank the docs YOU send: [{id, text, name?}]
    POST /embed {texts, backend?, kind?}  raw unit vectors (uncached) for
                                      callers that keep their own index.
                                      backend defaults to minilm: x402's 37k
                                      stored vectors are MiniLM and it checks
    GET  /mods                        every fleet mod's embedding hash
    GET  /mods/{name}?vector=1        one mod's row (+ its vector)
    POST /reindex                     re-read the fleet, re-hash what changed

POST with your own docs is the reusable door: a caller that already holds a
list (the build hub, which only holds what its viewer may see) sends it and
gets ids back — this service never decides who sees what.

Binds 127.0.0.1 by default (route: false). It is a local helper, not a
public endpoint: GET /search reads private modules' descriptions.

Run: m modsearch/serve   (pm2 `modsearch`, uvicorn on config `port`)
"""
import json
import sys
import threading
from pathlib import Path
from typing import List, Optional

MODULE_DIR = Path(__file__).resolve().parent.parent
if str(MODULE_DIR) not in sys.path:
    sys.path.append(str(MODULE_DIR))

from fastapi import FastAPI, HTTPException  # noqa: E402
from pydantic import BaseModel  # noqa: E402

import modsearch as engine  # noqa: E402

CONFIG = json.loads((MODULE_DIR / 'config.json').read_text())
FLEET_ROOTS = [MODULE_DIR.parent, MODULE_DIR.parent.parent / 'core']

app = FastAPI(title='modsearch', description=CONFIG.get('description', ''))
ENCODER = engine.Encoder()
INDEX = engine.ModIndex(FLEET_ROOTS, ENCODER)
REINDEX_SEC = 600


def _warm():
    # Embed the fleet at boot so the first search someone types is a query
    # encode, not a 300-blurb cold start — then keep each mod's embedding hash
    # current as mods are created and re-described.
    import time
    while True:
        try:
            INDEX.rebuild()
        except Exception:
            pass
        time.sleep(REINDEX_SEC)


threading.Thread(target=_warm, daemon=True).start()


class Doc(BaseModel):
    id: str
    text: str = ''
    name: Optional[str] = None


class SearchRequest(BaseModel):
    query: str
    docs: Optional[List[Doc]] = None
    k: int = 50


class EmbedRequest(BaseModel):
    texts: List[str]
    backend: str = 'minilm'      # minilm | liquid | auto
    kind: str = 'document'       # query | document (liquid is asymmetric)


MAX_EMBED = 512


@app.get('/health')
def health():
    return {'ok': True, 'module': 'modsearch', 'model': ENCODER.model_name,
            'encoder': ('ready' if ENCODER.active else ('error' if ENCODER.error else 'loading')),
            'error': ENCODER.error, 'backends': ENCODER.backend_state(),
            'index': {k: v for k, v in INDEX.data.items() if k != 'mods'}}


@app.get('/search')
def search_fleet(q: str = '', k: int = 30):
    res = engine.search(q, engine.fleet_docs(FLEET_ROOTS), k=k, encoder=ENCODER)
    return res


@app.post('/search')
def search(req: SearchRequest):
    if len(req.docs or []) > engine.MAX_DOCS:
        raise HTTPException(400, f'at most {engine.MAX_DOCS} docs per search')
    docs = [d.model_dump() for d in req.docs] if req.docs is not None else engine.fleet_docs(FLEET_ROOTS)
    return engine.search(req.query, docs, k=req.k, encoder=ENCODER)


@app.post('/embed')
def embed(req: EmbedRequest):
    if len(req.texts) > MAX_EMBED:
        raise HTTPException(400, f'at most {MAX_EMBED} texts per call')
    if not req.texts:
        return {'model': None, 'dim': 0, 'vectors': []}
    backend, vecs = ENCODER.embed(req.texts, req.kind,
                                  None if req.backend == 'auto' else req.backend)
    if vecs is None:
        raise HTTPException(409, f'encoder unavailable: {ENCODER.error}')
    return {'model': backend.model, 'backend': backend.name, 'kind': req.kind,
            'dim': len(vecs[0]), 'vectors': vecs}


@app.get('/mods')
def mods():
    """Every fleet mod's text hash + embedding hash (no vectors)."""
    return INDEX.data


@app.get('/mods/{name}')
def mod(name: str, vector: bool = False):
    row = INDEX.get(name, vector=vector)
    if row is None:
        raise HTTPException(404, f'no mod {name!r} in the index')
    return row


@app.post('/reindex')
def reindex():
    return INDEX.rebuild()
