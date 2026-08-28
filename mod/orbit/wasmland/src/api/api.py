"""
wasmland — a marketplace for computations you can check.

Upload something that computes. Run it in your own tab or on this box. Get a
receipt. Have somebody else run it again and see whether they agree with you.
That last step is the whole product: everything else here exists so that a
result can be re-derived by a party who trusts nobody.

    /engines            the compute types this box carries, live and planned
    /artifacts          bytes, addressed by their own SHA-256
    /listings           artifacts with a name and a price
    /run                execute on the server venue and record it
    /runs/{id}/verify   replay it and attest — this is the verification
    /games              the ones the arena will seat agents against

The console is served from the same port at /wasmland, and talks to /wasmland/_api
so one page works behind the gateway and on a bare port alike.

WHAT THIS SERVER WILL NOT DO
    It will not run a module outside the sandbox, it will not serve a paid
    artifact's bytes to somebody who hasn't bought it, and it will not mark a
    run verified because the runner said so. Those three are the module.
"""
import base64
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Body, FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel

# The module root holds a mod.py, so it must not be on sys.path when `mod` is
# imported — src/storage.py handles that, and this file only needs the package.
SRC = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SRC.parent))

from src import engines, games, identity, market, receipts, sandbox, storage  # noqa: E402

VERSION = '1.0.0'
APP_DIR = SRC / 'app'
RUNTIME_DIR = SRC / 'runtime'
OWNER_PATH = Path(os.path.expanduser('~/.mod/wasmland/owner.json'))

app = FastAPI(title='wasmland', version=VERSION,
              description='A marketplace for verifiable computation')
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_methods=['*'],
                   allow_headers=['*'])


@app.middleware('http')
async def api_alias(request, call_next):
    """/wasmland/_api/* is the same API, one path segment along.

    Behind the gateway the API answers on /api/wasmland and the console on
    /wasmland; on a bare port both are here. Rather than teach the page two
    shapes — and pay for a CORS preflight on every call — it always asks its
    own origin for `_api`, and that is rewritten to the real route here.
    """
    path = request.scope.get('path', '')
    if path.startswith('/wasmland/_api'):
        request.scope['path'] = path[len('/wasmland/_api'):] or '/'
    return await call_next(request)


# ── who ──────────────────────────────────────────────────────────────

def caller(authorization: Optional[str], required: bool = True) -> Optional[str]:
    try:
        if required:
            return identity.require(authorization)
        return identity.whoami(authorization)
    except identity.AuthError as e:
        raise HTTPException(401, str(e))


def owner() -> Optional[str]:
    """Whoever claimed this box. First signed caller to ask, then fixed."""
    try:
        return json.loads(OWNER_PATH.read_text()).get('address')
    except (OSError, ValueError):
        return None


def claim_owner(address: str) -> str:
    OWNER_PATH.parent.mkdir(parents=True, exist_ok=True)
    OWNER_PATH.write_text(json.dumps({'address': address}))
    return address


def require_owner(authorization: Optional[str]) -> str:
    who = caller(authorization)
    current = owner()
    if current is None:
        return claim_owner(who)
    if who != current and who != 'open-mode':
        raise HTTPException(403, f'owner only — this box is claimed by {current}')
    return who


# ── info ─────────────────────────────────────────────────────────────

@app.get('/')
def root():
    return {
        'module': 'wasmland',
        'version': VERSION,
        'description': 'A marketplace for computations anyone can re-run and check',
        'compute_types': {e['id']: e['status'] for e in engines.descriptors()},
        'venues': ['browser', 'server'],
        'verification': 'independent replay; two agreeing runs make a receipt verified',
        'storage': 'the store mod — no private database',
        'console': '/wasmland',
        'endpoints': [
            'GET /engines', 'GET /venues', 'POST /inspect',
            'GET|POST /artifacts', 'GET /artifacts/{id}', 'GET /artifacts/{id}/raw',
            'GET|POST /listings', 'GET|DELETE /listings/{id}',
            'POST /listings/{id}/buy', 'POST /listings/{id}/arena',
            'POST /run', 'POST /runs/claim', 'GET /runs', 'GET /runs/{id}',
            'POST /runs/{id}/verify', 'GET /account/{address}',
            'POST /credits/grant', 'GET /games',
            'POST /auth/challenge', 'POST /auth/verify', 'GET /auth/me',
            'GET /runtime/{file}',
        ],
    }


@app.get('/health')
def health():
    return {'ok': True, 'version': VERSION, 'store': storage.stats(),
            'server_venue': sandbox.capabilities()['ok'], 'owner': owner()}


