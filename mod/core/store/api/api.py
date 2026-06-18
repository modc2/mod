"""
store api — FastAPI gateway secured by the mod **protocol auth system**.

Auth model
    Clients sign a small payload with their wallet key (MetaMask / any mod key)
    and send the resulting protocol token as `Authorization: Bearer <token>`.
    The token is a base64url-encoded `{data, time, key, signature}` envelope —
    the exact shape produced/verified by `mod core/server/auth` (`m.mod('auth')`).
    No server-side nonce or session table: the token is a self-describing,
    time-bounded signature, verified statelessly on every request.

Access control
    Only the owner/admin or whitelisted addresses may store. The whitelist and
    owner live OFF-CHAIN under ~/.mod/store/ (never in committed config.json):
        ~/.mod/store/owner.json      {"owner": "0x.."}        (admin, unlimited)
        ~/.mod/store/whitelist.json  ["0x..", ...]            (allowed uploaders)
        ~/.mod/store/quotas.json     {"0x..": <bytes>}        (per-user overrides)
    Empty whitelist AND no owner ⇒ open access (back-compat / bootstrap).

Quota
    Each non-admin address gets `quota_bytes` (config.json, default 100 MiB) of
    storage, overridable per address in quotas.json. The admin is unlimited.

Endpoints
    GET  /health
    GET  /status
    GET  /backends
    GET  /me                      caller address, admin flag, quota
    GET  /quota                   caller usage + limit
    POST /quota                   (owner) set a per-user byte limit
    GET  /whitelist               owner + allowed addresses
    POST /whitelist               (owner) add an address
    DELETE /whitelist             (owner) remove an address
    POST /put                     (whitelisted) upload to filecoin/hippius/both
    GET  /get?cid=...             retrieve by CID
    POST /pin                     pin a CID
    GET  /list                    list caller's objects
    DELETE /rm?cid=...            delete record

Run (under pm2 — see ecosystem.config.js):
    uvicorn api.api:app --host 0.0.0.0 --port 50150
"""
import json
import os
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, Header, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

# Import the top-level `mod` package BEFORE polluting sys.path with the
# store module dir (its parent `mod/core/` contains a `mod.py` that would
# otherwise shadow the real package).
import mod as m  # noqa: E402

mod_dir = Path(__file__).resolve().parent.parent
config_path = mod_dir / 'config.json'
CONFIG = json.loads(config_path.read_text()) if config_path.exists() else {}

# Unified decentralized backend (filecoin + hippius). The core 'store' KV is separate.
store_mod = m.mod('dstore')()

# ── protocol auth ──────────────────────────────────────────────
# Wallet-signed, time-bounded bearer tokens verified by mod's auth module.
SESSION_TTL = int(os.environ.get('STORE_SESSION_TTL') or 86400 * 7)  # 7 days
AUTH = m.mod('auth')(crypto_type='ecdsa', max_age=SESSION_TTL)

DEFAULT_QUOTA = int(CONFIG.get('quota_bytes') or 100 * 1024 * 1024)  # 100 MiB

# ── off-chain access state (never committed) ───────────────────
PRIVATE_DIR = Path(os.environ.get('STORE_PRIVATE_DIR') or os.path.expanduser('~/.mod/store'))
PRIVATE_DIR.mkdir(parents=True, exist_ok=True)
WHITELIST_PATH = PRIVATE_DIR / 'whitelist.json'
OWNER_PATH = PRIVATE_DIR / 'owner.json'
QUOTAS_PATH = PRIVATE_DIR / 'quotas.json'


def read_owner() -> Optional[str]:
    """Admin address: ~/.mod/store/owner.json, else config 'admin'/'owner'."""
    try:
        owner = json.loads(OWNER_PATH.read_text()).get('owner', '').lower()
        if owner:
            return owner
    except Exception:
        pass
    cfg = (CONFIG.get('admin') or CONFIG.get('owner') or '').lower()
    return cfg or None


def read_whitelist() -> list:
    try:
        data = json.loads(WHITELIST_PATH.read_text())
    except Exception:
        return []
    if isinstance(data, dict):
        data = data.get('addresses', [])
    if not isinstance(data, list):
        return []
    return sorted({str(a).lower() for a in data if isinstance(a, str) and a.startswith('0x')})


