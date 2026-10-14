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
    from app.catalog import (
        KINDS, PROVIDERS, ProviderError, catalog as combined_catalog, list_models,
        make_client, parse_ref, provider_cfg, public_info, registry,
    )
except ImportError:  # `python app/server.py`
    from catalog import (
        KINDS, PROVIDERS, ProviderError, catalog as combined_catalog, list_models,
        make_client, parse_ref, provider_cfg, public_info, registry,
    )

APP_DIR = Path(__file__).parent
INDEX_PATH = APP_DIR / 'index.html'


app = FastAPI(title="model-gateway", version="0.3.0")
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
        raise HTTPException(status_code=401, detail=f"BYOK: supply a {pid} key via X-API-Key or X-API-Keys")
    return key


class Target:
    """Resolved (provider, model, client) for one run request."""

    def __init__(self, req: Request, ref: Optional[str], provider: Optional[str] = None):
        self.providers = providers_for(req)
        self.pid, self.model = parse_ref(ref, provider, self.providers)
        self.cfg = self.providers[self.pid]
        self.key = key_for(req, self.pid, self.providers)
        self.client = make_client(self.pid, self.key, providers=self.providers)


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
    t = Target(request, payload.model, payload.provider)
    pid, model, client = t.pid, t.model, t.client
    msgs = [m.model_dump() for m in payload.messages]

    if not payload.stream:
        res = client.chat.completions.create(
            model=model, messages=msgs,
            max_tokens=payload.max_tokens, temperature=payload.temperature,
        )
        return {
            'provider': pid,
            'model': model,
            'content': res.choices[0].message.content,
        }

    def gen():
        usage = None
        try:
            stream = client.chat.completions.create(
                model=model, messages=msgs, stream=True,
                max_tokens=payload.max_tokens, temperature=payload.temperature,
            )
            for chunk in stream:
                if getattr(chunk, 'usage', None):
                    usage = chunk.usage.model_dump()
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                content = getattr(delta, 'content', None)
                if content:
                    yield f"data: {json.dumps({'delta': content})}\n\n"
            yield f"data: {json.dumps({'done': True, 'provider': pid, 'model': model, 'usage': usage})}\n\n"
        except openai.APIStatusError as e:
            yield f"data: {json.dumps({'error': _upstream_detail(e)})}\n\n"
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