@app.get('/engines')
def list_engines():
    """Every compute type, live and planned. Adding one is one entry in
    src/engines.py — nothing in this API is wasm-shaped."""
    return {'engines': engines.descriptors(),
            'live': [e.id for e in engines.live()],
            'note': ('planned compute types are declared, not implemented: they '
                     'are listed so a listing can name one and so the shape is '
                     'fixed before the code exists')}


@app.get('/venues')
def venues():
    """Where a run can happen, and what each venue actually enforces."""
    return {
        'server': sandbox.capabilities(),
        'browser': {
            'venue': 'browser', 'ok': True,
            'isolation': 'a Worker the page can terminate; no filesystem, no sockets offered',
            'determinism': 'the same seeded host as the server — src/runtime/ is one file set',
            'trust': ('a browser result is a claim by a machine nobody here '
                      'controls. It is recorded as claimed and becomes verified '
                      'when an independent replay agrees.'),
        },
    }


# ── auth ─────────────────────────────────────────────────────────────

class VerifyRequest(BaseModel):
    address: str
    message: str
    signature: str


@app.post('/auth/challenge')
def auth_challenge(address: str = Body(..., embed=True)):
    return identity.challenge(address)


@app.post('/auth/verify')
def auth_verify(req: VerifyRequest):
    """Check a wallet signature and hand back a session for the tab to hold."""
    try:
        address = identity.from_signature(req.address, req.message, req.signature)
    except identity.AuthError as e:
        raise HTTPException(401, str(e))
    session = identity.mint_session(address)
    return {**session, 'owner': address == (owner() or address)}


@app.get('/auth/me')
def auth_me(authorization: Optional[str] = Header(None)):
    who = caller(authorization, required=False)
    return {'address': who, 'owner': bool(who and who == owner()),
            'auth': identity.status(),
            'account': market.account(who) if who else None}


# ── artifacts ────────────────────────────────────────────────────────

@app.post('/inspect')
def inspect(b64: Optional[str] = Body(None), text: Optional[str] = Body(None),
            filename: str = Body(''), engine: Optional[str] = Body(None)):
    """Read bytes without storing them: what they import, export, and are."""
    data = base64.b64decode(b64) if b64 else (text or '').encode()
    if not data:
        raise HTTPException(400, 'nothing to inspect')
    kind = engine or engines.guess(filename, data)
    try:
        return {'engine': kind, **engines.inspect(kind, data)}
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post('/artifacts')
async def upload(file: Optional[UploadFile] = File(None),
                 b64: Optional[str] = Form(None), text: Optional[str] = Form(None),
                 filename: str = Form(''), engine: Optional[str] = Form(None),
                 authorization: Optional[str] = Header(None)):
    """Store bytes under their own hash. Idempotent: same bytes, same id."""
    caller(authorization)
    if file is not None:
        data = await file.read()
        filename = filename or file.filename or ''
    elif b64:
        data = base64.b64decode(b64)
    elif text:
        data = text.encode()
    else:
        raise HTTPException(400, 'send a file, b64= or text=')
    if not data:
        raise HTTPException(400, 'empty upload')

    kind = engine or engines.guess(filename, data)
    try:
        manifest = engines.inspect(kind, data)
    except ValueError as e:
        raise HTTPException(400, f'{kind}: {e}')
    return storage.put_artifact(data, kind, manifest, filename)


@app.get('/artifacts')
def artifacts(limit: int = 100):
    return {'artifacts': storage.records('artifacts', limit=limit)}


@app.get('/artifacts/{artifact_id}')
def artifact(artifact_id: str):
    got = storage.artifact_record(artifact_id)
    if not got:
        raise HTTPException(404, f'no artifact {artifact_id[:12]}')
    return got


@app.get('/artifacts/{artifact_id}/raw')
def artifact_raw(artifact_id: str, listing: Optional[str] = None,
                 authorization: Optional[str] = Header(None)):
    """The bytes — this is what the browser venue fetches before it can run.

    Paid listings gate here, because for a browser run this *is* the sale: once
    the tab has the bytes it can run them without asking again. The console
    says so at the point of purchase rather than implying a per-run meter it
    cannot enforce.
    """
    record = storage.artifact_record(artifact_id)
    if not record:
        raise HTTPException(404, f'no artifact {artifact_id[:12]}')
    if listing:
        who = caller(authorization, required=False) or ''
        try:
            market.require_entitlement(who, listing)
        except market.MarketError as e:
            raise HTTPException(402, str(e))
    else:
        # No listing named: only free-to-all artifacts may be fetched bare.
        paid = [x for x in market.listings(limit=1000)
                if x['artifact'] == artifact_id and x.get('price')]
        if paid:
            raise HTTPException(
                402, f'these bytes are listed for sale ({paid[0]["id"]}) — '
                     'name the listing you bought with ?listing=')
    data = storage.get_artifact(artifact_id)
    if data is None:
        raise HTTPException(404, 'the record is here but the bytes are gone')
    engine = engines.get(record['engine'])
    return Response(content=data, media_type=engine.media_type)


