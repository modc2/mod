"""
secretshare api — Shamir split/combine over HTTP, plus the console.

One process serves both halves so there is nothing else to keep alive:

    /                        console (index.html + shamir.js)
    /secretshare[/...]       console again, as the gateway forwards it (prefix kept)
    /secretshare/api/...     API, when hit on the bare port
    /health /info            liveness + what this module is
    POST /split   {secret, n, k}         → {shares}
    POST /combine {shares}               → {secret, secret_b64}
    POST /inspect {share}                → {set, k, x, secret_bytes}

The console does its splitting in the browser (app/shamir.js) — the secret
never reaches this server. These endpoints exist for the CLI, agents and
scripts; anything POSTed here is processed in memory and never written or
logged.

Run: m secretshare/serve   (pm2 `secretshare`, uvicorn on config `port`)
"""
import base64
import json
import sys
from pathlib import Path
from typing import List, Union

MODULE_DIR = Path(__file__).resolve().parent.parent
if str(MODULE_DIR) not in sys.path:
    sys.path.append(str(MODULE_DIR))

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import FileResponse, HTMLResponse, Response  # noqa: E402
from pydantic import BaseModel  # noqa: E402

import shamir  # noqa: E402

CONFIG = json.loads((MODULE_DIR / 'config.json').read_text())
NAME = CONFIG.get('name', 'secretshare')
BASE = f'/{NAME}'
APP_DIR = MODULE_DIR / 'app'

app = FastAPI(title=NAME, description=CONFIG.get('description', ''))
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_methods=['*'],
                   allow_headers=['*'], allow_credentials=False)


class PrefixStrip:
    """Map /secretshare/api/x → /x and /secretshare/x → /x so the same routes
    answer on the bare port, via the gateway's app route, and via its api route."""

    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'http':
            path = scope['path']
            for prefix in (f'{BASE}/api', BASE):
                if path == prefix or path.startswith(prefix + '/'):
                    scope = dict(scope, path=path[len(prefix):] or '/')
                    break
        await self.inner(scope, receive, send)


class SplitReq(BaseModel):
    secret: str
    n: int = 5
    k: int = 3
    b64: bool = False          # secret is base64 bytes, not text


class CombineReq(BaseModel):
    shares: Union[List[str], str]


class InspectReq(BaseModel):
    share: str


def _fail(e: Exception):
    raise HTTPException(400, str(e))


@app.get('/', include_in_schema=False)
@app.get('/index.html', include_in_schema=False)
def console():
    return HTMLResponse((APP_DIR / 'index.html').read_text())


@app.get('/shamir.js', include_in_schema=False)
def shamir_js():
    return FileResponse(APP_DIR / 'shamir.js', media_type='application/javascript')


@app.get('/favicon.ico', include_in_schema=False)
def favicon():
    return Response(status_code=204)


@app.get('/health')
def health():
    return {'ok': True, 'name': NAME, 'version': CONFIG.get('version')}


@app.get('/info')
def info():
    return {'name': NAME, 'description': CONFIG.get('description'),
            'format': f'{shamir.VERSION}.<set>.<k>.<x>.<payload>',
            'max_shares': shamir.MAX_SHARES, 'stores_nothing': True}


@app.post('/split')
def split(req: SplitReq):
    try:
        secret = base64.b64decode(req.secret) if req.b64 else req.secret
        shares = shamir.split(secret, n=req.n, k=req.k)
    except (shamir.ShareError, ValueError) as e:
        _fail(e)
    return {'n': req.n, 'k': req.k, 'set': shares[0].split('.')[1], 'shares': shares}


@app.post('/combine')
def combine(req: CombineReq):
    try:
        data = shamir.combine(req.shares)
    except (shamir.ShareError, ValueError) as e:
        _fail(e)
    try:
        text = data.decode()
    except UnicodeDecodeError:
        text = None
    return {'secret': text, 'secret_b64': base64.b64encode(data).decode(),
            'bytes': len(data)}


@app.post('/inspect')
def inspect(req: InspectReq):
    try:
        return shamir.inspect(req.share)
    except shamir.ShareError as e:
        _fail(e)


app = PrefixStrip(app)
