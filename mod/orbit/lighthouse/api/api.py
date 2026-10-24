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
import html as _html
import importlib.util
import io
import json
import os
import re
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
import lhpay  # noqa: E402  — x402: strangers pay per upload in USDC
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
                   allow_headers=['*'], allow_credentials=False,
                   expose_headers=['X-PAYMENT-RESPONSE'])


@app.middleware('http')
async def api_alias(request, call_next):
    """`/lighthouse/_api/*` is this same API, one path segment along.

    Behind the gateway the API answers on /lighthouse/api and the console on
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
    """A client bound to the caller's key if they brought one, else the box's —
    which the box mints from its own wallet the first time it is needed."""
    lh = CORE.Mod(api_key=(byok or '').strip() or None)
    if not (byok or '').strip():
        lh.ensure_key()
    return lh


def key_source(byok: Optional[str]) -> str:
    if (byok or '').strip():
        return 'byok'
    lh = lighthouse()
    return lh.key_source() if lh.api_key else 'none'


def caller_or_anon(authorization: Optional[str]) -> str:
    """Who an upload is recorded under: the signer, or `anon` when nobody
    signed. A bad token is still a 401 — silence is anonymous, lying is not."""
    if not identity.strip(authorization):
        return CORE.ANON
    return caller(authorization)


def no_key_error(lh) -> HTTPException:
    why = CORE.Mod._mint_error
    return HTTPException(400, 'no Lighthouse API key and the deployment could not '
                              f'mint one from its wallet ({why}) — send x-lh-key, '
                              'or on the box run `m lighthouse/mint_key`' if why else
                              'no Lighthouse API key: auto-enrolment is off '
                              '(LIGHTHOUSE_AUTO_KEY=0) — send x-lh-key, or on the '
                              'box run `m lighthouse/mint_key` or `m lighthouse/set_key` '
                              '(dashboard key: files.lighthouse.storage)')


def anon_guard(lh, address: str, byok: Optional[str], size: int) -> None:
    if address == CORE.ANON and not (byok or '').strip():
        try:
            lh.anon_check(size)
        except PermissionError as e:
            raise HTTPException(429, str(e))


def exemption(request: Request, authorization: Optional[str],
              byok: Optional[str]) -> str:
    """Why this caller does not pay for an upload ('' = they do): their own
    Lighthouse key, this box itself, or the owner's token."""
    if (byok or '').strip():
        return 'byok'
    if lhpay.is_local(request.client.host if request.client else '', request.headers):
        return 'local'
    try:
        address = identity.whoami(authorization)
    except identity.AuthError:
        address = None
    if address and identity.is_owner(address):
        return 'owner'
    return ''


def charge(request: Request, authorization: Optional[str], byok: Optional[str],
           size: int, resource: str) -> 'lhpay.Charge':
    """The x402 gate, before a byte leaves the box. A 402 carries `accepts`;
    the caller signs one and retries with X-PAYMENT."""
    c = lhpay.Charge(request.headers, size, resource,
                     exempt=exemption(request, authorization, byok))
    if c.refusal:
        status, body = c.refusal
        raise PaymentRefused(status, body)
    return c


class PaymentRefused(Exception):
    def __init__(self, status: int, body: dict):
        self.status, self.body = status, body


@app.exception_handler(PaymentRefused)
async def _payment_refused(request: Request, e: PaymentRefused):
    # A plain JSON body, not FastAPI's {detail}: an x402 client reads
    # `accepts` at the top level of the 402.
    return JSONResponse(e.body, status_code=e.status)


def nothing_sent(request: Request, authorization: Optional[str],
                 byok: Optional[str], path: str, what: str):
    """An empty request is how x402 clients and crawlers ask the price: answer
    the 402 for the smallest upload, or say what is missing if they need not pay."""
    charge(request, authorization, byok, 0, resource_url(request, path))
    raise HTTPException(422, f'nothing to store — send {what}')


def paid(result: dict, c: 'lhpay.Charge') -> JSONResponse:
    """Settle after the CID came back and hand the receipt over."""
    info, extra = c.settle(result.get('cid', ''))
    result['payment'] = info
    return JSONResponse(result, headers=extra)


