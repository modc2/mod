"""The HTTP surface — the same functions, over the wire.

Nothing is implemented here that is not implemented in the package: this file
takes uploads apart, hands them to `pipeline`, and puts the answer in JSON.
It binds to the loopback interface by default, and the two endpoints that
write a secret refuse anything that did not come from it.
"""
from __future__ import annotations

import argparse
import base64
import sys
from pathlib import Path
from typing import Any, Dict, Optional

HERE = Path(__file__).resolve().parent
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))

from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from src import audio, cache, engines, keys, ledger, pipeline, router, samples, vad

app = FastAPI(title='sound2text', version='1.0.0',
              description='Speech to text, where the model is the last thing tried.')
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_methods=['*'],
                   allow_headers=['*'])

LOOPBACK = {'127.0.0.1', '::1', 'localhost'}


def _local_only(request: Request) -> None:
    host = request.client.host if request.client else ''
    if host not in LOOPBACK:
        raise HTTPException(403, 'keys can only be set from the machine they live on')


def _fail(exc: Exception) -> JSONResponse:
    status = {KeyError: 404, ValueError: 400, PermissionError: 402,
              audio.DecodeError: 415}.get(type(exc), 500)
    return JSONResponse({'error': f'{type(exc).__name__}: {exc}'}, status_code=status)


def _source(upload: Optional[UploadFile], path: Optional[str], url: Optional[str],
            b64: Optional[str] = None) -> Any:
    if upload is not None:
        return upload.file.read()
    for candidate in (url, path, b64):
        if candidate:
            return candidate
    return samples.default()


# ── what it is ───────────────────────────────────────────────────────

@app.get('/')
def root() -> Dict[str, Any]:
    return {
        'name': 'sound2text',
        'description': ('Speech to text as five steps: decode, detect the speech, '
                        'recall what was done before, route to the engine that '
                        'suits, run only what is left — packed to fill the window.'),
        'engines': [c['name'] for c in engines.catalog() if c['available']],
        'audio': audio.capabilities(),
        'endpoints': {
            'transcribe': 'POST /transcribe (file upload) | GET /transcribe?path=|url=',
            'vad': 'POST /vad (file upload) | GET /vad?path=|url=',
            'compare': 'GET /compare?path=',
            'bench': 'GET /bench?path=',
            'engines': 'GET /engines', 'route': 'GET /route?policy=fast',
            'samples': 'GET /samples | GET /samples/{name}',
            'speed': 'GET /speed', 'cache': 'GET /cache | DELETE /cache',
            'keys': 'GET /keys | POST /keys (loopback only)',
            'health': 'GET /health',
        },
    }


@app.get('/health')
def health() -> Dict[str, Any]:
    return {'ok': True, 'engines_ready': [c['name'] for c in engines.catalog()
                                          if c['available']],
            'cache': cache.stats()}


@app.get('/engines')
def engine_list() -> Dict[str, Any]:
    return {'engines': engines.catalog()}


@app.get('/route')
def route(policy: str = 'fast', prefer: Optional[str] = None) -> Any:
    try:
        return router.choose(prefer=prefer, policy=policy)
    except Exception as exc:
        return _fail(exc)


# ── the work ─────────────────────────────────────────────────────────

@app.post('/transcribe')
async def transcribe_upload(
        file: Optional[UploadFile] = File(None), path: Optional[str] = Form(None),
        url: Optional[str] = Form(None), audio_b64: Optional[str] = Form(None),
        engine: Optional[str] = Form(None), policy: str = Form('fast'),
        model: Optional[str] = Form(None), language: Optional[str] = Form(None),
        task: str = Form('transcribe'), vad_on: bool = Form(True),
        cache_on: bool = Form(True), pack: bool = Form(True)) -> Any:
    try:
        return pipeline.transcribe(_source(file, path, url, audio_b64), engine=engine,
                                   policy=policy, model=model, language=language,
                                   task=task, use_vad=vad_on, use_cache=cache_on,
                                   pack=pack)
    except Exception as exc:
        return _fail(exc)