# ── listings ─────────────────────────────────────────────────────────

class ListingRequest(BaseModel):
    artifact: str
    title: str = ''
    description: str = ''
    entry: str = ''
    price: float = 0.0
    tags: List[str] = []
    license: str = ''


@app.get('/listings')
def get_listings(engine: Optional[str] = None, role: Optional[str] = None,
                 tag: Optional[str] = None, seller: Optional[str] = None,
                 q: Optional[str] = None, free: Optional[bool] = None,
                 limit: int = 100):
    return {'listings': market.listings(engine=engine, role=role, tag=tag,
                                        seller=seller, q=q, free=free, limit=limit)}


@app.post('/listings')
def create_listing(req: ListingRequest, authorization: Optional[str] = Header(None)):
    who = caller(authorization)
    try:
        return market.publish(req.artifact, who, title=req.title,
                              description=req.description, entry=req.entry,
                              price=req.price, tags=req.tags, license=req.license)
    except market.MarketError as e:
        raise HTTPException(400, str(e))


@app.get('/listings/{listing_id}')
def get_listing(listing_id: str):
    try:
        got = market.listing(listing_id)
    except market.MarketError as e:
        raise HTTPException(404, str(e))
    return {**got, 'runs_recorded': len(receipts.runs(listing=listing_id, limit=200))}


@app.delete('/listings/{listing_id}')
def delete_listing(listing_id: str, authorization: Optional[str] = Header(None)):
    who = caller(authorization)
    try:
        return market.unpublish(listing_id, who)
    except market.MarketError as e:
        raise HTTPException(403, str(e))


@app.post('/listings/{listing_id}/buy')
def buy(listing_id: str, authorization: Optional[str] = Header(None)):
    who = caller(authorization)
    try:
        return market.charge(who, listing_id)
    except market.MarketError as e:
        raise HTTPException(402, str(e))


@app.post('/listings/{listing_id}/arena')
def to_arena(listing_id: str, authorization: Optional[str] = Header(None)):
    """Hand a game listing to the arena, where agents play it."""
    who = caller(authorization)
    try:
        return games.publish_listing(listing_id, who)
    except (market.MarketError, ValueError) as e:
        raise HTTPException(400, str(e))


# ── running ──────────────────────────────────────────────────────────

class RunRequest(BaseModel):
    artifact: Optional[str] = None
    listing: Optional[str] = None
    entry: str = ''
    input: str = ''
    seed: int = 0
    engine: Optional[str] = None


def _resolve(req) -> tuple:
    """(artifact_id, engine, entry, listing_id) from either kind of request."""
    if req.listing:
        try:
            got = market.listing(req.listing)
        except market.MarketError as e:
            raise HTTPException(404, str(e))
        return got['artifact'], got['engine'], req.entry or got['entry'], got['id']
    if not req.artifact:
        raise HTTPException(400, 'name an artifact= or a listing=')
    record = storage.artifact_record(req.artifact)
    if not record:
        raise HTTPException(404, f'no artifact {req.artifact[:12]}')
    return req.artifact, req.engine or record['engine'], req.entry or 'run', None


@app.post('/run')
def run(req: RunRequest, authorization: Optional[str] = Header(None)):
    """Execute on this box, in the sandbox, and record the run with a receipt."""
    who = caller(authorization, required=False) or 'anon'
    artifact_id, engine, entry, listing_id = _resolve(req)
    if listing_id:
        try:
            market.charge(who, listing_id)
        except market.MarketError as e:
            raise HTTPException(402, str(e))
    try:
        out = receipts.run_here(artifact_id, engine, entry=entry, input=req.input,
                                seed=req.seed, runner=who, listing=listing_id)
    except sandbox.RunFailed as e:
        raise HTTPException(400, f'{e}')
    except (ValueError, NotImplementedError) as e:
        raise HTTPException(400, str(e))
    if listing_id:
        market.touch(listing_id)
    return out


