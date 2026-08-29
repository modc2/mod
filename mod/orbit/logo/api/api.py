"""
logo api — the fleet's brand marks, on their own port.

Reads are public. A logo is the thing everyone sees; gating it on a session
would only make the corner flicker for visitors.

Writes are owner-signed, and the owner is the *target module's* — read from
that module's own config.json, never from a list this module keeps. The caller
presents a mod-protocol token (`m.mod('auth').token`, or one `personal_sign`
from a browser wallet) and the recovered address has to match. This module
holds no credential that stands in for anyone, which is the property that lets
a different console render the editor: orbit/build proxies the owner's signed
token to this API and cannot mint one, so showing the UI never grants it the
power to change a mark.

Routes (module may be `build` or, to name one side of a name collision,
`orbit/store` / `core/store`):

    GET    /health
    GET    /status
    GET    /whoami                     (Bearer) address + what it may write
    GET    /marks                      every module that has set a mark
    GET    /logo/{module}              the mark to draw
    GET    /logo/{module}/image        uploaded bytes, CSP-hardened
    GET    /logo/{module}/owner        who may write it, and where that came from
    POST   /logo/{module}              owner-only: {glyph|url|dataUrl|reset}
    DELETE /logo/{module}              owner-only: back to the cube

Run:
    python3 api/api.py --port 50760
    uvicorn api.api:app --host 0.0.0.0 --port 50760
"""
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import Body, FastAPI, Header, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import identity  # noqa: E402
import marks  # noqa: E402

CONFIG = json.loads((ROOT / 'config.json').read_text())
VERSION = CONFIG.get('version', '1.0.0')
BASE = CONFIG.get('base_path', '/logo')

app = FastAPI(title='logo', version=VERSION,
              description=CONFIG.get('description', 'The fleet brand-mark service'))
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_methods=['*'],
                   allow_headers=['*'], allow_credentials=False)


@app.middleware('http')
async def api_alias(request: Request, call_next):
    """`/logo/_api/*` is this same API, one path segment along.

    Behind the gateway the API answers on /api/logo and the console on /logo,
    and the console asks its OWN origin for `_api` so a wallet token never
    crosses an origin. Accepting the alias here too means the same stored
    image `src` resolves whether it is fetched through the console's proxy or
    straight off this port.
    """
    path = request.scope.get('path', '')
    for prefix in (f'{BASE}/_api', '/_api'):
        if path.startswith(prefix):
            request.scope['path'] = path[len(prefix):] or '/'
            break
    return await call_next(request)


def _bearer(authorization: Optional[str], x_mod_token: Optional[str]) -> Optional[str]:
    """The protocol token, from either header.

    `Authorization: Bearer <token>` is the normal way in. `x-mod-token` exists
    for callers that already spend Authorization on a different session — a
    console proxying through its own API, for instance — so the owner's signed
    token can ride alongside rather than displace it.
    """
    return identity.strip(x_mod_token) or identity.strip(authorization)


def _fail(exc: Exception) -> HTTPException:
    if isinstance(exc, identity.UnknownModule):
        return HTTPException(404, str(exc))
    if isinstance(exc, identity.AuthError):
        return HTTPException(401, str(exc))
    if isinstance(exc, marks.BadMark):
        return HTTPException(400, str(exc))
    return HTTPException(500, f'{type(exc).__name__}: {exc}')


def _split(rest: str):
    """`orbit/build/image` -> ('orbit/build', 'image').

    A trailing `image` / `owner` is only an op when what precedes it actually
    resolves to a module — otherwise a module honestly named `image` would
    become unaddressable, and `/logo/orbit/image` would 404 on the wrong half.
    """
    parts = [p for p in (rest or '').split('/') if p]
    if not parts:
        raise identity.UnknownModule('name a module')
    whole = '/'.join(parts)
    if len(parts) > 1 and parts[-1] in ('image', 'owner'):
        head = '/'.join(parts[:-1])
        try:
            identity.resolve(head)
            return head, parts[-1]
        except identity.UnknownModule:
            pass                       # not a module — `whole` had better be
    return whole, None


# -- the module itself ------------------------------------------------

@app.get('/health')
def health() -> Dict[str, Any]:
    return {'ok': True, 'module': 'logo', 'version': VERSION,
            'marks': len(marks.marks()), 'open_mode': identity.open_mode()}


@app.get('/config')
def config() -> Dict[str, Any]:
    return CONFIG


