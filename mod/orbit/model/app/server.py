"""
Unified model gateway — BYOK chat across openrouter + grok (chutes/targon/venice).

Strict BYOK: the server never reads from disk-stored API keys. Every request
must supply its key via the `X-API-Key` header (or `Authorization: Bearer ...`).
Keys live only in memory for the duration of a single request and are
forwarded to the upstream provider — they are not logged or persisted.
"""

import json
import os
import time
from pathlib import Path
from typing import Optional

import openai
import requests
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel


# Provider registry — single source of truth. Mirrors grok/model.py PROVIDERS
# but unified so the front-end gets a single flat list.
PROVIDERS = {
    'openrouter': {
        'label': 'OpenRouter',
        'url': 'https://openrouter.ai/api/v1',
        'default_model': 'anthropic/claude-opus-4',
        'key_hint': 'sk-or-…',
        'docs': 'https://openrouter.ai/keys',
    },
    'chutes': {
        'label': 'Chutes',
        'url': 'https://llm.chutes.ai/v1',
        'default_model': 'deepseek-ai/DeepSeek-V3',
        'key_hint': 'cpk_…',
        'docs': 'https://chutes.ai',
    },
    'targon': {
        'label': 'Targon',
        'url': 'https://api.targon.com/v1',
        'default_model': 'deepseek-ai/DeepSeek-V3',
        'key_hint': 'sn4_…',
        'docs': 'https://targon.com',
    },
    'venice': {
        'label': 'Venice',
        'url': 'https://api.venice.ai/api/v1',
        'default_model': 'llama-3.3-70b',
        'key_hint': 'venice key',
        'docs': 'https://venice.ai',
    },
}

APP_DIR = Path(__file__).parent
INDEX_PATH = APP_DIR / 'index.html'


app = FastAPI(title="model-gateway", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_methods=['*'],
    allow_headers=['*'],
)


# ── helpers ───────────────────────────────────────────────────────────

def extract_key(req: Request) -> str:
    key = req.headers.get('x-api-key')
    if not key:
        auth = req.headers.get('authorization') or ''
        if auth.lower().startswith('bearer '):
            key = auth.split(' ', 1)[1].strip()
    if not key:
        raise HTTPException(status_code=401, detail="BYOK: supply X-API-Key or Authorization: Bearer …")
    return key


def provider_cfg(pid: str) -> dict:
    cfg = PROVIDERS.get(pid)
    if not cfg:
        raise HTTPException(status_code=400, detail=f"unknown provider '{pid}'")
    return cfg


def make_client(pid: str, key: str, timeout: float = 60.0) -> openai.OpenAI:
    cfg = provider_cfg(pid)
    return openai.OpenAI(base_url=cfg['url'], api_key=key, timeout=timeout, max_retries=2)


# ── routes ────────────────────────────────────────────────────────────

@app.get('/health')
def health():
    return {'ok': True, 'time': time.time(), 'providers': list(PROVIDERS.keys())}


@app.get('/providers')
def providers():
    return [
        {
            'id': pid,
            'label': cfg['label'],
            'url': cfg['url'],
            'default_model': cfg['default_model'],
            'key_hint': cfg['key_hint'],
            'docs': cfg['docs'],
        }
        for pid, cfg in PROVIDERS.items()
    ]


@app.get('/models')
def models(provider: str, request: Request):
    cfg = provider_cfg(provider)
    key = extract_key(request)
    try:
        resp = requests.get(
            cfg['url'].rstrip('/') + '/models',
            headers={'Authorization': f'Bearer {key}'},
            timeout=15,
        )
        if resp.status_code >= 400:
            raise HTTPException(status_code=resp.status_code, detail=f"{provider}: {resp.text[:200]}")
        data = resp.json().get('data') or resp.json().get('models') or []
        # Normalise to {id, context_length?}
        out = []
        for m in data:
            if isinstance(m, str):
                out.append({'id': m})
            elif isinstance(m, dict):
                out.append({
                    'id': m.get('id') or m.get('name') or '',
                    'context_length': m.get('context_length') or m.get('max_model_len'),
                    'pricing': m.get('pricing'),
                })
        out = [m for m in out if m['id']]
        return {'provider': provider, 'count': len(out), 'models': out}
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"{provider}/models failed: {e}")


class ChatMessage(BaseModel):
    role: str  # "system" | "user" | "assistant"
    content: str


class ChatRequest(BaseModel):
    provider: str
    model: Optional[str] = None
    messages: list[ChatMessage]
    temperature: float = 1.0
    max_tokens: int = 4096
    stream: bool = True


@app.post('/chat')
def chat(payload: ChatRequest, request: Request):
    cfg = provider_cfg(payload.provider)
    key = extract_key(request)
    model = payload.model or cfg['default_model']
    client = make_client(payload.provider, key, timeout=120.0)
    msgs = [m.model_dump() for m in payload.messages]

    if not payload.stream:
        try:
            res = client.chat.completions.create(
                model=model, messages=msgs,
                max_tokens=payload.max_tokens, temperature=payload.temperature,
            )
        except openai.APIError as e:
            raise HTTPException(status_code=502, detail=str(e))
        return {
            'provider': payload.provider,
            'model': model,
            'content': res.choices[0].message.content,
        }

    def gen():
        try:
            stream = client.chat.completions.create(
                model=model, messages=msgs, stream=True,
                max_tokens=payload.max_tokens, temperature=payload.temperature,
            )
            for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                content = getattr(delta, 'content', None)
                if content:
                    yield f"data: {json.dumps({'delta': content})}\n\n"
            yield f"data: {json.dumps({'done': True, 'provider': payload.provider, 'model': model})}\n\n"
        except Exception as e:  # surface upstream errors over SSE
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(gen(), media_type='text/event-stream')


@app.get('/')
def index():
    if INDEX_PATH.exists():
        return FileResponse(INDEX_PATH)
    return JSONResponse({'error': 'index.html missing', 'path': str(INDEX_PATH)})


# ── entrypoint ────────────────────────────────────────────────────────

def serve(host: str = '0.0.0.0', port: int = 50110, reload: bool = False):
    import uvicorn
    uvicorn.run('app.server:app' if reload else app, host=host, port=port, reload=reload)


if __name__ == '__main__':
    port = int(os.environ.get('MODEL_GATEWAY_PORT', '50110'))
    serve(port=port)