class ClaimRequest(BaseModel):
    artifact: str
    engine: str = 'wasm'
    entry: str = 'run'
    input: str = ''
    seed: int = 0
    result: Dict[str, Any]
    listing: Optional[str] = None


@app.post('/runs/claim')
def claim(req: ClaimRequest, authorization: Optional[str] = Header(None)):
    """Record a run the caller performed elsewhere — a tab, or another box.

    Stored as claimed, never as verified. POST /runs/{id}/verify is how it
    becomes worth something.
    """
    who = caller(authorization, required=False) or 'browser'
    out = receipts.claim(req.artifact, req.engine, req.result, entry=req.entry,
                         input=req.input, seed=req.seed, runner=who)
    if req.listing:
        out['listing'] = req.listing
        storage.put_record('runs', out['id'], out)
        market.touch(req.listing)
    return out


@app.get('/runs')
def list_runs(limit: int = 50, artifact: Optional[str] = None,
              listing: Optional[str] = None, status: Optional[str] = None):
    return {'runs': receipts.runs(limit=limit, artifact=artifact,
                                  listing=listing, status=status)}


@app.get('/runs/{run_id}')
def get_run(run_id: str):
    got = storage.get_record('runs', run_id)
    if not got:
        raise HTTPException(404, f'no run {run_id}')
    return got


@app.post('/runs/{run_id}/verify')
def verify_run(run_id: str, authorization: Optional[str] = Header(None)):
    """Replay a recorded run here and attest to the result.

    The replay reads the job out of the stored run, so a claimant cannot
    smuggle a different computation into its own verification.
    """
    who = caller(authorization, required=False) or 'server'
    try:
        out = receipts.verify(run_id, verifier=who)
    except sandbox.RunFailed as e:
        raise HTTPException(400, f'replay failed: {e}')
    except ValueError as e:
        raise HTTPException(400, str(e))
    if out.get('listing') and out['verdict']['status'] == 'verified':
        market.touch(out['listing'], verified=True)
    return out


# ── accounts ─────────────────────────────────────────────────────────

@app.get('/account/{address}')
def get_account(address: str):
    acct = market.account(address)
    return {**acct,
            'listings': market.listings(seller=address, limit=100),
            'runs': receipts.runs(limit=25)}


@app.post('/credits/grant')
def grant(address: str = Body(...), amount: float = Body(...),
          reason: str = Body('grant'), authorization: Optional[str] = Header(None)):
    """Credits into an account. Owner only — this is the mint."""
    require_owner(authorization)
    try:
        return market.grant(address, amount, reason)
    except market.MarketError as e:
        raise HTTPException(400, str(e))


# ── games ────────────────────────────────────────────────────────────

@app.get('/games')
def list_games(limit: int = 100):
    return {'arena': games.available(), 'games': games.games(limit=limit)}


# ── the browser venue's own code ─────────────────────────────────────

@app.get('/runtime/{name}')
def runtime(name: str):
    """The execution layer, served to the tab.

    The console imports the same files the server runner does, off this route.
    That is not a convenience: it is why a run in a tab and its replay here are
    the same computation rather than two implementations that agree for now.
    """
    if not name.endswith('.mjs') or '/' in name or '..' in name:
        raise HTTPException(404, 'no such runtime file')
    path = RUNTIME_DIR / name
    if not path.is_file():
        raise HTTPException(404, f'no runtime file {name}')
    return FileResponse(path, media_type='text/javascript')


# ── the console ──────────────────────────────────────────────────────

@app.get('/wasmland', response_class=HTMLResponse)
@app.get('/wasmland/', response_class=HTMLResponse)
def console():
    index = APP_DIR / 'index.html'
    if not index.is_file():
        raise HTTPException(404, 'console not built')
    return HTMLResponse(index.read_text())


@app.get('/wasmland/{asset:path}')
def console_asset(asset: str):
    """The console's own files. `_api/*` never reaches here — the middleware
    above rewrites it to the API route before routing happens."""
    path = (APP_DIR / asset).resolve()
    if not str(path).startswith(str(APP_DIR.resolve())) or not path.is_file():
        raise HTTPException(404, f'no such asset {asset}')
    kinds = {'.js': 'text/javascript', '.mjs': 'text/javascript',
             '.css': 'text/css', '.html': 'text/html', '.svg': 'image/svg+xml',
             '.png': 'image/png', '.json': 'application/json'}
    return FileResponse(path, media_type=kinds.get(path.suffix, 'text/plain'))


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=int(os.environ.get('PORT', 50480)))