def resource_url(request: Request, path: str) -> str:
    proto = request.headers.get('x-forwarded-proto') or request.url.scheme
    host = request.headers.get('x-forwarded-host')
    if host:
        return f"{proto}://{host.split(',')[0].strip()}/{CONFIG['name']}/api{path}"
    return f"{proto}://{request.headers.get('host') or request.url.netloc}{path}"


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
        'pay': lhpay.public(),
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
    try:
        r, _ = lighthouse().fetch(cid, timeout=120)
    except LookupError as e:
        raise HTTPException(404, str(e))
    except RuntimeError as e:
        raise HTTPException(502, str(e))
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


# ── x402 ─────────────────────────────────────────────────────────────

def _extract_html_meta(url: str) -> dict:
    """Fetch the first 4096 bytes of an HTML URL and return title + og:image."""
    try:
        snippet = b''
        r = requests.get(url, headers={'Range': 'bytes=0-4095'}, timeout=10,
                         allow_redirects=True, stream=True)
        if r.status_code == 206:
            snippet = r.content
        else:
            # Server ignored Range; read just enough bytes from the stream
            for chunk in r.iter_content(chunk_size=4096):
                snippet += chunk
                if len(snippet) >= 4096:
                    break
        text = snippet.decode('utf-8', errors='replace')
        m = re.search(r'<title[^>]*>(.*?)</title>', text, re.I | re.S)
        title = _html.unescape(m.group(1).strip()) if m else ''
        og = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', text, re.I)
        if not og:
            og = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', text, re.I)
        og_image = og.group(1).strip() if og else ''
        return {'title': title, 'og_image': og_image}
    except Exception:
        return {'title': '', 'og_image': ''}


@app.get('/fetch/info')
def fetch_info(url: str = Query(...)):
    """Pre-flight HEAD on a URL: returns content_type, size, and accessibility.

    Used by the console to show a preview card before the user archives a URL,
    and to get the byte count so x402 payment can be signed for the right amount.
    """
    try:
        head = requests.head(url, timeout=10, allow_redirects=True)
        if head.status_code < 400:
            content_type = head.headers.get('Content-Type', '').split(';')[0].strip()
            size = int(head.headers.get('Content-Length', 0) or 0)
            title = ''
            og_image = ''
            if content_type.startswith('text/html'):
                meta = _extract_html_meta(url)
                title = meta['title']
                og_image = meta['og_image']
            return {
                'url': url,
                'content_type': content_type,
                'size': size,
                'accessible': True,
                'title': title,
                'og_image': og_image,
            }
        # HEAD returned 4xx/5xx (e.g. 405 Method Not Allowed) — fall back to streaming GET
        r = requests.get(url, timeout=10, allow_redirects=True, stream=True)
        content_type = r.headers.get('Content-Type', '').split(';')[0].strip()
        size = int(r.headers.get('Content-Length', 0) or 0)
        title = ''
        og_image = ''
        if content_type.startswith('text/html'):
            snippet = b''
            for chunk in r.iter_content(4096):
                snippet += chunk
                break
            text = snippet.decode('utf-8', errors='replace')
            m = re.search(r'<title[^>]*>(.*?)</title>', text, re.I | re.S)
            if m:
                title = _html.unescape(m.group(1).strip())
            og = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', text, re.I)
            if not og:
                og = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', text, re.I)
            if og:
                og_image = og.group(1).strip()
        return {
            'url': url,
            'content_type': content_type,
            'size': size,
            'accessible': r.status_code < 400,
            'title': title,
            'og_image': og_image,
        }
    except requests.RequestException as e:
        return {
            'url': url,
            'content_type': '',
            'size': 0,
            'accessible': False,
            'title': '',
            'error': str(e),
        }


