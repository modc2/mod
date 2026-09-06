"""
lighthouse api — perpetual storage with a door onto the store module.

Two halves that only touch at one seam:

    Lighthouse   bytes go to lighthouse.storage over HTTPS and land on
                 IPFS/Filecoin with a perpetual pin. The CID comes back and is
                 recorded in this module's own owner-keyed sqlite index.

    the store    that CID is then *registered* in the store module, which is
                 where visibility, grants, pools, quota and the marketplace
                 live. No bytes are copied there — the store is CID-agnostic by
                 design and keeps the gateway url so a reader is redirected
                 straight at Lighthouse.

Auth is the fleet's protocol token (`m.mod('auth')`), and the store bridge
forwards the caller's own token rather than acting on their behalf with a
credential of ours: the store applies its whitelist, terms and quota to the
address that actually signed. If the caller cannot store in the store, nothing
routed through here can either — that is the property worth having.

Keys, in the order they are tried:

    x-lh-key header    the caller's own Lighthouse key. Never written to disk,
                       discarded when the request ends. This is how a visitor
                       uses the console without trusting the box.
    module key         ~/.mod/lighthouse/credentials.json or LIGHTHOUSE_API_KEY,
                       set by the owner. Shared, so it is the fallback.

The same work is spoken as MCP from here too: `POST /mcp` is the Streamable
HTTP transport and `GET /mcp` is the schema as a plain document, so the tools,
these routes and the console are one thing described three ways (see mcp.py).

Run:
    uvicorn api.api:app --host 0.0.0.0 --port 50680
    python3 api/api.py --port 50680
"""
import importlib.util
import io
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from fastapi import (Body, FastAPI, File, Form, Header, HTTPException, Query,
                     Request, Response, UploadFile)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import identity  # noqa: E402
import mcp  # noqa: E402  — this module's mcp.py; ROOT leads sys.path above
from store_link import StoreLink, StoreError  # noqa: E402

CONFIG = json.loads((ROOT / 'config.json').read_text())
VERSION = CONFIG.get('version', '1.0.0')
BASE = CONFIG.get('base_path', '/lighthouse')


def _core():
    """This module's own mod.py, loaded by path so `import mod` stays the
    protocol package (every mod ships a mod.py — see protocol.py)."""
    spec = importlib.util.spec_from_file_location('lighthouse_core', ROOT / 'mod.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CORE = _core()
STORE = StoreLink()

app = FastAPI(title='lighthouse', version=VERSION,
              description=CONFIG.get('description', 'Lighthouse perpetual storage'))
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_methods=['*'],
                   allow_headers=['*'], allow_credentials=False)


@app.middleware('http')
async def api_alias(request, call_next):
    """`/lighthouse/_api/*` is this same API, one path segment along.

    Behind the gateway the API answers on /api/lighthouse and the console on
    /lighthouse; on a bare port both are here. The page always asks its own
    origin for `_api`, so one build works in both places with no CORS preflight
    on the hot path and no API url baked into the console.
    """
    path = request.scope.get('path', '')
    # Kept because the rewrite below destroys it, and the MCP routes have to
    # tell a client the url it actually reached them on.
    request.scope['lighthouse_path'] = path
    prefix = f'{BASE}/_api'
    if path.startswith(prefix):
        request.scope['path'] = path[len(prefix):] or '/'
    return await call_next(request)


# ── callers and keys ─────────────────────────────────────────────────

def caller(authorization: Optional[str], required: bool = True) -> Optional[str]:
    try:
        return (identity.require(authorization) if required
                else identity.whoami(authorization))
    except identity.AuthError as e:
        raise HTTPException(401, str(e))


def owner_caller(authorization: Optional[str]) -> str:
    try:
        return identity.require_owner(authorization)
    except identity.AuthError as e:
        raise HTTPException(403 if identity.owner() else 401, str(e))


def lighthouse(byok: Optional[str] = None):
    """A client bound to the caller's key if they brought one, else the box's."""
    return CORE.Mod(api_key=(byok or '').strip() or None)


def key_source(byok: Optional[str]) -> str:
    if (byok or '').strip():
        return 'byok'
    return 'module' if lighthouse().api_key else 'none'


def token_of(authorization: Optional[str]) -> str:
    token = identity.strip(authorization)
    if not token:
        raise HTTPException(401, 'the store needs your protocol token — sign in first')
    return token


def store_error(e: StoreError) -> HTTPException:
    # Pass the store's own verdict through rather than flattening it: 403 (not
    # whitelisted) and 451 (terms unsigned) are things the console must show
    # differently, and a 502 here would hide which one happened.
    status = e.status if 400 <= e.status < 600 else 502
    return HTTPException(status, e.message)


