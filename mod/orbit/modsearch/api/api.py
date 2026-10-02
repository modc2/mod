"""
modsearch api — semantic search over modules (or any short documents).

    GET  /health                      liveness + which scorer is live
    GET  /search?q=&k=                rank this box's fleet (orbit/ + core/)
    POST /search {query, docs?, k?}   rank the docs YOU send: [{id, text, name?}]

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


def _warm():
    # Load the encoder and embed the fleet once at boot, so the first search
    # someone types is a query encode, not a 300-blurb cold start.
    try:
        ENCODER.encode([d['text'][:engine.MAX_TEXT] for d in engine.fleet_docs(FLEET_ROOTS)])
    except Exception:
        pass


threading.Thread(target=_warm, daemon=True).start()


class Doc(BaseModel):
    id: str
    text: str = ''
    name: Optional[str] = None


class SearchRequest(BaseModel):
    query: str
    docs: Optional[List[Doc]] = None
    k: int = 50


@app.get('/health')
def health():
    return {'ok': True, 'module': 'modsearch', 'model': ENCODER.model_name,
            'encoder': 'error' if ENCODER.error else ('ready' if ENCODER._model else 'loading'),
            'error': ENCODER.error}


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