@app.get('/price')
def price(request: Request, size: int = Query(default=0, ge=0),
          authorization: Optional[str] = Header(default=None),
          x_lh_key: Optional[str] = Header(default=None)):
    """What an upload of `size` bytes costs THIS caller, before sending it.
    `due` false = free for you (and `why`); otherwise `x402` is the exact 402
    body /put would answer, so a client can sign first and upload once."""
    p = lhpay.policy()
    why = exemption(request, authorization, x_lh_key)
    out = {'size': size, 'due': lhpay.active(p) and not why, 'why': why or None,
           'usd': lhpay.usd(lhpay.cost(size, p)), 'units': lhpay.cost(size, p),
           'pay': lhpay.public(p)}
    if out['due']:
        out['x402'] = lhpay.required(out['units'], resource_url(request, '/put'),
                                     None, p, size=size)
    return out


@app.get('/pay')
def pay_info(authorization: Optional[str] = Header(default=None)):
    """The x402 policy: price, chains, USDC asset + EIP-712 domain, pay_to,
    facilitator. The owner also sees the ledger."""
    out = {**lhpay.public(), 'stats': lhpay.stats()}
    try:
        address = identity.whoami(authorization)
    except identity.AuthError:
        address = None
    if address and identity.is_owner(address):
        out['payments'] = lhpay.payments(50)
    return out


class PayBody(BaseModel):
    enabled: Optional[bool] = None
    pay_to: Optional[str] = None
    networks: Optional[List[str]] = None
    price_usd: Optional[float] = None
    price_per_mib_usd: Optional[float] = None
    max_mb: Optional[int] = None


@app.post('/pay')
def pay_set(body: PayBody, authorization: Optional[str] = Header(default=None)):
    """(owner) Change the price, chains or receiving address — off-chain."""
    owner_caller(authorization)
    try:
        return lhpay.public(lhpay.set_policy(**body.model_dump(exclude_none=True)))
    except lhpay.PayError as e:
        raise HTTPException(400, str(e))


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
    if not token:
        return {'registered': False,
                'error': 'not signed in — the store only hears from a signer; '
                         'the CID is pinned, register it later with POST '
                         '/store/register',
                'status': 401}
    try:
        reg = STORE.register(token, cid=result['cid'], key=result.get('key'),
                             size=result.get('size'), url=result.get('url'),
                             public=public, pool=pool)
        return {'registered': True, **reg}
    except StoreError as e:
        return {'registered': False, 'error': e.message, 'status': e.status}


@app.post('/put')
async def put(request: Request,
              file: Optional[UploadFile] = File(default=None),
              key: Optional[str] = Form(default=None),
              register: bool = Form(default=True),
              public: bool = Form(default=False),
              pool: Optional[str] = Form(default=None),
              authorization: Optional[str] = Header(default=None),
              x_lh_key: Optional[str] = Header(default=None)):
    """Upload bytes to Lighthouse and (by default) register the CID in the store.

    No token needed for the upload itself: an anonymous caller gets a CID,
    recorded under `anon` and spending the deployment key within its budget.
    The store half needs a signer and is reported, not demanded."""
    if file is None:
        nothing_sent(request, authorization, x_lh_key, '/put', 'a multipart `file`')
    address = caller_or_anon(authorization)
    lh = lighthouse(x_lh_key)
    if not lh.api_key:
        raise no_key_error(lh)
    name = key or file.filename or 'upload'
    tmp = Path(tempfile.mkdtemp(prefix='lighthouse-')) / Path(name).name
    try:
        with open(tmp, 'wb') as f:
            while chunk := await file.read(1 << 20):
                f.write(chunk)
        size = tmp.stat().st_size
        c = charge(request, authorization, x_lh_key, size, resource_url(request, '/put'))
        if c.mode == 'free':
            anon_guard(lh, address, x_lh_key, size)   # a paid upload paid for itself
        try:
            result = lh.put(str(tmp), owner=address, key=name)
        except Exception as e:
            raise HTTPException(502, f'lighthouse upload failed (not charged): {e}')
    finally:
        try:
            tmp.unlink(missing_ok=True)
            tmp.parent.rmdir()
        except OSError:
            pass
    if register:
        result['store'] = _register_after_put(result, identity.strip(authorization),
                                              public, pool)
    return paid(result, c)


class TextBody(BaseModel):
    text: Optional[str] = None
    key: Optional[str] = None
    register: bool = True
    public: bool = False
    pool: Optional[str] = None