# ── public ───────────────────────────────────────────────────────────

@app.get('/health')
def health():
    return {'ok': True, 'service': 'lighthouse', 'time': int(time.time())}


@app.get('/status')
def status(authorization: Optional[str] = Header(default=None)):
    """Everything a console needs to render before the visitor does anything."""
    lh = lighthouse()
    out = lh.status()
    out.update({
        'version': VERSION,
        'base_path': BASE,
        'auth': identity.status(),
        'store': STORE.status(identity.strip(authorization)),
        'perpetual': True,
        'mcp': {'endpoint': 'POST /mcp', 'schema': 'GET /mcp',
                'transport': 'Streamable HTTP (JSON-RPC 2.0)',
                'stdio': 'python3 mcp.py', 'tools': len(mcp.TOOLS)},
    })
    return out


@app.get('/get')
def get(cid: str, download: bool = False, filename: Optional[str] = None):
    """Stream a CID through the Lighthouse gateway.

    Unauthenticated on purpose: an IPFS CID is public bytes to anyone who holds
    it. Access *control* is the store's job, and the store gates on its own
    side — this route is the plain gateway, not a way around that.
    """
    lh = lighthouse()
    url = f"{lh.gateway.rstrip('/')}/ipfs/{cid}"
    try:
        r = requests.get(url, stream=True, timeout=120)
    except requests.RequestException as e:
        raise HTTPException(502, f'gateway unreachable: {e}')
    if r.status_code >= 400:
        raise HTTPException(404, f'gateway {r.status_code} for {cid}')
    headers = {}
    if download:
        headers['Content-Disposition'] = f'attachment; filename="{filename or cid}"'
    return StreamingResponse(
        r.iter_content(1 << 16),
        media_type=r.headers.get('Content-Type', 'application/octet-stream'),
        headers=headers)


@app.get('/preview')
def preview(cid: str, max_bytes: int = Query(default=65536, ge=1, le=1 << 20)):
    """Peek at a CID: decoded text when it is text, size and a truncated flag.

    The reading itself lives in mod.py, so this route, the MCP tool and the CLI
    are one implementation and cannot disagree about a CID.
    """
    try:
        return lighthouse().preview(cid, max_bytes=max_bytes)
    except LookupError as e:
        raise HTTPException(404, str(e))
    except RuntimeError as e:
        raise HTTPException(502, str(e))


# ── identity ─────────────────────────────────────────────────────────

@app.get('/me')
def me(authorization: Optional[str] = Header(default=None),
       x_lh_key: Optional[str] = Header(default=None)):
    address = caller(authorization)
    return {'address': address,
            'owner': identity.is_owner(address),
            'owner_address': identity.owner(),
            'key_source': key_source(x_lh_key),
            'store': STORE.status(identity.strip(authorization))}


# ── the Lighthouse key ───────────────────────────────────────────────

class KeyBody(BaseModel):
    api_key: str


@app.get('/key')
def key_get(authorization: Optional[str] = Header(default=None),
            x_lh_key: Optional[str] = Header(default=None)):
    """Is a key configured, and does Lighthouse still accept it?"""
    caller(authorization)
    out = lighthouse(x_lh_key).key_status()
    out['source'] = key_source(x_lh_key)
    return out


@app.post('/key')
def key_set(body: KeyBody, authorization: Optional[str] = Header(default=None)):
    """(owner) Persist the deployment's Lighthouse API key off-chain.

    Written to ~/.mod/lighthouse/credentials.json at 0600 — never config.json,
    which is committed. A caller who does not want to share the box's key can
    skip this entirely and send `x-lh-key` per request instead.
    """
    owner_caller(authorization)
    out = lighthouse().set_key(body.api_key)
    if out.get('error'):
        raise HTTPException(400, out['error'])
    return out


@app.get('/usage')
def usage(authorization: Optional[str] = Header(default=None),
          x_lh_key: Optional[str] = Header(default=None)):
    """Remote data usage for whichever key this request is using."""
    caller(authorization)
    try:
        return lighthouse(x_lh_key).usage()
    except Exception as e:
        raise HTTPException(502, f'lighthouse: {e}')


@app.get('/uploads')
def uploads(page: int = 1, authorization: Optional[str] = Header(default=None),
            x_lh_key: Optional[str] = Header(default=None)):
    """What Lighthouse itself lists under this key (remote, not our index)."""
    caller(authorization)
    try:
        return lighthouse(x_lh_key).uploads(page=page)
    except Exception as e:
        raise HTTPException(502, f'lighthouse: {e}')


