"""
Unified model gateway — BYOK access to every provider in providers.json,
for every TYPE of model: chat, image, embedding, speech, transcription, rerank.

Strict BYOK: the server never reads from disk-stored API keys. Every request
must supply its key via the `X-API-Key` header (or `Authorization: Bearer ...`),
or several at once via `X-API-Keys: {"openrouter": "...", "chutes": "..."}`.
Keys live only in memory for the duration of a single request and are
forwarded to the upstream provider — they are not logged or persisted.

Models from all providers combine into one catalog: each is addressed by a
ref `<provider>:<model>` (e.g. `chutes:deepseek-ai/DeepSeek-V3`), which every
run endpoint accepts in place of a separate provider field. Callers may add
their own OpenAI-compatible endpoints per request with
`X-Custom-Providers: {"c-mine": {"url": "https://…", "label": "Mine"}}`
(public https hosts only — the server is the one fetching them).
"""

import json
import os
import time
from pathlib import Path
from typing import Optional

import openai
import requests
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

try:  # `uvicorn app.server:app` from the module root
    from app import adapters
    from app.catalog import (
        KINDS, PROVIDERS, ProviderError, catalog as combined_catalog, list_models,
        make_client, market as build_market, market_key, parse_ref, provider_cfg, public_info, registry,
    )
except ImportError:  # `python app/server.py`
    import adapters
    from catalog import (
        KINDS, PROVIDERS, ProviderError, catalog as combined_catalog, list_models,
        make_client, market as build_market, market_key, parse_ref, provider_cfg, public_info, registry,
    )

APP_DIR = Path(__file__).parent
INDEX_PATH = APP_DIR / 'index.html'


app = FastAPI(title="model-gateway", version="0.4.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_methods=['*'],
    allow_headers=['*'],
)


def _status(code: int) -> int:
    # Cloudflare replaces 5xx bodies with its own page; keep upstream detail readable.
    return 424 if code >= 500 else code


@app.exception_handler(ProviderError)
def provider_error(_req: Request, e: ProviderError):
    return JSONResponse({'detail': e.detail}, status_code=_status(e.status))


@app.exception_handler(openai.APIStatusError)
def upstream_status_error(_req: Request, e: openai.APIStatusError):
    return JSONResponse({'detail': _upstream_detail(e)}, status_code=_status(e.status_code))


@app.exception_handler(openai.APIError)
def upstream_error(_req: Request, e: openai.APIError):
    return JSONResponse({'detail': str(e)}, status_code=424)


def _upstream_detail(e: openai.APIStatusError) -> str:
    try:
        body = e.response.json()
        err = body.get('error') if isinstance(body, dict) else None
        msg = err.get('message') if isinstance(err, dict) else (err or body.get('detail') or body)
        return f'{e.status_code}: {msg}'[:600]
    except Exception:
        return str(e)[:600]


# ── helpers ───────────────────────────────────────────────────────────

def single_key(req: Request) -> Optional[str]:
    key = req.headers.get('x-api-key')
    if not key:
        auth = req.headers.get('authorization') or ''
        if auth.lower().startswith('bearer '):
            key = auth.split(' ', 1)[1].strip()
    return key or None


def _json_header(req: Request, name: str) -> dict:
    raw = req.headers.get(name)
    if not raw:
        return {}
    try:
        val = json.loads(raw)
    except ValueError:
        val = None
    if not isinstance(val, dict):
        raise HTTPException(status_code=400, detail=f'{name} must be a JSON object')
    return val


def providers_for(req: Request) -> dict:
    """Registry + this caller's custom endpoints (X-Custom-Providers)."""
    return registry(_json_header(req, 'x-custom-providers'))


def key_map(req: Request, providers: dict) -> dict:
    keys = _json_header(req, 'x-api-keys')
    return {k: v for k, v in keys.items() if k in providers and isinstance(v, str) and v}


def key_for(req: Request, pid: str, providers: dict, required: bool = True) -> Optional[str]:
    key = key_map(req, providers).get(pid) or single_key(req)
    if not key and required and not providers[pid].get('custom'):
        raise ProviderError(401, f"BYOK: supply a {pid} key via X-API-Key or X-API-Keys")
    return key