@app.post('/put/text')
def put_text(request: Request, body: Optional[TextBody] = Body(default=None),
             authorization: Optional[str] = Header(default=None),
             x_lh_key: Optional[str] = Header(default=None)):
    """The same as /put for a string — what a console's text box needs."""
    if body is None or body.text is None:
        nothing_sent(request, authorization, x_lh_key, '/put/text', '{"text": "..."}')
    address = caller_or_anon(authorization)
    lh = lighthouse(x_lh_key)
    if not lh.api_key:
        raise no_key_error(lh)
    size = len(body.text.encode())
    c = charge(request, authorization, x_lh_key, size, resource_url(request, '/put/text'))
    if c.mode == 'free':
        anon_guard(lh, address, x_lh_key, size)
    name = body.key or f'text-{int(time.time())}.txt'
    tmp = Path(tempfile.mkdtemp(prefix='lighthouse-')) / Path(name).name
    try:
        tmp.write_text(body.text)
        try:
            result = lh.put(str(tmp), owner=address, key=name)
        except Exception as e:
            raise HTTPException(502, f'lighthouse upload failed (not charged): {e}')
    finally:
        try:
            tmp.unlink(missing_ok=True)
            tmp.parent.rmdir()
        except OSError:
            pass
    if body.register:
        result['store'] = _register_after_put(result, identity.strip(authorization),
                                              body.public, body.pool)
    return paid(result, c)


class UrlBody(BaseModel):
    url: Optional[str] = None
    key: Optional[str] = None
    register: bool = True
    public: bool = False
    pool: Optional[str] = None


@app.post('/put/url')
def put_url(request: Request, body: Optional[UrlBody] = Body(default=None),
            authorization: Optional[str] = Header(default=None),
            x_lh_key: Optional[str] = Header(default=None)):
    """Fetch a URL, store it to Lighthouse, and register the CID in the store.

    HEAD first for Content-Length (used as the x402 charge estimate), then GET
    into a tempdir, then lh.put — same outcome as /put for a file upload."""
    if body is None or body.url is None:
        nothing_sent(request, authorization, x_lh_key, '/put/url', '{"url": "https://..."}')
    address = caller_or_anon(authorization)
    lh = lighthouse(x_lh_key)
    if not lh.api_key:
        raise no_key_error(lh)
    try:
        head = requests.head(body.url, timeout=10, allow_redirects=True)
        if head.status_code < 400:
            size = int(head.headers.get('Content-Length', 0) or 0)
        else:
            # HEAD rejected (e.g. 405) — fall back to streaming GET for headers only
            r_probe = requests.get(body.url, timeout=10, allow_redirects=True, stream=True)
            size = int(r_probe.headers.get('Content-Length', 0) or 0)
            r_probe.close()
    except requests.RequestException as e:
        raise HTTPException(502, f'could not reach {body.url}: {e}')
    c = charge(request, authorization, x_lh_key, size, resource_url(request, '/put/url'))
    from urllib.parse import urlparse
    parsed = urlparse(body.url)
    filename = Path(parsed.path).name or 'archive.html'
    name = body.key or filename or 'archive.html'
    tmpdir = Path(tempfile.mkdtemp(prefix='lighthouse-url-'))
    local = tmpdir / Path(name).name
    try:
        try:
            resp = requests.get(body.url, timeout=60, stream=True)
            resp.raise_for_status()
        except requests.RequestException as e:
            raise HTTPException(502, f'fetch failed: {e}')
        with open(local, 'wb') as f:
            for chunk in resp.iter_content(1 << 16):
                if chunk:
                    f.write(chunk)
        actual_size = local.stat().st_size
        if c.mode == 'free':
            anon_guard(lh, address, x_lh_key, actual_size)
        og_image = ''
        ct = resp.headers.get('Content-Type', '').split(';')[0].strip()
        if ct.startswith('text/html'):
            try:
                with open(local, 'rb') as _f:
                    snippet = _f.read(8192).decode('utf-8', errors='replace')
                og = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', snippet, re.I)
                if not og:
                    og = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', snippet, re.I)
                if og:
                    og_image = og.group(1).strip()
            except Exception:
                pass
        _meta: dict = {'source_url': body.url}
        if og_image:
            _meta['og_image'] = og_image
        try:
            result = lh.put(str(local), owner=address, key=name, meta=_meta)
        except Exception as e:
            raise HTTPException(502, f'lighthouse upload failed (not charged): {e}')
    finally:
        try:
            local.unlink(missing_ok=True)
        except OSError:
            pass
        try:
            tmpdir.rmdir()
        except OSError:
            pass
    result['source_url'] = body.url
    if body.register:
        result['store'] = _register_after_put(result, identity.strip(authorization),
                                              body.public, body.pool)
    return paid(result, c)


