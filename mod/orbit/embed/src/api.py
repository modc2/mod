"""The HTTP surface. Every route is a call into the same functions mod.py uses.

    uvicorn src.api:app --port 50620          or      m embed/serve

Reads only — nothing here writes anything a caller supplies to disk except the
models it builds under EMBED_DIR, so there is no auth. The one route that
touches the network is /pull, and it takes a Hugging Face repo, not a URL.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from src import check as checker
from src import compress as compressor
from src import data, evaluate, onnxfile, quantize, runtime, text, zoo

app = FastAPI(title='embed', version='1.0.0',
              description='small models, made smaller, with the cost written down')
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_methods=['*'],
                   allow_headers=['*'])


@app.get('/')
def info() -> Dict[str, Any]:
    return {
        'name': 'embed',
        'description': 'small models, made smaller, with the cost written down',
        'models': [m['name'] for m in zoo.catalog()],
        'methods': list(compressor.METHODS),
        'ops_implemented': runtime.implemented(),
        'endpoints': ['/models', '/models/{name}', '/build', '/embed', '/search',
                      '/classify', '/compare', '/compress', '/sweep', '/check',
                      '/corpus', '/collisions', '/examples', '/health'],
    }


@app.get('/health')
def health() -> Dict[str, Any]:
    return {'ok': True,
            'built': [m['name'] for m in zoo.catalog() if m['present']],
            'cross_check': checker.available()}


# ── the zoo ──────────────────────────────────────────────────────────

@app.get('/models')
def models() -> List[Dict[str, Any]]:
    return zoo.catalog()


@app.get('/models/{name}')
def model(name: str) -> Dict[str, Any]:
    try:
        file = zoo.ensure(name)
    except (KeyError, FileNotFoundError) as exc:
        raise HTTPException(404, str(exc))
    loaded = onnxfile.load(file)
    return {'name': name, **zoo.CATALOG.get(name, {}), 'path': str(file),
            'summary': loaded.summary(),
            'nodes': [{'op': n.op_type, 'inputs': n.inputs, 'outputs': n.outputs}
                      for n in loaded.graph.nodes[:60]],
            'ops_missing_here': runtime.unsupported(loaded),
            'built': zoo.built().get(name)}


@app.post('/build')
def build(name: str = Query('bow-64'), rebuild: bool = False) -> Dict[str, Any]:
    if rebuild:
        zoo.path(name).unlink(missing_ok=True)
    try:
        file = zoo.ensure(name)
    except (KeyError, FileNotFoundError) as exc:
        raise HTTPException(400, str(exc))
    return {'name': name, 'path': str(file), 'bytes': file.stat().st_size,
            'built': zoo.built().get(name)}


@app.post('/pull')
def pull(name: str = Query('minilm'), repo: Optional[str] = None,
         file: Optional[str] = None) -> Dict[str, Any]:
    try:
        return zoo.pull(name, repo, file)
    except Exception as exc:                       # network, mostly
        raise HTTPException(502, f'{type(exc).__name__}: {exc}')


# ── using the models ─────────────────────────────────────────────────

@app.get('/embed')
def embed(text_: str = Query(..., alias='text'),
          name: str = 'bow-64') -> Dict[str, Any]:
    vector = evaluate.embed(_load(name), text_)
    return {'text': text_, 'model': name, 'dimensions': int(vector.size),
            'vector': [round(float(v), 5) for v in vector]}


@app.get('/search')
def search(query: str, name: str = 'bow-64', top: int = 5,
           method: Optional[str] = None) -> Dict[str, Any]:
    """`method` searches with a compressed copy instead, for comparison."""
    model_ = _compressed(name, method) if method and method != 'float32' else _load(name)
    return {'query': query, 'model': name, 'method': method or 'float32',
            'results': evaluate.search(model_, query, None, int(top))}


@app.get('/classify')
def classify(text_: str = Query(..., alias='text'),
             name: str = 'sent-mlp', method: Optional[str] = None) -> Dict[str, Any]:
    model_ = _compressed(name, method) if method and method != 'float32' else _load(name)
    probs = evaluate.classify(model_, [text_])[0]
    return {'text': text_, 'model': name, 'method': method or 'float32',
            'label': ['negative', 'positive'][int(probs.argmax())],
            'probabilities': {'negative': round(float(probs[0]), 4),
                              'positive': round(float(probs[1]), 4)},
            'margin': round(abs(float(probs[1] - probs[0])), 4)}


@app.get('/corpus')
def corpus() -> Dict[str, Any]:
    return {'documents': [{'topic': t, 'text': d} for t, d in data.DOCS],
            'queries': [{'query': q, 'topic': t} for q, t in data.QUERIES],
            'topics': data.TOPICS}


@app.get('/collisions')
def collisions(vocab: int = 8192) -> Dict[str, Any]:
    corpus_ = [d for _, d in data.DOCS] + [q for q, _ in data.QUERIES]
    return text.collisions(corpus_, int(vocab))


# ── compression ──────────────────────────────────────────────────────

@app.get('/compare')
def compare(name: str = 'bow-64', tensor: Optional[str] = None) -> Dict[str, Any]:
    weights = _load(name).tensors()
    picked = tensor or max(weights, key=lambda k: weights[k].size)
    if picked not in weights:
        raise HTTPException(404, f'no tensor {picked!r} — {sorted(weights)}')
    return {'model': name, 'tensor': picked, **quantize.compare(weights[picked])}


@app.post('/compress')
def compress(name: str = Query('bow-64'), method: str = 'int8',
             keep: bool = False) -> Dict[str, Any]:
    if method not in compressor.METHODS:
        raise HTTPException(400, f'unknown method — {list(compressor.METHODS)}')
    source = zoo.ensure(name)
    target = source.with_name(f'{source.stem}.{method}.onnx')
    report = compressor.compress_file(source, target, method)
    report.pop('model', None)
    if not keep:
        target.unlink(missing_ok=True)
        report['target'] = None
    return report


@app.get('/sweep')
def sweep(name: str = 'bow-64') -> Dict[str, Any]:
    try:
        return evaluate.sweep(name)
    except (KeyError, FileNotFoundError) as exc:
        raise HTTPException(404, str(exc))


@app.get('/check')
def check(name: str = 'bow-64', all: bool = False) -> Dict[str, Any]:
    return checker.check_all() if all else checker.check(name)


@app.get('/examples')
def examples() -> List[Dict[str, str]]:
    folder = HERE / 'examples'
    out = []
    for script in sorted(folder.glob('*.py')):
        lines = script.read_text().splitlines()
        title = next((l.strip('"').strip() for l in lines if l.startswith('"""')),
                     script.stem)
        out.append({'id': script.stem.split('_')[0], 'file': script.name,
                    'title': title, 'source': script.read_text()})
    return out


# ── helpers ──────────────────────────────────────────────────────────

def _load(name: str):
    try:
        return zoo.load(name)
    except (KeyError, FileNotFoundError) as exc:
        raise HTTPException(404, str(exc))


_CACHE: Dict[str, Any] = {}


def _compressed(name: str, method: str):
    """A compressed copy, built once per process and kept in memory."""
    if method not in compressor.METHODS:
        raise HTTPException(400, f'unknown method — {list(compressor.METHODS)}')
    key = f'{name}:{method}'
    if key not in _CACHE:
        result = compressor.compress(_load(name), method)
        _CACHE[key] = result['model']
    return _CACHE[key]


if __name__ == '__main__':
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int,
                        default=int(os.environ.get('EMBED_PORT', 50620)))
    parser.add_argument('--host', default='0.0.0.0')
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port, log_level='info')