@app.get('/status')
def status() -> Dict[str, Any]:
    return {'ok': True, 'module': 'logo', 'version': VERSION,
            'auth': identity.status(),
            'limits': {'max_image_bytes': marks.MAX_IMAGE_BYTES,
                       'glyph_chars': marks.MAX_GLYPH_CHARS,
                       'mime': sorted(marks.ALLOWED_MIME)},
            'public_base': marks.PUBLIC_BASE}


@app.get('/whoami')
def who(authorization: Optional[str] = Header(None),
        x_mod_token: Optional[str] = Header(None)) -> Dict[str, Any]:
    """Who this token says you are, and which of the modules that have marks
    you could change. Answers 200 with `address: null` for no token — "not
    signed in" is a state the console renders, not an error."""
    address = identity.whoami(_bearer(authorization, x_mod_token))
    owns = []
    if address:
        for entry in marks.marks():
            try:
                if identity.may_write(address, entry['module']):
                    owns.append(entry['module'])
            except identity.UnknownModule:
                continue
    return {'ok': True, 'address': address, 'owns': owns,
            'open_mode': identity.open_mode()}


@app.get('/marks')
def all_marks(base: Optional[str] = None) -> Dict[str, Any]:
    return {'ok': True, 'marks': marks.marks(base)}


# -- one module's mark ------------------------------------------------

@app.get('/logo/{rest:path}')
def get_logo(rest: str, base: Optional[str] = None, v: Optional[str] = None):
    try:
        module, op = _split(rest)
        if op == 'owner':
            return {'ok': True, **identity.owners(module)}
        if op == 'image':
            found = marks.image_bytes(module)
            if not found:
                raise HTTPException(404, f'{module} has no uploaded logo image')
            payload, mime = found
            return Response(
                content=payload,
                media_type=mime,
                headers={
                    # Immutable per `?v=` stamp, which every save bumps: a
                    # browser may cache it hard and still pick up the next
                    # mark the moment the owner changes it.
                    'cache-control': 'public, max-age=31536000, immutable',
                    # An uploaded SVG is markup running from OUR origin if
                    # somebody opens this URL directly. Deny it every resource
                    # and script it could reach, and stop the browser sniffing
                    # a type other than the one we declared.
                    'content-security-policy':
                        "default-src 'none'; style-src 'unsafe-inline'; sandbox",
                    'x-content-type-options': 'nosniff',
                    'content-disposition': 'inline',
                })
        return {'ok': True, 'module': identity.owners(module)['module'],
                'logo': marks.public(module, base=base)}
    except HTTPException:
        raise
    except Exception as e:
        raise _fail(e)


@app.post('/logo/{rest:path}')
def set_logo(rest: str, body: Dict[str, Any] = Body(default={}),
             authorization: Optional[str] = Header(None),
             x_mod_token: Optional[str] = Header(None)):
    """Set a module's mark. The one write, and the one gate."""
    try:
        module, op = _split(rest)
        if op:
            raise HTTPException(405, f'/{op} is read-only')
        who_signed = identity.require_owner(_bearer(authorization, x_mod_token), module)
        state = marks.apply(module, body or {}, by=who_signed)
        return {'ok': True, 'module': identity.owners(module)['module'],
                'by': who_signed, 'logo': marks.public(module, state)}
    except HTTPException:
        raise
    except Exception as e:
        raise _fail(e)


@app.delete('/logo/{rest:path}')
def clear_logo(rest: str,
               authorization: Optional[str] = Header(None),
               x_mod_token: Optional[str] = Header(None)):
    """Back to the protocol's cube."""
    try:
        module, op = _split(rest)
        if op:
            raise HTTPException(405, f'/{op} is read-only')
        who_signed = identity.require_owner(_bearer(authorization, x_mod_token), module)
        state = marks.apply(module, {'reset': True}, by=who_signed)
        return {'ok': True, 'module': identity.owners(module)['module'],
                'by': who_signed, 'logo': marks.public(module, state)}
    except HTTPException:
        raise
    except Exception as e:
        raise _fail(e)


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    """One error shape, and never a bare 5xx body: a gateway that strips 5xx
    bodies would turn an explainable refusal into `error code: 502`."""
    return JSONResponse({'ok': False, 'error': exc.detail},
                        status_code=exc.status_code, headers=exc.headers or {})


def main():
    import argparse
    import uvicorn
    parser = argparse.ArgumentParser(description='logo api')
    parser.add_argument('--port', type=int,
                        default=int(os.environ.get('LOGO_API_PORT', CONFIG.get('port', 50760))))
    parser.add_argument('--host', default=os.environ.get('LOGO_API_HOST', '0.0.0.0'))
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port, log_level='info')


if __name__ == '__main__':
    main()