class Target:
    """Resolved (provider, model, client) for one run request."""

    def __init__(self, req: Request, ref: Optional[str], provider: Optional[str] = None):
        self.providers = providers_for(req)
        self.pid, self.model = parse_ref(ref, provider, self.providers)
        self.cfg = self.providers[self.pid]
        self.key = key_for(req, self.pid, self.providers)
        self.client = make_client(self.pid, self.key, providers=self.providers)

    def shapes(self) -> Optional[list]:
        """Wire shapes this model accepts, for providers that declare them per model (SayGM)."""
        if not self.cfg.get('model_shapes'):
            return None
        public = self.cfg.get('public_models')  # public catalog: don't let a bad key break the lookup
        for row in list_models(self.pid, None if public else self.key, providers=self.providers):
            if row['id'] == self.model:
                return row.get('shapes')
        return None


# ── catalog routes ────────────────────────────────────────────────────

@app.get('/health')
def health():
    return {'ok': True, 'time': time.time(), 'providers': list(PROVIDERS.keys()), 'kinds': list(KINDS)}


@app.get('/providers')
def providers():
    return [public_info(cfg) for cfg in PROVIDERS.values()]


@app.get('/models')
def models(provider: str, request: Request, refresh: bool = False):
    provs = providers_for(request)
    cfg = provider_cfg(provider, provs)
    key = key_for(request, provider, provs, required=not cfg.get('public_models'))
    out = list_models(provider, key, refresh=refresh, providers=provs)
    return {'provider': provider, 'count': len(out), 'models': out}


@app.get('/check')
def check(provider: str, request: Request):
    """Validate a key by listing with it (never cached): {ok, count} or {ok:false, detail}."""
    provs = providers_for(request)
    key = key_for(request, provider, provs)
    t0 = time.time()
    try:
        out = list_models(provider, key, refresh=True, providers=provs)
    except ProviderError as e:
        return {'ok': False, 'provider': provider, 'detail': e.detail}
    return {'ok': True, 'provider': provider, 'count': len(out), 'ms': int((time.time() - t0) * 1000)}


@app.get('/catalog')
def catalog(request: Request, search: Optional[str] = None, kind: Optional[str] = None, refresh: bool = False):
    """All providers' models (every type) in one list. Keys via X-API-Keys (JSON map)."""
    provs = providers_for(request)
    return combined_catalog(key_map(request, provs), search=search, refresh=refresh, providers=provs, kind=kind)


# ── run: chat ─────────────────────────────────────────────────────────

class ChatMessage(BaseModel):
    role: str  # "system" | "user" | "assistant"
    content: str


class ChatRequest(BaseModel):
    provider: Optional[str] = None  # omit (or "all") when model is a "<provider>:<model>" ref
    model: Optional[str] = None
    messages: list[ChatMessage]
    temperature: float = 1.0
    max_tokens: int = 4096
    stream: bool = True


@app.post('/chat')
def chat(payload: ChatRequest, request: Request):
    """UI chat. Goes through the same OpenAI-standard adapter as /v1/chat/completions,
    so models that only speak Anthropic messages / Gemini generateContent work too."""
    t = Target(request, payload.model, payload.provider)
    body = {'messages': [m.model_dump() for m in payload.messages],
            'max_tokens': payload.max_tokens, 'temperature': payload.temperature}
    shapes = t.shapes()

    if not payload.stream:
        res = adapters.chat(t.cfg, t.key, t.model, body, shapes)
        return {'provider': t.pid, 'model': t.model,
                'content': ((res.get('choices') or [{}])[0].get('message') or {}).get('content')}

    def gen():
        usage = None
        try:
            for chunk in adapters.stream(t.cfg, t.key, t.model, body, shapes):
                usage = chunk.get('usage') or usage
                for ch in chunk.get('choices') or []:
                    content = (ch.get('delta') or {}).get('content')
                    if content:
                        yield f"data: {json.dumps({'delta': content})}\n\n"
            yield f"data: {json.dumps({'done': True, 'provider': t.pid, 'model': t.model, 'usage': usage})}\n\n"
        except ProviderError as e:
            yield f"data: {json.dumps({'error': e.detail})}\n\n"
        except Exception as e:  # surface upstream errors over SSE
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(gen(), media_type='text/event-stream')


# ── run: image ────────────────────────────────────────────────────────

class ImageRequest(BaseModel):
    model: str
    provider: Optional[str] = None
    prompt: str
    size: str = '1024x1024'
    n: int = 1