# ── uploading ────────────────────────────────────────────────────────

def _register_after_put(result: dict, token: Optional[str], public: bool,
                        pool: Optional[str]) -> dict:
    """Best-effort store registration attached to an upload result.

    The upload already happened and the bytes are pinned forever; a store that
    is down, or a caller the store will not accept, must not turn that into a
    failed request. So the outcome is reported *inside* the result and the
    caller keeps their CID either way.
    """
    try:
        reg = STORE.register(token, cid=result['cid'], key=result.get('key'),
                             size=result.get('size'), url=result.get('url'),
                             public=public, pool=pool)
        return {'registered': True, **reg}
    except StoreError as e:
        return {'registered': False, 'error': e.message, 'status': e.status}


@app.post('/put')
async def put(file: UploadFile = File(...),
              key: Optional[str] = Form(default=None),
              register: bool = Form(default=True),
              public: bool = Form(default=False),
              pool: Optional[str] = Form(default=None),
              authorization: Optional[str] = Header(default=None),
              x_lh_key: Optional[str] = Header(default=None)):
    """Upload bytes to Lighthouse and (by default) register the CID in the store."""
    address = caller(authorization)
    lh = lighthouse(x_lh_key)
    if not lh.api_key:
        raise HTTPException(400, 'no Lighthouse API key: send x-lh-key, or ask '
                                 'the owner to POST /key (get one at '
                                 'https://files.lighthouse.storage)')
    name = key or file.filename or 'upload'
    tmp = Path(tempfile.mkdtemp(prefix='lighthouse-')) / Path(name).name
    try:
        with open(tmp, 'wb') as f:
            while chunk := await file.read(1 << 20):
                f.write(chunk)
        try:
            result = lh.put(str(tmp), owner=address, key=name)
        except Exception as e:
            raise HTTPException(502, f'lighthouse upload failed: {e}')
    finally:
        try:
            tmp.unlink(missing_ok=True)
            tmp.parent.rmdir()
        except OSError:
            pass
    if register:
        result['store'] = _register_after_put(result, identity.strip(authorization),
                                              public, pool)
    return result


class TextBody(BaseModel):
    text: str
    key: Optional[str] = None
    register: bool = True
    public: bool = False
    pool: Optional[str] = None


@app.post('/put/text')
def put_text(body: TextBody, authorization: Optional[str] = Header(default=None),
             x_lh_key: Optional[str] = Header(default=None)):
    """The same as /put for a string — what a console's text box needs."""
    address = caller(authorization)
    lh = lighthouse(x_lh_key)
    if not lh.api_key:
        raise HTTPException(400, 'no Lighthouse API key: send x-lh-key, or ask '
                                 'the owner to POST /key')
    name = body.key or f'text-{int(time.time())}.txt'
    tmp = Path(tempfile.mkdtemp(prefix='lighthouse-')) / Path(name).name
    try:
        tmp.write_text(body.text)
        try:
            result = lh.put(str(tmp), owner=address, key=name)
        except Exception as e:
            raise HTTPException(502, f'lighthouse upload failed: {e}')
    finally:
        try:
            tmp.unlink(missing_ok=True)
            tmp.parent.rmdir()
        except OSError:
            pass
    if body.register:
        result['store'] = _register_after_put(result, identity.strip(authorization),
                                              body.public, body.pool)
    return result


# ── this module's index ──────────────────────────────────────────────

@app.get('/list')
def list_objects(limit: int = Query(default=100, ge=1, le=1000),
                 scope: str = Query(default='mine', pattern='^(mine|all)$'),
                 authorization: Optional[str] = Header(default=None)):
    """What this module has pushed: the caller's rows, or all of them (owner)."""
    address = caller(authorization)
    if scope == 'all' and not identity.is_owner(address) and address != 'open-mode':
        raise HTTPException(403, 'scope=all is owner only')
    rows = lighthouse().list(owner=None if scope == 'all' else address, limit=limit)
    gateway = lighthouse().gateway.rstrip('/')
    for row in rows:
        row['url'] = f"{gateway}/ipfs/{row['cid']}"
    return {'owner': address, 'scope': scope, 'count': len(rows), 'objects': rows}


class PinBody(BaseModel):
    cid: str


@app.post('/pin')
def pin(body: PinBody, authorization: Optional[str] = Header(default=None)):
    """Record a CID in this module's index.

    Lighthouse uploads are already pinned perpetually, so for our own CIDs this
    is bookkeeping. For a foreign CID it is an intent, and it says so.
    """
    address = caller(authorization)
    return lighthouse().pin(cid=body.cid, owner=address)