# ── this module's index ──────────────────────────────────────────────

@app.get('/list')
def list_objects(limit: int = Query(default=100, ge=1, le=1000),
                 scope: str = Query(default='mine', pattern='^(mine|all)$'),
                 authorization: Optional[str] = Header(default=None)):
    """What this module has pushed: the caller's rows (the `anon` rows without
    a token), or all of them (owner)."""
    address = caller_or_anon(authorization)
    if scope == 'all' and not identity.is_owner(address) and address != 'open-mode':
        raise HTTPException(403, 'scope=all is owner only')
    rows = lighthouse().list(owner=None if scope == 'all' else address, limit=limit)
    gateway = lighthouse().gateway.rstrip('/')
    for row in rows:
        row['url'] = f"{gateway}/ipfs/{row['cid']}"
        meta_raw = row.get('meta')
        if meta_raw:
            try:
                meta_obj = json.loads(meta_raw)
                if isinstance(meta_obj, dict):
                    if 'source_url' in meta_obj:
                        row['source_url'] = meta_obj['source_url']
                    if 'og_image' in meta_obj:
                        row['og_image'] = meta_obj['og_image']
            except (json.JSONDecodeError, TypeError):
                pass
    return {'owner': address, 'scope': scope, 'count': len(rows), 'objects': rows}


class PinBody(BaseModel):
    cid: str


@app.post('/pin')
def pin(body: PinBody, authorization: Optional[str] = Header(default=None)):
    """Record a CID in this module's index.

    Lighthouse uploads are already pinned perpetually, so for our own CIDs this
    is bookkeeping. For a foreign CID it is an intent, and it says so.
    """
    address = caller_or_anon(authorization)
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
def store_mirror(body: MirrorBody, request: Request,
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
        c = charge(request, authorization, x_lh_key, local.stat().st_size,
                   resource_url(request, '/store/mirror'))
        try:
            result = lh.put(str(local), owner=address, key=str(name))
        except Exception as e:
            raise HTTPException(502, f'lighthouse upload failed (not charged): {e}')
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
    return paid(result, c)


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

        the gateway   https://modc2.com/lighthouse/api/mcp — caddy strips
                      `/lighthouse/api` before proxying and does not say so, so
                      the prefix is put back from the fleet's own routing
                      convention (`/<module>/api`), keyed off the forwarded host.
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
        return f"{proto}://{forwarded.split(',')[0].strip()}/{CONFIG['name']}/api{suffix}"
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

    Nothing is required to get in: no API key, no token. A caller with no
    Authorization header is `anon` — they can store, preview and list; a tool
    that acts in the store answers an isError result naming the token it needs.
    That refusal lives in the tool result on purpose: a transport 401 sends an
    MCP client off to discover an OAuth server this module does not have,
    whereas a model that reads "send a Bearer token" can simply do so. A token
    that IS sent but does not verify is still a 401 — silence is anonymous,
    lying is not. The context is built without the box's own keys either way.
    """
    if isinstance(body, list):
        raise HTTPException(400, 'JSON-RPC batching is not supported — send one '
                                 'message per request (MCP dropped batching in '
                                 '2025-06-18)')
    if identity.strip(authorization):
        caller(authorization)                 # a bad token is refused, not ignored
    ctx = mcp.Ctx(token=identity.strip(authorization), key=x_lh_key, local=False,
                  payment=lhpay.payment_header(request.headers),
                  exempt=exemption(request, authorization, x_lh_key),
                  resource=_mcp_url(request))
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
