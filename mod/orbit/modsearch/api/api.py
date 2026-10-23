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

Agent protocol (the orbit/agent contract — GET /agents + POST /run/stream):

    GET  /agents                      this module's roster: one search agent
    POST /run {query, docs?, k?}      one ranked search as an agent run
    POST /run/stream                  same, as SSE step/done/error events

so any console that mounts agent mods (orbit/build's roster) can mount
modsearch and drive the hub's agent search over the standard contract
instead of a bespoke seam. A run IS a search: steps show what was ranked
and how, `done` carries the full search payload. charged is always null —
this agent runs local models and bills nobody.

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


# ── agent protocol ────────────────────────────────────────────────────
# GET /agents + POST /run(/stream) — the orbit/agent shape, so consoles
# that mount agent mods can drive this search as a standard agent instead
# of calling the bespoke /search. One agent, one skill: rank documents.

class RunRequest(BaseModel):
    query: str = ''
    docs: Optional[List[Doc]] = None
    k: int = 50
    # fields a mounting console sends that a search has no use for —
    # accepted so the contract call never 422s on the standard body
    goal: Optional[str] = None
    prompt: Optional[str] = None
    model: Optional[str] = None
    agent_type: Optional[str] = None
    key: Optional[str] = None
    path: Optional[str] = None
    timeout: Optional[int] = None


def _agent_card():
    state = 'ready' if ENCODER.active else ('error' if ENCODER.error else 'loading')
    return {
        'name': 'modsearch',
        'label': '⌕ modsearch',
        'description': ('Semantic search agent — ranks this fleet’s modules '
                        '(or any docs you send) by what the query means. '
                        'Liquid LFM embeddings + BM25, local, no key.'),
        'module': 'modsearch',
        # lexical BM25 still answers when no encoder is up, so the agent
        # is available as long as the process is — mode says how well
        'available': True,
        'encoder': state,
        'model': ENCODER.model_name,
        'search': True,
    }


@app.get('/agents')
def agents():
    """The contract's roster: this module hosts exactly one agent."""
    return {'agents': [_agent_card()]}


def _run_search(req: RunRequest):
    """One agent run = one ranked search. Returns (steps, search payload).

    The query a mounting console sends may carry its 'Working directory:'
    preamble — a search has no cwd, so everything after the first blank
    line is treated as the real query when that preamble is present.
    """
    query = req.query.strip()
    if query.startswith('Working directory:') and '\n\n' in query:
        query = query.split('\n\n', 1)[1].strip()
    if len(req.docs or []) > engine.MAX_DOCS:
        raise HTTPException(400, f'at most {engine.MAX_DOCS} docs per search')
    docs = [d.model_dump() for d in req.docs] if req.docs is not None else engine.fleet_docs(FLEET_ROOTS)
    steps = [{'tool': 'search', 'params': {
        'query': query, 'docs': len(docs),
        'scope': 'caller docs' if req.docs is not None else 'fleet',
    }}]
    res = engine.search(query, docs, k=req.k, encoder=ENCODER)
    top = [r['id'] for r in (res.get('results') or [])[:5]]
    summary = (f"{len(res.get('results') or [])} match(es), mode {res.get('mode')}"
               + (f" — top: {', '.join(top)}" if top else ''))
    steps.append({'tool': 'finish', 'params': {'summary': summary, 'search': res}})
    return steps, res


@app.post('/run')
def run(req: RunRequest):
    """Blocking agent run — {result: steps, search, task_id}."""
    import uuid
    if not req.query.strip():
        raise HTTPException(400, 'query is required')
    steps, res = _run_search(req)
    return {'result': steps, 'search': res, 'task_id': uuid.uuid4().hex[:12],
            'charged': None}


@app.post('/run/stream')
def run_stream(req: RunRequest):
    """Streaming agent run — SSE data: {type: step|done|error, ...}."""
    import uuid
    from fastapi.responses import StreamingResponse
    task_id = uuid.uuid4().hex[:12]

    def gen():
        try:
            if not req.query.strip():
                yield 'data: ' + json.dumps({'type': 'error', 'error': 'query is required',
                                             'task_id': task_id}) + '\n\n'
                return
            steps, res = _run_search(req)
            for s in steps:
                yield 'data: ' + json.dumps({'type': 'step', 'step': s}) + '\n\n'
            yield 'data: ' + json.dumps({'type': 'done', 'result': steps, 'search': res,
                                         'task_id': task_id, 'charged': None}) + '\n\n'
        except HTTPException as e:
            yield 'data: ' + json.dumps({'type': 'error', 'error': str(e.detail),
                                         'task_id': task_id}) + '\n\n'
        except Exception as e:
            yield 'data: ' + json.dumps({'type': 'error', 'error': str(e),
                                         'task_id': task_id}) + '\n\n'

    return StreamingResponse(gen(), media_type='text/event-stream',
                             headers={'Cache-Control': 'no-cache',
                                      'X-Accel-Buffering': 'no'})