@app.delete('/rm')
def rm(cid: str, authorization: Optional[str] = Header(default=None)):
    """Drop a row from this module's index.

    It does not unpin: Lighthouse pins are perpetual and paid for, and pretending
    a delete reaches Filecoin would be a lie. The bytes stay reachable by CID.
    """
    address = caller(authorization)
    rows = lighthouse().list(owner=address, limit=100000)
    if not any(r['cid'] == cid for r in rows) and not identity.is_owner(address):
        raise HTTPException(403, f'{cid} is not yours')
    out = lighthouse().rm(cid)
    out['note'] = ('removed from this index only — the Lighthouse pin is '
                   'perpetual and the CID stays retrievable')
    return out


# ── the store bridge ─────────────────────────────────────────────────

@app.get('/store')
def store_status(authorization: Optional[str] = Header(default=None)):
    """The link, from the caller's side: reachable, whitelisted, terms, quota."""
    return STORE.status(identity.strip(authorization))


@app.get('/store/terms')
def store_terms(authorization: Optional[str] = Header(default=None)):
    try:
        return STORE.terms(identity.strip(authorization))
    except StoreError as e:
        raise store_error(e)


@app.post('/store/terms/accept')
def store_accept_terms(authorization: Optional[str] = Header(default=None)):
    """Accept the store's terms with the caller's own signed token as the proof.

    This module never signs for anybody: the token that arrives here is the one
    the store records, so the acceptance is the visitor's, not the box's.
    """
    caller(authorization)
    try:
        return STORE.accept_terms(token_of(authorization))
    except StoreError as e:
        raise store_error(e)


class RegisterBody(BaseModel):
    cid: str
    key: Optional[str] = None
    size: Optional[int] = None
    public: bool = False
    pool: Optional[str] = None


@app.post('/store/register')
def store_register(body: RegisterBody,
                   authorization: Optional[str] = Header(default=None)):
    """Reference an existing Lighthouse CID in the store — no bytes move."""
    caller(authorization)
    lh = lighthouse()
    url = f"{lh.gateway.rstrip('/')}/ipfs/{body.cid}"
    try:
        return STORE.register(token_of(authorization), cid=body.cid, key=body.key,
                              size=body.size, url=url, public=body.public,
                              pool=body.pool)
    except StoreError as e:
        raise store_error(e)


@app.get('/store/objects')
def store_objects(limit: int = Query(default=200, ge=1, le=1000),
                  all_backends: bool = False,
                  authorization: Optional[str] = Header(default=None)):
    """The caller's store objects — Lighthouse-backed ones unless asked wider."""
    caller(authorization)
    try:
        objs = STORE.objects(token_of(authorization), limit=limit,
                             only_lighthouse=not all_backends)
    except StoreError as e:
        raise store_error(e)
    return {'count': len(objs), 'objects': objs,
            'filter': 'all backends' if all_backends else 'lighthouse only'}


class MirrorBody(BaseModel):
    cid: str
    public: bool = False
    pool: Optional[str] = None
    key: Optional[str] = None


@app.post('/store/mirror')
def store_mirror(body: MirrorBody,
                 authorization: Optional[str] = Header(default=None),
                 x_lh_key: Optional[str] = Header(default=None)):
    """Make an object the store already has perpetual.

    Fetched from the store with the caller's token (so their read rights, not
    ours), uploaded to Lighthouse, and registered back. The CID Lighthouse
    returns is usually identical — same content, same hash — but chunking can
    differ, so both are reported and the Lighthouse one is what gets registered.
    """
    address = caller(authorization)
    token = token_of(authorization)
    lh = lighthouse(x_lh_key)
    if not lh.api_key:
        raise HTTPException(400, 'no Lighthouse API key: send x-lh-key, or ask '
                                 'the owner to POST /key')
    tmpdir = Path(tempfile.mkdtemp(prefix='lighthouse-mirror-'))
    try:
        info = {}
        try:
            info = STORE.object_info(token, body.cid) or {}
        except StoreError:
            pass  # /object is a nicety; a readable CID is the requirement
        name = body.key or info.get('key') or body.cid
        local = tmpdir / Path(str(name)).name
        try:
            STORE.fetch(token, body.cid, local)
        except StoreError as e:
            raise store_error(e)
        try:
            result = lh.put(str(local), owner=address, key=str(name))
        except Exception as e:
            raise HTTPException(502, f'lighthouse upload failed: {e}')
    finally:
        for p in sorted(tmpdir.rglob('*'), reverse=True):
            try:
                p.unlink()
            except OSError:
                pass
        try:
            tmpdir.rmdir()
        except OSError:
            pass
    result['source_cid'] = body.cid
    result['same_cid'] = result['cid'] == body.cid
    result['store'] = _register_after_put(result, token, body.public, body.pool)
    return result