@app.post('/images')
def images(payload: ImageRequest, request: Request):
    """Text -> image. OpenRouter serves image models through chat completions
    (modalities); everyone else through /images/generations. Always returns
    {images: [url or data: URL]}."""
    t = Target(request, payload.model, payload.provider)
    n = max(1, min(payload.n, 4))
    out = []
    if t.pid == 'openrouter':
        res = t.client.chat.completions.create(
            model=t.model, messages=[{'role': 'user', 'content': payload.prompt}],
            extra_body={'modalities': ['image', 'text']},
        )
        msg = res.choices[0].message.model_dump() if res.choices else {}
        for im in msg.get('images') or []:
            url = (im.get('image_url') or {}).get('url')
            if url:
                out.append(url)
        text = msg.get('content')
    else:
        res = t.client.images.generate(model=t.model, prompt=payload.prompt, size=payload.size, n=n)
        for d in res.data or []:
            if d.b64_json:
                out.append('data:image/png;base64,' + d.b64_json)
            elif d.url:
                out.append(d.url)
        text = None
    if not out:
        raise HTTPException(status_code=424, detail=f'{t.pid} returned no image' + (f': {text[:300]}' if text else ''))
    return {'provider': t.pid, 'model': t.model, 'images': out, 'text': text}


# ── run: embeddings ───────────────────────────────────────────────────

class EmbedRequest(BaseModel):
    model: str
    provider: Optional[str] = None
    input: list[str]


@app.post('/embeddings')
def embeddings(payload: EmbedRequest, request: Request):
    """Embed up to 32 strings; returns full vectors plus a cosine-similarity matrix."""
    t = Target(request, payload.model, payload.provider)
    texts = [s for s in payload.input if s.strip()][:32]
    if not texts:
        raise HTTPException(status_code=400, detail='input: give at least one non-empty string')
    res = t.client.embeddings.create(model=t.model, input=texts)
    vecs = [d.embedding for d in sorted(res.data, key=lambda d: d.index)]

    def norm(v):
        return sum(x * x for x in v) ** 0.5 or 1.0

    norms = [norm(v) for v in vecs]
    sim = [[round(sum(a * b for a, b in zip(vi, vj)) / (norms[i] * norms[j]), 4)
            for j, vj in enumerate(vecs)] for i, vi in enumerate(vecs)]
    return {'provider': t.pid, 'model': t.model, 'dims': len(vecs[0]) if vecs else 0,
            'input': texts, 'vectors': vecs, 'similarity': sim}


# ── run: speech (TTS) ─────────────────────────────────────────────────

class SpeechRequest(BaseModel):
    model: str
    provider: Optional[str] = None
    input: str
    voice: str = 'alloy'
    format: str = 'mp3'


@app.post('/speech')
def speech(payload: SpeechRequest, request: Request):
    t = Target(request, payload.model, payload.provider)
    res = t.client.audio.speech.create(
        model=t.model, input=payload.input[:4000], voice=payload.voice, response_format=payload.format,
    )
    mime = {'mp3': 'audio/mpeg', 'wav': 'audio/wav', 'opus': 'audio/ogg', 'aac': 'audio/aac', 'flac': 'audio/flac'}
    return Response(content=res.content, media_type=mime.get(payload.format, 'application/octet-stream'))


# ── run: transcription (STT) ──────────────────────────────────────────

@app.post('/transcribe')
async def transcribe(request: Request, model: str = Form(...), provider: Optional[str] = Form(None),
                     file: UploadFile = File(...)):
    t = Target(request, model, provider)
    data = await file.read()
    if len(data) > 25 * 1024 * 1024:
        raise HTTPException(status_code=413, detail='audio file over 25 MB')
    res = t.client.audio.transcriptions.create(model=t.model, file=(file.filename or 'audio', data))
    return {'provider': t.pid, 'model': t.model, 'text': getattr(res, 'text', None) or str(res)}


# ── run: rerank ───────────────────────────────────────────────────────

class RerankRequest(BaseModel):
    model: str
    provider: Optional[str] = None
    query: str
    documents: list[str]