@app.get('/transcribe')
def transcribe_get(path: Optional[str] = None, url: Optional[str] = None,
                   engine: Optional[str] = None, policy: str = 'fast',
                   model: Optional[str] = None, language: Optional[str] = None,
                   task: str = 'transcribe', vad_on: bool = True,
                   cache_on: bool = True, pack: bool = True) -> Any:
    try:
        return pipeline.transcribe(_source(None, path, url), engine=engine,
                                   policy=policy, model=model, language=language,
                                   task=task, use_vad=vad_on, use_cache=cache_on,
                                   pack=pack)
    except Exception as exc:
        return _fail(exc)


@app.post('/vad')
async def vad_upload(file: Optional[UploadFile] = File(None),
                     path: Optional[str] = Form(None),
                     url: Optional[str] = Form(None),
                     threshold_db: Optional[float] = Form(None),
                     min_silence_ms: Optional[int] = Form(None)) -> Any:
    return _vad(_source(file, path, url), threshold_db, min_silence_ms)


@app.get('/vad')
def vad_get(path: Optional[str] = None, url: Optional[str] = None,
            threshold_db: Optional[float] = None,
            min_silence_ms: Optional[int] = None) -> Any:
    return _vad(_source(None, path, url), threshold_db, min_silence_ms)


def _vad(source: Any, threshold_db: Optional[float], min_silence_ms: Optional[int]) -> Any:
    try:
        pcm, meta = audio.load(source)
        found = vad.segments(pcm, audio.SAMPLE_RATE, threshold_db=threshold_db,
                             min_silence_ms=min_silence_ms)
        found['audio'] = meta
        found['packed'] = vad.pack(found['segments'])
        found['waveform'] = _waveform(pcm)
        return found
    except Exception as exc:
        return _fail(exc)


def _waveform(pcm, buckets: int = 900) -> list:
    """Peak per bucket — enough to draw the file, small enough to send."""
    import numpy as np
    if pcm.size == 0:
        return []
    step = max(pcm.size // buckets, 1)
    usable = pcm[:(pcm.size // step) * step].reshape(-1, step)
    return [round(float(v), 4) for v in np.abs(usable).max(axis=1)]


@app.get('/compare')
def compare(path: Optional[str] = None, url: Optional[str] = None,
            engine: Optional[str] = None, model: Optional[str] = None) -> Any:
    try:
        return pipeline.compare(_source(None, path, url), engine=engine, model=model)
    except Exception as exc:
        return _fail(exc)


@app.get('/bench')
def bench(path: Optional[str] = None, url: Optional[str] = None,
          engines_csv: Optional[str] = None, model: Optional[str] = None) -> Any:
    try:
        names = [e.strip() for e in engines_csv.split(',')] if engines_csv else None
        return pipeline.bench(_source(None, path, url), engine_names=names, model=model)
    except Exception as exc:
        return _fail(exc)


# ── housekeeping ─────────────────────────────────────────────────────

@app.get('/samples')
def sample_list() -> Dict[str, Any]:
    return {'samples': samples.catalog()}


@app.get('/samples/{name}')
def sample_file(name: str) -> Any:
    target = samples.HERE / Path(name).name          # no traversal
    if not target.exists():
        raise HTTPException(404, f'no sample {name}')
    return FileResponse(target, media_type='audio/wav')


@app.get('/speed')
def speed() -> Dict[str, Any]:
    return ledger.table()


@app.get('/cache')
def cache_stats() -> Dict[str, Any]:
    return cache.stats()


@app.delete('/cache')
def cache_clear() -> Dict[str, Any]:
    return cache.clear()


@app.get('/keys')
def key_list() -> Dict[str, Any]:
    return {'keys': keys.listing()}


@app.post('/keys')
def key_put(request: Request, body: Dict[str, str] = Body(...)) -> Dict[str, Any]:
    _local_only(request)
    vendor, key = body.get('vendor'), body.get('key')
    if not vendor or not key:
        raise HTTPException(400, 'vendor and key are both required')
    return keys.put(vendor, key)


@app.delete('/keys/{vendor}')
def key_drop(request: Request, vendor: str) -> Dict[str, Any]:
    _local_only(request)
    return keys.drop(vendor)


def main() -> None:
    import uvicorn
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=50640)
    parser.add_argument('--host', default='127.0.0.1')
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port, log_level='info')


if __name__ == '__main__':
    main()