# ── mcp ──────────────────────────────────────────────────────────────
#
# The same tools as the CLI and the REST routes, spoken as MCP. `GET /mcp` is
# the whole schema as a document — a client should not have to be run before
# anyone can see what this server offers — and `POST /mcp` is the Streamable
# HTTP transport.

def _mcp_url(request: Request, suffix: str = '/mcp') -> str:
    """The url a client should point at, as seen from wherever this was asked.

    Three urls reach this one server and a config block naming the wrong one is
    a client that never connects, so the answer is rebuilt from the request
    rather than baked into the config:

        the gateway   https://modc2.com/api/lighthouse/mcp — caddy strips
                      `/api/lighthouse` before proxying and does not say so, so
                      the prefix is put back from the fleet's own routing
                      convention (`/api/<module>`), keyed off the forwarded host.
        the console   its page fetches `_api/mcp` on its own origin; the app
                      server proxies that here, forwarding the same
                      x-forwarded-* headers, so it lands in the case above and
                      the browser is handed the public API url — which is what
                      an MCP client can actually reach.
        the box       http://127.0.0.1:50680/mcp, or the /lighthouse/_api alias
                      when something calls that path on this port directly.
    """
    proto = request.headers.get('x-forwarded-proto') or request.url.scheme
    forwarded = request.headers.get('x-forwarded-host')
    if forwarded:
        return f"{proto}://{forwarded.split(',')[0].strip()}/api/{CONFIG['name']}{suffix}"
    host = request.headers.get('host') or request.url.netloc
    path = request.scope.get('lighthouse_path') or request.url.path
    prefix = path.rsplit('/mcp', 1)[0] if '/mcp' in path else path.rstrip('/')
    return f'{proto}://{host}{prefix}{suffix}'


@app.get('/mcp')
def mcp_schema(request: Request):
    """The MCP server as a document: protocol, transports, auth, every tool's
    input schema, and the config to paste into a client. No auth — a schema
    nobody can read is a server nobody adopts."""
    return mcp.describe(_mcp_url(request))


@app.get('/mcp/tools')
def mcp_tools(request: Request):
    """Just the tools — what `tools/list` answers, plus which transport and
    which auth each one needs."""
    doc = mcp.describe(_mcp_url(request))
    return {'count': doc['count'], 'url': doc['transports']['http']['url'],
            'instructions': doc['instructions'], 'tools': doc['tools']}


@app.get('/mcp/config')
def mcp_config(request: Request):
    """Client config for this deployment — http, stdio, and the claude CLI line."""
    return mcp.client_config(_mcp_url(request))


@app.post('/mcp')
async def mcp_rpc(request: Request,
                  body: Any = Body(default=None),
                  authorization: Optional[str] = Header(default=None),
                  x_lh_key: Optional[str] = Header(default=None)):
    """MCP over Streamable HTTP.

    Auth is the module's, unchanged: a tool that acts for a signer needs the
    signer's token, and the 401 arrives at the transport where a client can see
    it rather than buried in a tool result. The context is built without the
    box's own keys — an HTTP caller gets their token and their Lighthouse key,
    never this deployment's identity.
    """
    if isinstance(body, list):
        raise HTTPException(400, 'JSON-RPC batching is not supported — send one '
                                 'message per request (MCP dropped batching in '
                                 '2025-06-18)')
    if isinstance(body, dict) and body.get('method') == 'tools/call':
        name = (body.get('params') or {}).get('name')
        if mcp.needs_auth(name):
            caller(authorization)
    ctx = mcp.Ctx(token=identity.strip(authorization), key=x_lh_key, local=False)
    response = mcp.handle(body, ctx)
    if response is None:
        return Response(status_code=202)          # a notification, per the spec
    return JSONResponse(response)


# ── openapi convenience ──────────────────────────────────────────────

@app.get('/endpoints')
def endpoints():
    """The config's own endpoint table — what this module says it does."""
    return CONFIG.get('endpoints', {})


if __name__ == '__main__':
    import argparse
    import uvicorn

    ap = argparse.ArgumentParser(description='lighthouse api')
    ap.add_argument('--port', type=int,
                    default=int(os.environ.get('LIGHTHOUSE_API_PORT',
                                               CONFIG.get('port', 50680))))
    ap.add_argument('--host', default=os.environ.get('LIGHTHOUSE_API_HOST', '0.0.0.0'))
    args = ap.parse_args()
    uvicorn.run(app, host=args.host, port=args.port)