@app.post('/rerank')
def rerank(payload: RerankRequest, request: Request):
    """Cohere-style /rerank, which the OpenAI SDK has no method for."""
    t = Target(request, payload.model, payload.provider)
    docs = [d for d in payload.documents if d.strip()][:64]
    try:
        resp = requests.post(
            t.cfg['url'].rstrip('/') + '/rerank',
            headers={'Authorization': f'Bearer {t.key}'} if t.key else {},
            json={'model': t.model, 'query': payload.query, 'documents': docs},
            timeout=60, allow_redirects=not t.cfg.get('custom'),
        )
    except requests.RequestException as e:
        raise HTTPException(status_code=424, detail=f'{t.pid}/rerank failed: {e}')
    if resp.status_code >= 400:
        raise HTTPException(status_code=_status(resp.status_code), detail=f'{t.pid}: {resp.text[:400]}')
    body = resp.json()
    rows = body.get('results') or body.get('data') or []
    ranked = [{'index': r.get('index'), 'score': r.get('relevance_score', r.get('score')),
               'document': docs[r['index']] if isinstance(r.get('index'), int) and r['index'] < len(docs) else None}
              for r in rows]
    return {'provider': t.pid, 'model': t.model, 'results': ranked}


# ── market ────────────────────────────────────────────────────────────

@app.get('/market')
def market(request: Request, search: Optional[str] = None, kind: Optional[str] = None,
           min_offers: int = 1, refresh: bool = False):
    """Every model as a market row: the same model from every provider, offers
    sorted cheapest first (blended USD/1M = (3*in + out)/4), with spread."""
    provs = providers_for(request)
    keys = key_map(request, provs)
    cat = combined_catalog(keys, search=search, refresh=refresh, providers=provs, kind=kind)
    rows = build_market(cat['models'], min_offers=max(1, min_offers))
    return {'count': len(rows), 'offers': cat['count'], 'providers': cat['counts'],
            'errors': cat['errors'], 'missing': cat['missing'], 'markets': rows}


# ── /v1: one OpenAI-compatible API over every provider ────────────────
#
# Point any OpenAI SDK at  base_url=<host>/model/api/v1  and use
#   model="<provider>:<model>"   e.g. "saygm:claude-sonnet-5", "venice:llama-3.3-70b"
# or a bare model name ("kimi-k3") to take the cheapest offer among the
# providers you sent keys for (X-API-Keys). Auth: Bearer <key> for one
# provider, or X-API-Keys: {"saygm": "...", "openrouter": "..."} for many.

def _oai_error(status: int, msg: str, typ: str = 'invalid_request_error'):
    return JSONResponse({'error': {'message': msg, 'type': typ, 'code': status}}, status_code=_status(status))


def route(req: Request, ref: Optional[str]) -> Target:
    """Resolve a /v1 `model`: a '<provider>:<model>' ref, or a bare name routed
    to the cheapest market offer the caller holds a key for."""
    provs = providers_for(req)
    if ref and ':' in ref and ref.split(':', 1)[0] in provs:
        return Target(req, ref)
    if not ref:
        raise ProviderError(400, "model is required ('<provider>:<model>' or a model name)")
    keys = key_map(req, provs)
    usable = {pid for pid in provs if keys.get(pid) or provs[pid].get('custom')}
    if not usable:
        raise ProviderError(400, f"'{ref}' has no provider prefix; use '<provider>:{ref}', "
                                 'or send X-API-Keys so the market can pick the cheapest provider you hold a key for')
    cat = combined_catalog(keys, providers=provs)
    want = market_key(ref)
    offers = [m for m in cat['models'] if m['provider'] in usable and (m['id'] == ref or market_key(m['id']) == want)]
    if not offers:
        raise ProviderError(404, f"no provider you hold a key for ({sorted(usable)}) lists '{ref}'")
    offers.sort(key=lambda m: (m['price_in'] is None, (m['price_in'] or 0) * 3 + (m['price_out'] or 0)))
    return Target(req, offers[0]['ref'])


@app.get('/v1/models')
def v1_models(request: Request, kind: Optional[str] = None):
    """OpenAI list shape; ids are '<provider>:<model>' refs, plus pricing/kind extras."""
    provs = providers_for(request)
    cat = combined_catalog(key_map(request, provs), providers=provs, kind=kind)
    return {'object': 'list', 'data': [{
        'id': m['ref'], 'object': 'model', 'created': int(m['created'] or 0), 'owned_by': m['provider'],
        'name': m['name'], 'kind': m['kind'], 'context_length': m['context_length'],
        'pricing': {'input': m['price_in'], 'output': m['price_out'], 'image': m['price_image'], 'unit': 'usd_per_mtok'},
        'market': market_key(m['id']), 'tee': m['tee'],
    } for m in cat['models']]}


