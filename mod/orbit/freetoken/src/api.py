"""The HTTP surface — the same functions, over the wire, plus one thing more.

Two kinds of route live here:

  /boxes /preflight /install /start /stats /cache …
        the module's own control plane, JSON in and out.

  /v1/models /v1/chat/completions /v1/completions /v1/messages …
        a straight forward to the default box. This is the reason to run the
        module as a service at all: one stable base_url on :50660 that any
        OpenAI or Anthropic client can hold, while the engine behind it moves
        between machines, models and ports. Streaming passes through
        untouched, chunk for chunk.

Anything that installs software, spawns a process or edits the box list is
loopback-only. Reads and inference are not.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

HERE = Path(__file__).resolve().parent
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from src import boxes, catalog, client, engine, install, preflight, state

app = FastAPI(title='freetoken', version='0.1.0',
              description='A handle on a FreeToken engine — local, or on the box '
                          'that actually has the GPU.')
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_methods=['*'],
                   allow_headers=['*'])

LOOPBACK = {'127.0.0.1', '::1', 'localhost'}


def _local_only(request: Request) -> None:
    host = request.client.host if request.client else ''
    if host not in LOOPBACK:
        raise HTTPException(403, 'this endpoint changes the machine it runs on, '
                                 'and only answers the machine it runs on')


def _fail(exc: Exception) -> JSONResponse:
    status = {client.Unreachable: 502, KeyError: 404, ValueError: 400}.get(type(exc))
    if status is None:
        status = exc.status if isinstance(exc, client.Refused) else 500
    detail = exc.body if isinstance(exc, client.Refused) else str(exc)
    return JSONResponse({'error': f'{type(exc).__name__}', 'detail': detail},
                        status_code=status)


def _box(name: Optional[str]) -> Dict[str, Any]:
    return boxes.resolve(name)


# ── what it is ───────────────────────────────────────────────────────

@app.get('/')
def root() -> Dict[str, Any]:
    gate = preflight.report()
    return {
        'name': 'freetoken',
        'wraps': {'project': 'FreeToken', 'license': 'Apache-2.0',
                  'repo': 'https://github.com/FlashML-org/FreeToken',
                  'paper': 'https://arxiv.org/abs/2608.16157'},
        'can_serve_here': gate['can_serve_here'],
        'ft_installed': bool(install.ft_bin()),
        'default_box': boxes.listing()['default'],
        'endpoints': {
            'preflight': 'GET /preflight',
            'install': 'GET /install | POST /install (loopback) | GET /install/log',
            'boxes': 'GET /boxes | POST /boxes (loopback) | DELETE /boxes/{name} '
                     '| POST /boxes/{name}/use | GET /boxes/{name}/probe',
            'models': 'GET /models?box=',
            'engine': 'POST /start (loopback) | POST /switch | POST /stop | '
                      'GET /server?box= | GET /logs',
            'telemetry': 'GET /stats?box= | GET /cache?box= | POST /cache/rebuild | '
                         'GET /requests?box=',
            'inference': 'POST /chat (stream=true works) | POST /generate | POST /ask',
            'provider': 'GET /v1/models | POST /v1/chat/completions | '
                        'POST /v1/completions | POST /v1/messages — forwarded to the '
                        'default box, so any OpenAI/Anthropic client can hold this URL',
        },
        'state': str(state.home()),
    }


@app.get('/health')
def health() -> Dict[str, Any]:
    cards = [client.probe(b, timeout=2.0) for b in boxes.all()]
    return {'ok': True, 'ft_installed': bool(install.ft_bin()),
            'up': [c['name'] for c in cards if c['up']],
            'down': [c['name'] for c in cards if not c['up']]}


@app.get('/preflight')
def preflight_report() -> Dict[str, Any]:
    return preflight.report()


# ── installing ───────────────────────────────────────────────────────

@app.get('/install')
def install_status() -> Dict[str, Any]:
    return install.status()


@app.post('/install')
def install_start(request: Request, body: Dict[str, Any] = Body(default={})) -> Any:
    _local_only(request)
    return install.install(source=bool(body.get('source')),
                           accel=bool(body.get('accel', True)),
                           upgrade=bool(body.get('upgrade')),
                           ref=body.get('ref'), dry=bool(body.get('dry')))


@app.get('/install/log')
def install_log(lines: int = 60) -> Dict[str, Any]:
    return install.log(lines)


@app.delete('/install')
def install_cancel(request: Request) -> Any:
    _local_only(request)
    return install.cancel()


# ── boxes ────────────────────────────────────────────────────────────

@app.get('/boxes')
def box_list(probe: bool = True) -> Dict[str, Any]:
    listing = boxes.listing()
    if probe:
        listing['engines'] = [client.probe(b, timeout=2.5) for b in boxes.all()]
    return listing


@app.post('/boxes')
def box_add(request: Request, body: Dict[str, Any] = Body(...)) -> Any:
    _local_only(request)
    try:
        return boxes.add(body['name'], url=body.get('url'), daemon=body.get('daemon'),
                         token=body.get('token'), note=body.get('note', ''),
                         use=bool(body.get('use')))
    except (KeyError, ValueError) as exc:
        return _fail(exc)


@app.delete('/boxes/{name}')
def box_drop(request: Request, name: str) -> Any:
    _local_only(request)
    return boxes.drop(name)


@app.post('/boxes/{name}/use')
def box_use(request: Request, name: str) -> Any:
    _local_only(request)
    try:
        return boxes.use(name)
    except KeyError as exc:
        return _fail(exc)


@app.get('/boxes/{name}/probe')
def box_probe(name: str) -> Any:
    try:
        return client.probe(_box(name), timeout=6.0)
    except KeyError as exc:
        return _fail(exc)


# ── models ───────────────────────────────────────────────────────────

@app.get('/models')
def model_catalog(box: Optional[str] = None, size: bool = True) -> Dict[str, Any]:
    out = catalog.catalog(size=size)
    try:
        target = _box(box)
        out['served'] = client.models(target)
        out['box'] = target['name']
    except (client.Unreachable, client.Refused, KeyError) as exc:
        out['served'], out['why_not'] = None, str(exc)
    return out


# ── the engine ───────────────────────────────────────────────────────

@app.post('/start')
def engine_start(request: Request, body: Dict[str, Any] = Body(...)) -> Any:
    _local_only(request)
    model = body.get('model')
    if not model:
        return _fail(ValueError('model is required'))
    flags = dict(body.get('flags') or {})
    target = _box(body.get('box'))
    try:
        argv = engine.serve_argv(model, **flags)
    except ValueError as exc:
        return _fail(exc)
    port = body.get('port')
    if target.get('daemon'):
        try:
            client.daemon_self(target, timeout=3.0)
            return {'via': 'daemon', 'box': target['name'],
                    'result': client.engine_start(target, model, port=port,
                                                  args=argv[3:])}
        except (client.Unreachable, client.Refused):
            pass
    if not _is_local(target):
        return _fail(client.Unreachable(
            f'{target["name"]} is remote and has no reachable daemon'))
    return {'via': 'local process', 'box': target['name'],
            **engine.start(model, force=bool(body.get('force')),
                           **({'port': port} if port else {}), **flags)}


@app.post('/switch')
def engine_switch(request: Request, body: Dict[str, Any] = Body(...)) -> Any:
    _local_only(request)
    try:
        target = _box(body.get('box'))
        argv = engine.serve_argv(body['model'], **(body.get('flags') or {}))
        return client.engine_switch(target, body['model'], port=body.get('port'),
                                    args=argv[3:])
    except (KeyError, ValueError, client.Unreachable, client.Refused) as exc:
        return _fail(exc)


@app.post('/stop')
def engine_stop(request: Request, body: Dict[str, Any] = Body(default={})) -> Any:
    _local_only(request)
    target = _box(body.get('box'))
    force = bool(body.get('force'))
    if target.get('daemon'):
        try:
            return {'via': 'daemon', 'result': client.engine_stop(target, force=force)}
        except (client.Unreachable, client.Refused) as exc:
            if not _is_local(target):
                return _fail(exc)
    return {'via': 'local process', **engine.stop(force=force)}


@app.get('/server')
def server(box: Optional[str] = None) -> Any:
    try:
        target = _box(box)
    except KeyError as exc:
        return _fail(exc)
    card = client.probe(target, timeout=6.0)
    if _is_local(target):
        card['local_process'] = engine.status()
    return card


@app.get('/logs')
def logs(lines: int = 60) -> Dict[str, Any]:
    return engine.logs(lines)


# ── telemetry ────────────────────────────────────────────────────────

@app.get('/stats')
def stats(box: Optional[str] = None) -> Any:
    try:
        return client.stats(_box(box))
    except Exception as exc:
        return _fail(exc)


@app.get('/cache')
def cache(box: Optional[str] = None) -> Any:
    try:
        return client.cache_status(_box(box))
    except Exception as exc:
        return _fail(exc)


@app.post('/cache/rebuild')
def cache_rebuild(request: Request, body: Dict[str, Any] = Body(...)) -> Any:
    _local_only(request)
    try:
        return client.cache_rebuild(_box(body.get('box')), moe=body.get('moe'),
                                    kv=body.get('kv'), mamba=body.get('mamba'),
                                    swa=body.get('swa'), wait=int(body.get('wait', 300)))
    except Exception as exc:
        return _fail(exc)


@app.get('/requests')
def request_ring(box: Optional[str] = None, since: int = 0, limit: int = 50) -> Any:
    try:
        return client.requests(_box(box), since=since, limit=limit)
    except Exception as exc:
        return _fail(exc)


@app.get('/profile')
def bench_profile(box: Optional[str] = None) -> Any:
    try:
        return client.bench_profile(_box(box))
    except Exception as exc:
        return _fail(exc)


# ── inference ────────────────────────────────────────────────────────

@app.post('/chat')
def chat(body: Dict[str, Any] = Body(...)) -> Any:
    try:
        target = _box(body.get('box'))
        messages = body.get('messages') or [{'role': 'user',
                                             'content': body.get('prompt', '')}]
        if body.get('stream'):
            return _passthrough(target, '/v1/chat/completions',
                                {'model': body.get('model') or client.served_name(target),
                                 'messages': messages,
                                 'max_tokens': int(body.get('max_tokens', 512)),
                                 'stream': True})
        return client.chat(target, messages, model=body.get('model'),
                           max_tokens=int(body.get('max_tokens', 512)),
                           temperature=body.get('temperature'))
    except Exception as exc:
        return _fail(exc)


@app.post('/generate')
def generate(body: Dict[str, Any] = Body(default={})) -> Any:
    try:
        return client.generate(_box(body.get('box')), body.get('prompt', 'Hello'),
                               max_tokens=int(body.get('max_tokens', 32)),
                               ignore_eos=bool(body.get('ignore_eos')))
    except Exception as exc:
        return _fail(exc)


# ── the provider surface ─────────────────────────────────────────────

def _passthrough(target: Dict[str, Any], path: str, body: Dict[str, Any]) -> Any:
    """Forward a streaming request byte for byte. No re-framing, no buffering."""
    def chunks():
        try:
            for line in client.stream(target['url'], path, body,
                                      token=target.get('token')):
                yield (line + '\n').encode()
        except (client.Unreachable, client.Refused) as exc:
            yield f'data: {json.dumps({"error": str(exc)})}\n\n'.encode()
    return StreamingResponse(chunks(), media_type='text/event-stream',
                             headers={'Cache-Control': 'no-cache',
                                      'X-Freetoken-Box': str(target.get('name'))})


@app.get('/v1/models')
def v1_models() -> Any:
    try:
        return client.models(_box(None))
    except Exception as exc:
        return _fail(exc)


@app.post('/v1/chat/completions')
@app.post('/v1/completions')
@app.post('/v1/messages')
@app.post('/v1/messages/count_tokens')
@app.post('/v1/responses')
async def v1_forward(request: Request) -> Any:
    """Whatever the client sent, to whichever box is default, unchanged."""
    try:
        target = _box(None)
        body = await request.json()
    except Exception as exc:
        return _fail(exc)
    path = request.url.path
    if body.get('stream'):
        return _passthrough(target, path, body)
    try:
        return client.call(target['url'], path, 'POST', body,
                           token=target.get('token'), timeout=600)
    except Exception as exc:
        return _fail(exc)


def _is_local(box: Dict[str, Any]) -> bool:
    url = box.get('url') or ''
    return any(h in url for h in ('127.0.0.1', 'localhost', '::1', '0.0.0.0'))


def main() -> None:
    import uvicorn
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=50660)
    parser.add_argument('--host', default='127.0.0.1')
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port, log_level='info')


if __name__ == '__main__':
    main()