def write_whitelist(addresses: list) -> None:
    clean = sorted({str(a).lower() for a in addresses if isinstance(a, str) and a.startswith('0x')})
    WHITELIST_PATH.write_text(json.dumps(clean, indent=2))


def read_quotas() -> dict:
    try:
        data = json.loads(QUOTAS_PATH.read_text())
        return {str(k).lower(): int(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except Exception:
        return {}


def write_quotas(quotas: dict) -> None:
    QUOTAS_PATH.write_text(json.dumps({k.lower(): int(v) for k, v in quotas.items()}, indent=2))


def is_admin(address: str) -> bool:
    owner = read_owner()
    return bool(owner) and address.lower() == owner


def is_authorized(address: str) -> bool:
    """Owner ∪ whitelist; if whitelist is empty AND no owner is set, allow all."""
    addr = address.lower()
    owner = read_owner()
    wl = read_whitelist()
    if not owner and not wl:
        return True
    if owner and addr == owner:
        return True
    return addr in wl


def quota_limit(address: str) -> Optional[int]:
    """Byte allowance for an address; None ⇒ unlimited (admin)."""
    if is_admin(address):
        return None
    return read_quotas().get(address.lower(), DEFAULT_QUOTA)


def quota_view(address: str) -> dict:
    addr = address.lower()
    used = store_mod.usage(owner=addr).get('bytes', 0)
    limit = quota_limit(addr)
    return {
        'address': addr,
        'admin': is_admin(addr),
        'used_bytes': used,
        'limit_bytes': limit,
        'unlimited': limit is None,
        'remaining_bytes': None if limit is None else max(0, limit - used),
    }


# ── token verification ─────────────────────────────────────────

def require_session(authorization: Optional[str]) -> str:
    """Verify a protocol-auth bearer token → lowercase signer address."""
    if not authorization or not authorization.startswith('Bearer '):
        raise HTTPException(401, 'missing bearer token')
    token = authorization[7:].strip()
    try:
        headers = AUTH.verify(token)
    except Exception as e:
        raise HTTPException(401, f'invalid or expired token: {e}')
    addr = str(headers.get('key', '')).lower()
    if not addr.startswith('0x'):
        raise HTTPException(401, 'token missing signer address')
    return addr


def require_authorized(authorization: Optional[str]) -> str:
    addr = require_session(authorization)
    if not is_authorized(addr):
        raise HTTPException(403, f'{addr} is not on the store whitelist')
    return addr


def require_owner(authorization: Optional[str]) -> str:
    addr = require_session(authorization)
    owner = read_owner()
    if not owner:
        # No owner set yet: any authenticated address may manage (bootstrap).
        return addr
    if addr != owner:
        raise HTTPException(403, 'owner only')
    return addr


# ── app ────────────────────────────────────────────────────────

app = FastAPI(title='store', description=CONFIG.get('description', 'store'))
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'], allow_methods=['*'], allow_headers=['*'], allow_credentials=False,
)


@app.get('/health')
def health():
    return {'ok': True, 'service': 'store', 'time': int(time.time())}


@app.get('/status')
def status():
    return store_mod.status()


@app.get('/backends')
def backends():
    return {'backends': store_mod.backends()}


# ── identity / authorization ──

@app.get('/me')
def me(authorization: Optional[str] = Header(default=None)):
    addr = require_session(authorization)
    return {
        'address': addr,
        'authorized': is_authorized(addr),
        'admin': is_admin(addr),
        'quota': quota_view(addr),
    }


# ── quota ──

@app.get('/quota')
def quota_get(authorization: Optional[str] = Header(default=None)):
    addr = require_session(authorization)
    return quota_view(addr)


class QuotaBody(BaseModel):
    address: str
    limit_bytes: int


@app.post('/quota')
def quota_set(body: QuotaBody, authorization: Optional[str] = Header(default=None)):
    require_owner(authorization)
    addr = body.address.strip().lower()
    if not addr.startswith('0x') or len(addr) != 42:
        raise HTTPException(400, 'address must be 0x-prefixed 42 chars')
    quotas = read_quotas()
    quotas[addr] = int(body.limit_bytes)
    write_quotas(quotas)
    return {'address': addr, 'limit_bytes': quotas[addr]}


# ── whitelist management (owner only) ──

@app.get('/whitelist')
def whitelist_get():
    return {'owner': read_owner(), 'addresses': read_whitelist()}


class WhitelistBody(BaseModel):
    address: str


@app.post('/whitelist')
def whitelist_add(body: WhitelistBody, authorization: Optional[str] = Header(default=None)):
    require_owner(authorization)
    addr = body.address.strip().lower()
    if not addr.startswith('0x') or len(addr) != 42:
        raise HTTPException(400, 'address must be 0x-prefixed 42 chars')
    wl = read_whitelist()
    if addr not in wl:
        wl.append(addr)
        write_whitelist(wl)
    return {'addresses': read_whitelist(), 'added': addr}


@app.delete('/whitelist')
def whitelist_rm(address: str, authorization: Optional[str] = Header(default=None)):
    require_owner(authorization)
    addr = address.strip().lower()
    wl = [a for a in read_whitelist() if a != addr]
    write_whitelist(wl)
    return {'addresses': read_whitelist(), 'removed': addr}


# ── storage ────────────────────────────────────────────────────

@app.post('/put')
async def put(
    file: UploadFile = File(...),
    backend: str = Form('filecoin'),
    key: Optional[str] = Form(None),
    authorization: Optional[str] = Header(default=None),
):
    owner = require_authorized(authorization)

    cache_dir = Path(os.path.expanduser('~/.store-mod/upload'))
    cache_dir.mkdir(parents=True, exist_ok=True)
    tmp = cache_dir / f'{int(time.time()*1000)}-{file.filename}'
    size = 0
    with open(tmp, 'wb') as f:
        while True:
            chunk = await file.read(1 << 20)
            if not chunk:
                break
            size += len(chunk)
            f.write(chunk)

    # Quota enforcement — admin is unlimited (quota_limit returns None).
    limit = quota_limit(owner)
    if limit is not None:
        used = store_mod.usage(owner=owner).get('bytes', 0)
        # 'both' stores to two backends ⇒ counts against quota twice.
        multiplier = 2 if backend.lower() == 'both' else 1
        if used + size * multiplier > limit:
            tmp.unlink(missing_ok=True)
            raise HTTPException(
                413,
                f'quota exceeded: {used + size * multiplier} > {limit} bytes '
                f'(used {used}, uploading {size}×{multiplier})',
            )

    try:
        return store_mod.put(path=str(tmp), backend=backend, owner=owner, key=key)
    finally:
        tmp.unlink(missing_ok=True)


@app.get('/get')
def get(cid: str, backend: Optional[str] = None):
    out = Path(os.path.expanduser(f'~/.store-mod/cache/{cid}'))
    out.parent.mkdir(parents=True, exist_ok=True)
    r = store_mod.get(cid=cid, backend=backend, out=str(out))
    if 'error' in r:
        raise HTTPException(404, r['error'])
    return FileResponse(out, filename=cid)


class PinBody(BaseModel):
    cid: str
    backend: str = 'filecoin'


@app.post('/pin')
def pin(body: PinBody, authorization: Optional[str] = Header(default=None)):
    owner = require_authorized(authorization)
    return store_mod.pin(cid=body.cid, backend=body.backend, owner=owner)


@app.get('/list')
def list_objects(
    backend: Optional[str] = None,
    limit: int = 100,
    authorization: Optional[str] = Header(default=None),
):
    owner = require_session(authorization)
    return {'owner': owner, 'objects': store_mod.list(owner=owner, backend=backend, limit=limit)}


@app.delete('/rm')
def rm(cid: str, authorization: Optional[str] = Header(default=None)):
    require_authorized(authorization)
    return store_mod.rm(cid)


@app.get('/')
def root():
    return {
        'name': 'store',
        'description': CONFIG.get('description'),
        'app': CONFIG.get('urls', {}).get('app'),
        'auth': 'mod-protocol',
        'endpoints': sorted(CONFIG.get('endpoints', {}).keys()),
    }