@app.post('/v1/chat/completions')
async def v1_chat(request: Request):
    try:
        body = await request.json()
    except ValueError:
        return _oai_error(400, 'body must be JSON')
    try:
        t = route(request, body.get('model'))
        shapes = t.shapes()
        hdr = {'X-Model-Ref': f'{t.pid}:{t.model}'}
        if not body.get('stream'):
            res = adapters.chat(t.cfg, t.key, t.model, body, shapes)
            res['model'] = f'{t.pid}:{t.model}'
            return JSONResponse(res, headers=hdr)
        it = iter(adapters.stream(t.cfg, t.key, t.model, body, shapes))
        first = next(it, None)  # surface upstream auth/model errors as a status, not mid-stream
    except ProviderError as e:
        return _oai_error(e.status, e.detail)

    def gen():
        try:
            for chunk in ([first] if first else []):
                yield f"data: {json.dumps({**chunk, 'model': hdr['X-Model-Ref']})}\n\n"
            for chunk in it:
                yield f"data: {json.dumps({**chunk, 'model': hdr['X-Model-Ref']})}\n\n"
        except ProviderError as e:
            yield f"data: {json.dumps({'error': {'message': e.detail, 'code': e.status}})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': {'message': str(e)[:500]}})}\n\n"
        yield 'data: [DONE]\n\n'

    return StreamingResponse(gen(), media_type='text/event-stream', headers=hdr)


def _passthrough(request: Request, body: dict, path: str):
    """Forward an OpenAI-shaped JSON body to the provider's own <url><path>, model rewritten."""
    t = route(request, body.get('model'))
    try:
        r = requests.post(t.cfg['url'].rstrip('/') + path, json={**body, 'model': t.model},
                          headers={'Authorization': f'Bearer {t.key}'} if t.key else {},
                          timeout=300, allow_redirects=not t.cfg.get('custom'))
    except requests.RequestException as e:
        raise ProviderError(502, f'{t.pid}{path} failed: {e}')
    if r.status_code >= 400:
        raise ProviderError(r.status_code, f'{t.pid}: {r.text[:400]}')
    ctype = r.headers.get('content-type', 'application/json')
    return Response(content=r.content, media_type=ctype.split(';')[0], headers={'X-Model-Ref': f'{t.pid}:{t.model}'})


for _path in ('/embeddings', '/images/generations', '/audio/speech', '/moderations', '/rerank'):
    def _make(path):
        async def handler(request: Request):
            try:
                body = await request.json()
                return _passthrough(request, body, path)
            except ProviderError as e:
                return _oai_error(e.status, e.detail)
            except ValueError:
                return _oai_error(400, 'body must be JSON')
        handler.__name__ = 'v1' + path.replace('/', '_')
        return handler
    app.post('/v1' + _path)(_make(_path))


@app.post('/v1/audio/transcriptions')
async def v1_transcriptions(request: Request, model: str = Form(...), file: UploadFile = File(...)):
    try:
        t = route(request, model)
        data = await file.read()
        res = t.client.audio.transcriptions.create(model=t.model, file=(file.filename or 'audio', data))
        return {'text': getattr(res, 'text', None) or str(res)}
    except ProviderError as e:
        return _oai_error(e.status, e.detail)


# ── UI ────────────────────────────────────────────────────────────────

@app.get('/model')
@app.get('/model/')
@app.get('/')
def index():
    if INDEX_PATH.exists():
        return FileResponse(INDEX_PATH, headers={'Cache-Control': 'no-cache'})
    return JSONResponse({'error': 'index.html missing', 'path': str(INDEX_PATH)})


# ── entrypoint ────────────────────────────────────────────────────────

def serve(host: str = '0.0.0.0', port: int = 50110, reload: bool = False):
    import uvicorn
    uvicorn.run('app.server:app' if reload else app, host=host, port=port, reload=reload)


if __name__ == '__main__':
    port = int(os.environ.get('MODEL_GATEWAY_PORT', '50110'))
    serve(port=port)
