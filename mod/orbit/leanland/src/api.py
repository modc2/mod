"""
HTTP surface: one port serves the console and the JSON API.

Bound to 127.0.0.1 by default. There is no auth here on purpose — writing an
endpoint that edits the library and half a permission system to guard it is
worse than saying plainly that this listens on loopback. Put it behind the
fleet's gateway and the `auth` module before flipping `route` on in config.json.
"""
from __future__ import annotations

import os

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware

from . import chat, check, lower
from .library import Library

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONSOLE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'console.html')


def build_app(root: str = ROOT) -> FastAPI:
    lib = Library(root)
    app = FastAPI(title='leanland', docs_url='/api/docs', openapi_url='/api/openapi.json')
    app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_methods=['*'],
                       allow_headers=['*'])

    def fail(e: Exception):
        raise HTTPException(status_code=400, detail=str(e))

    # -- console ----------------------------------------------------------
    @app.get('/', response_class=HTMLResponse)
    @app.get('/leanland', response_class=HTMLResponse)
    def console():
        with open(CONSOLE) as f:
            return f.read()

    # -- library ----------------------------------------------------------
    @app.get('/api/info')
    def info():
        defs, errors = lib.load()
        return {'name': 'leanland', 'root': root, 'defs': len(defs),
                'papers': len(lib.lit.keys()), 'errors': errors,
                'files': lib.files(), 'targets': ['python', 'notebook', 'rust',
                                                  'nextjs', 'lean'],
                'lean_toolchain': check.lean_available()}

    @app.get('/api/defs')
    def defs():
        d, errors = lib.load()
        return {'defs': lib.index(), 'errors': errors}

    @app.get('/api/defs/{name}')
    def one(name: str, lowered: bool = True):
        try:
            d = lib.get(name)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))
        all_defs, _ = lib.load()
        out = {**d.to_dict(), 'cases': lower.cases(d, all_defs)}
        if d.source.get('key') and d.source['key'] in lib.lit.keys():
            out['paper'] = lib.lit.get(d.source['key'])
        if lowered:
            need = lower.closure(all_defs, [name])
            out['lowered'] = {
                'python': lower.emit.function(d, all_defs, lower.TARGETS['py']),
                'rust': lower.emit.function(d, all_defs, lower.TARGETS['rs']),
                'typescript': lower.emit.function(d, all_defs, lower.TARGETS['ts']),
                'lean4': lower.lean4(need),
            }
        return out

    @app.post('/api/defs')
    def add(source: str = Body(..., embed=True), file: str = Body('user.lean', embed=True),
            replace: bool = Body(True, embed=True)):
        return lib.add(source, file=file, replace=replace)

    @app.delete('/api/defs/{name}')
    def rm(name: str):
        try:
            return lib.rm(name)
        except (KeyError, ValueError) as e:
            fail(e)

    # -- literature -------------------------------------------------------
    @app.get('/api/lit')
    def papers(q: str = ''):
        return {'papers': lib.lit.search(q) if q else list(lib.lit.all().values())}

    @app.get('/api/lit/{key}')
    def paper(key: str):
        try:
            p = lib.lit.get(key)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=f'no lit/{key}.md')
        d, _ = lib.load()
        return {**p, 'defs': [x.name for x in d.values() if x.source.get('key') == key]}

    @app.post('/api/lit')
    def add_paper(payload: dict = Body(...)):
        if payload.get('arxiv'):
            try:
                return lib.lit.add_arxiv(payload['arxiv'], key=payload.get('key'),
                                         notes=payload.get('notes', ''))
            except RuntimeError as e:
                fail(e)
        if not payload.get('key'):
            raise HTTPException(status_code=400, detail='key is required')
        return lib.lit.add(**payload)

    @app.post('/api/lit/{key}/note')
    def note(key: str, text: str = Body(..., embed=True)):
        return lib.lit.note(key, text)

    @app.delete('/api/lit/{key}')
    def rm_paper(key: str):
        try:
            return lib.lit.rm(key)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=f'no lit/{key}.md')

    # -- the agent --------------------------------------------------------
    @app.post('/api/chat')
    def discuss(payload: dict = Body(...)):
        try:
            return chat.discuss(lib, payload.get('message', ''),
                                paper=payload.get('paper'),
                                history=payload.get('history'),
                                model=payload.get('model'),
                                full=bool(payload.get('full')))
        except Exception as e:
            fail(e)

    @app.post('/api/elaborate')
    def elaborate(payload: dict = Body(...)):
        try:
            return chat.elaborate(lib, payload.get('want', ''), paper=payload.get('paper'),
                                  tries=int(payload.get('tries', 3)),
                                  model=payload.get('model'), file=payload.get('file'),
                                  write=payload.get('write', True))
        except Exception as e:
            fail(e)

    @app.post('/api/read')
    def read(payload: dict = Body(...)):
        try:
            return chat.read(lib, payload['key'], about=payload.get('about', ''),
                             model=payload.get('model'))
        except Exception as e:
            fail(e)

    # -- checking and building --------------------------------------------
    @app.get('/api/verify')
    def verify():
        return lib.verify()

    @app.get('/api/parity')
    def parity(targets: str = '', only: str = ''):
        d, _ = lib.load()
        return check.parity(d, targets=[t for t in targets.split(',') if t] or None,
                            only=[n for n in only.split(',') if n] or None)

    @app.post('/api/build')
    def build(payload: dict = Body(default={})):
        return lib.build(payload.get('targets'))

    @app.get('/api/drift')
    def drift():
        return lib.drift()

    @app.get('/api/artifact', response_class=PlainTextResponse)
    def artifact(path: str = Query(...)):
        files = lib.artifacts()
        if path not in files:
            raise HTTPException(status_code=404,
                                detail=f'not a generated artifact; have {len(files)}')
        return files[path]

    @app.get('/api/artifacts')
    def artifacts():
        return {'files': sorted(lib.artifacts())}

    @app.get('/api/health')
    def health():
        d, errors = lib.load()
        return {'ok': not errors, 'defs': len(d)}

    return app


app = build_app()


def serve(port: int = 50540, host: str = '127.0.0.1', root: str = ROOT):
    import uvicorn
    uvicorn.run(build_app(root), host=host, port=int(port), log_level='info')
