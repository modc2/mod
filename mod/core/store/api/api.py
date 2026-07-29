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
        ~/.mod/store/terms.json      {"0x..": {version, ts, proof}}  (signed ToS)
        ~/.mod/store/takedowns.json  [{cid, by, reason, ts}]  (moderation audit)
    Empty whitelist AND no owner ⇒ open access (back-compat / bootstrap).

Terms of service (liability)
    Every uploader must sign-accept terms.md (versioned) before /put or
    /register: acceptance is recorded with the caller's wallet-signed session
    token as proof. The admin may remove ANY content (illegal/infringing) via
    DELETE /rm; non-owner removals are appended to the takedown audit log.

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
    POST /put                     (whitelisted) upload; form: public, pool, key
    POST /register                reference an external CID (cid-agnostic)
    GET  /get?cid=…[&token=…]     retrieve by CID (read-gated for private)
    GET  /preview?cid=…           peek content (truncated text + size flag)
    GET  /object?cid=…            full info: stored when/by-whom + who has access
    POST /publish                 (owner) flip object private⇄public
    POST /pin                     pin a CID
    GET  /pins                    list caller's pinned objects
    DELETE /pin?cid=…             unpin
    GET  /list                    list caller's objects (+visibility/scheme/semhash)
    GET  /search?q=…[&semantic_q=…] filter + 1-bit semantic (Hamming) ranking
    GET  /shared                  objects shared WITH caller (grants + pools)
    DELETE /rm?cid=…[&reason=…]   delete own object; admin may remove ANY (logged)
    GET  /terms                   current terms text + version (+accepted if auth)
    POST /terms/accept            sign-accept current terms (required to store)
    GET  /terms/accepts           (owner) audit: who accepted what, when
    GET  /takedowns               (owner) audit log of admin content removals

  timed access grants
    POST   /grants                grant addr timed read/write to a CID or all
    GET    /grants                grants I made + grants made to me
    DELETE /grants/{id}           revoke

  data pools (mutual access)
    POST   /pools                 create a pool (caller = owner)
    GET    /pools                 pools I own or belong to
    GET    /pools/{id}            members + objects (members only)
    DELETE /pools/{id}            delete a pool (owner only)
    POST   /pools/{id}/members    add a member (role, optional timed)
    DELETE /pools/{id}/members    remove a member / leave
    POST   /pools/{id}/objects    pool an object (owner/editor)
    DELETE /pools/{id}/objects    unpool an object

  QR auth handoff (computer ↔ phone)
    POST /handoff                 mint one-time code carrying my session token
    GET  /handoff/{code}          claim → token (single use, short TTL)

  one-time access tickets (single-use, anti-replay)
    POST /tickets                 mint single-use short-TTL fetch ticket (default 10s)
    GET  /tickets                 my active (unused, unexpired) tickets
    GET  /ticket/{code}           redeem → serve object exactly once

  marketplace (listings over stored objects; BlocTime-priced access)
    GET    /market                browse the public storefront (q/tag/sort/free)
    POST   /market/list           list an object you own (title/desc/tags/price_bloc)
    DELETE /market/list           delist (seller; admin ⇒ logged takedown)
    POST   /market/acquire        get an item — free, or hold >= price_bloc BlocTime
    POST   /market/like           toggle a like (one per wallet)
    GET    /market/mine           my listings + my acquisitions

  on-chain (chain mod: BlocTime + Registry)
    GET  /onchain                 Registry registration + BlocTime gate status
    GET  /onchain/bloctime        caller's BlocTime balance + holder flag
    POST /onchain/register        (owner) register store in the chain Registry

  MCP (Model Context Protocol — api/mcp.py)
    POST /mcp                     JSON-RPC 2.0 Streamable HTTP: store tools for LLM agents

Access also honors on-chain BlocTime: a staked BlocTime holder is authorized to
store as if whitelisted (config bloctime_gate, default on).

Run (under pm2 — see ecosystem.config.js):
    uvicorn api.api:app --host 0.0.0.0 --port 50152
"""
import json
import os
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, Header, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel

# Import the top-level `mod` package BEFORE polluting sys.path with the
# store module dir (its parent `mod/core/` contains a `mod.py` that would
# otherwise shadow the real package).
import mod as m  # noqa: E402

from api.access import Access, infer_scheme  # noqa: E402
from api.market import Market  # noqa: E402
from api.onchain import OnChain  # noqa: E402
from api import semantic  # noqa: E402

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
TERMS_ACCEPTS_PATH = PRIVATE_DIR / 'terms.json'
TAKEDOWNS_PATH = PRIVATE_DIR / 'takedowns.json'

# Versioned terms of service every uploader must sign-accept before storing.
TERMS_PATH = mod_dir / 'terms.md'
TERMS_VERSION = str(CONFIG.get('terms_version', '1.0'))

# Access model: timed grants, data pools, QR auth handoff, CID-agnostic ACL.
# SQLite alongside the JSON access state — off-chain, never committed.
ACCESS = Access(PRIVATE_DIR / 'access.db')

# Marketplace: listings/likes/acquisitions over stored objects. Priced items
# are gated by on-chain BlocTime holdings; buying mints a permanent read grant.
MARKET = Market(PRIVATE_DIR / 'market.json')

# On-chain integration (chain mod): BlocTime holders earn store access, and the
# module registers itself in the chain Registry. Tunable via config.json:
#   bloctime_gate (default true), chain_network (default testnet),
#   bloctime_ttl (cache secs), onchain_registry (auto-register on startup).
_gate_env = os.environ.get('STORE_BLOCTIME_GATE')
ONCHAIN = OnChain(
    network=os.environ.get('STORE_CHAIN_NETWORK') or CONFIG.get('chain_network', 'testnet'),
    gate=(_gate_env.strip().lower() not in ('0', 'false', 'no', 'off'))
    if _gate_env is not None else CONFIG.get('bloctime_gate', True),
    ttl=int(CONFIG.get('bloctime_ttl', 60)),
    name='store',
)


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


def read_terms_text() -> str:
    try:
        return TERMS_PATH.read_text()
    except Exception:
        return f'Store terms of service v{TERMS_VERSION}: you are solely ' \
               'responsible for content you upload; the operator may remove ' \
               'any content at any time.'


def read_terms_accepts() -> dict:
    try:
        return json.loads(TERMS_ACCEPTS_PATH.read_text())
    except Exception:
        return {}


def has_accepted_terms(address: str) -> bool:
    rec = read_terms_accepts().get(address.lower())
    return bool(rec) and str(rec.get('version')) == TERMS_VERSION


def record_terms_accept(address: str, proof: str) -> dict:
    """Record a wallet-signed acceptance of the CURRENT terms version.

    `proof` is the caller's bearer token — a wallet-signed, time-bounded
    protocol-auth signature — kept off-chain in ~/.mod/store/terms.json as
    evidence the address accepted this version."""
    accepts = read_terms_accepts()
    rec = {'version': TERMS_VERSION, 'accepted_at': int(time.time()), 'proof': proof}
    accepts[address.lower()] = rec
    TERMS_ACCEPTS_PATH.write_text(json.dumps(accepts, indent=2))
    return rec


def read_takedowns() -> list:
    try:
        return json.loads(TAKEDOWNS_PATH.read_text())
    except Exception:
        return []


def log_takedown(cid: str, by: str, reason: Optional[str]) -> None:
    entries = read_takedowns()
    entries.append({'cid': cid, 'by': by.lower(),
                    'reason': reason or 'admin takedown', 'ts': int(time.time())})
    TAKEDOWNS_PATH.write_text(json.dumps(entries, indent=2))


def is_admin(address: str) -> bool:
    owner = read_owner()
    return bool(owner) and address.lower() == owner


def is_authorized(address: str) -> bool:
    """Owner ∪ whitelist ∪ on-chain BlocTime holders.

    If the whitelist is empty AND no owner is set, access is open (bootstrap).
    Otherwise a caller is authorized if they are the owner, on the off-chain
    whitelist, OR hold BlocTime on-chain — so access can be earned on-chain
    without the owner editing the whitelist."""
    addr = address.lower()
    owner = read_owner()
    wl = read_whitelist()
    if not owner and not wl:
        return True
    if owner and addr == owner:
        return True
    if addr in wl:
        return True
    return ONCHAIN.is_bloctime_holder(addr)


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


def optional_session(authorization: Optional[str], token_q: Optional[str] = None) -> Optional[str]:
    """Recover a signer from a bearer header OR a ?token= query (for QR/links),
    returning None when neither is present/valid rather than raising. Used by
    read paths where anonymous access to *public* objects is allowed."""
    raw = None
    if authorization and authorization.startswith('Bearer '):
        raw = authorization[7:].strip()
    elif token_q:
        raw = token_q.strip()
    if not raw:
        return None
    try:
        addr = str(AUTH.verify(raw).get('key', '')).lower()
        return addr if addr.startswith('0x') else None
    except Exception:
        return None


def caller_owns(cid: str, addr: str) -> bool:
    """True if `addr` is the recorded owner of `cid` in the dstore index or ACL."""
    rows = store_mod.list(owner=addr, limit=100000)
    if any(o.get('cid') == cid for o in rows):
        return True
    acl = ACCESS.get_acl(cid)
    return bool(acl and acl.get('owner') == addr.lower())


def require_authorized(authorization: Optional[str]) -> str:
    addr = require_session(authorization)
    if not is_authorized(addr):
        raise HTTPException(403, f'{addr} is not on the store whitelist')
    return addr


def require_terms(address: str) -> str:
    """Storing content requires a signed acceptance of the current terms."""
    if not has_accepted_terms(address):
        raise HTTPException(
            451,
            f'terms of service v{TERMS_VERSION} not accepted: '
            'GET /terms then POST /terms/accept before uploading',
        )
    return address


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


@app.get('/backends/status')
def backends_status(probe: bool = False,
                    authorization: Optional[str] = Header(default=None)):
    """Per-backend readiness for the app's BACKENDS tab.

    Light by default (no network round-trips); `?probe=1` additionally
    validates stored credentials against the remote service (owner only,
    since it can be slow and leaks validity)."""
    require_session(authorization)
    if probe:
        require_owner(authorization)
        keyed = store_mod.keys()
    else:
        keyed = {}
        for b in getattr(store_mod, 'KEYED_BACKENDS', ['hippius', 'lighthouse']):
            try:
                s = getattr(store_mod, b)().status()
                keyed[b] = {'configured': bool(s.get('configured')),
                            'needs_key': bool(s.get('needs_key'))}
            except Exception as e:
                keyed[b] = {'error': str(e), 'configured': False, 'needs_key': True}
    out = {}
    for b in store_mod.backends():
        if b == 'both':
            continue
        out[b] = keyed.get(b, {'configured': True, 'needs_key': False})
    return {'backends': out, 'probed': bool(probe)}


class BackendKeyBody(BaseModel):
    backend: str
    api_key: Optional[str] = None          # lighthouse
    s3_key: Optional[str] = None           # hippius
    s3_secret: Optional[str] = None
    s3_endpoint: Optional[str] = None
    s3_bucket: Optional[str] = None


@app.post('/backends/key')
def backends_set_key(body: BackendKeyBody,
                     authorization: Optional[str] = Header(default=None)):
    """(owner) Persist API credentials for a keyed backend.

    Keys are stored off-chain under ~/.mod/{hippius,lighthouse}/ — never in
    committed config — and picked up by the backend modules on next use."""
    require_owner(authorization)
    creds = {k: v for k, v in body.dict().items()
             if k != 'backend' and v is not None and str(v).strip()}
    if not creds:
        raise HTTPException(400, 'no credentials provided')
    r = store_mod.set_key(body.backend, **creds)
    if isinstance(r, dict) and r.get('error'):
        raise HTTPException(400, r['error'])
    return {'backend': body.backend.lower(), **(r if isinstance(r, dict) else {})}


# ── identity / authorization ──

@app.get('/me')
def me(authorization: Optional[str] = Header(default=None)):
    addr = require_session(authorization)
    bloctime = ONCHAIN.is_bloctime_holder(addr)
    owner = read_owner()
    via = ('config' if (owner and addr == owner) or addr in read_whitelist()
           else 'bloctime' if bloctime
           else 'open' if not owner and not read_whitelist()
           else None)
    return {
        'address': addr,
        'authorized': is_authorized(addr),
        'admin': is_admin(addr),
        'bloctime': bloctime,
        'via': via,
        'quota': quota_view(addr),
        'terms': {'version': TERMS_VERSION, 'accepted': has_accepted_terms(addr)},
    }


# ── terms of service (signed acceptance, required to store) ──

@app.get('/terms')
def terms_get(authorization: Optional[str] = Header(default=None)):
    addr = optional_session(authorization)
    out = {'version': TERMS_VERSION, 'text': read_terms_text(), 'required': True}
    if addr:
        out['address'] = addr
        out['accepted'] = has_accepted_terms(addr)
    return out


@app.post('/terms/accept')
def terms_accept(authorization: Optional[str] = Header(default=None)):
    addr = require_session(authorization)
    token = (authorization or '').split(' ', 1)[-1]
    rec = record_terms_accept(addr, proof=token)
    return {'address': addr, 'accepted': True,
            'version': rec['version'], 'accepted_at': rec['accepted_at']}


@app.get('/terms/accepts')
def terms_accepts(authorization: Optional[str] = Header(default=None)):
    """Owner-only liability audit: every signed acceptance on record."""
    require_owner(authorization)
    accepts = read_terms_accepts()
    return {'version': TERMS_VERSION, 'count': len(accepts),
            'accepts': {a: {'version': r.get('version'),
                            'accepted_at': r.get('accepted_at')}
                        for a, r in accepts.items()}}


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

def _apply_acl_and_pool(results: dict, owner: str, backend: str,
                        public: bool, pool: Optional[str], key: Optional[str],
                        semhash: Optional[str] = None):
    """Record ACL (visibility + scheme + semantic hash) for each stored CID,
    optionally drop it into a pool. Pool writes require a write role in that pool."""
    pool_role = ACCESS.member_role(pool, owner) if pool else None
    if pool and pool_role not in ('owner', 'editor'):
        raise HTTPException(403, f'need owner/editor in pool {pool} to add objects')
    for b, v in results.items():
        cid = v.get('cid') if isinstance(v, dict) else None
        if not cid:
            continue
        ACCESS.set_acl(cid, owner=owner, scheme=infer_scheme(cid), backend=b,
                       visibility='public' if public else 'private', semhash=semhash)
        if pool:
            ACCESS.add_object(pool, cid, backend=b, key=key, added_by=owner)


def _truthy(v) -> bool:
    return str(v).strip().lower() in ('1', 'true', 'yes', 'on')


def _detect_cid_refs(path: Path, exclude: set, cap: int = 1 << 16) -> list:
    """Best-effort scan of a file's text content for embedded references to
    other CIDs already known to the store — the signal that this object is a
    manifest/bundle *mapped from* pre-existing objects. Binary or unreadable
    content just yields no refs."""
    try:
        with open(path, 'rb') as f:
            text = f.read(cap).decode('utf-8')
    except (OSError, UnicodeDecodeError):
        return []
    known = {o.get('cid') for o in store_mod.list(limit=100000) if o.get('cid')}
    known -= exclude
    return [c for c in known if c in text]


@app.post('/put')
async def put(
    file: UploadFile = File(...),
    backend: str = Form('localfs'),
    key: Optional[str] = Form(None),
    public: str = Form('false'),
    pool: Optional[str] = Form(None),
    authorization: Optional[str] = Header(default=None),
):
    owner = require_terms(require_authorized(authorization))

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

    # Local 1-bit semantic encoder: fingerprint the content before we delete it.
    sem = semantic.encode_file(str(tmp))
    try:
        r = store_mod.put(path=str(tmp), backend=backend, owner=owner, key=key)
        new_cids = {v.get('cid') for v in r.get('results', {}).values()
                    if isinstance(v, dict) and v.get('cid')}
        refs = _detect_cid_refs(tmp, exclude=new_cids)
    finally:
        tmp.unlink(missing_ok=True)
    _apply_acl_and_pool(r.get('results', {}), owner, backend, _truthy(public), pool, key, semhash=sem)
    for cid in new_cids:
        ACCESS.set_refs(cid, refs)
    r['public'] = _truthy(public)
    r['semhash'] = sem
    r['refs'] = refs
    return r


class RegisterBody(BaseModel):
    cid: str
    scheme: Optional[str] = None
    backend: str = 'external'
    url: Optional[str] = None
    key: Optional[str] = None
    size: int = 0
    public: bool = False
    pool: Optional[str] = None


@app.post('/register')
def register(body: RegisterBody, authorization: Optional[str] = Header(default=None)):
    """Reference a CID stored in *any other* system (arweave, ipfs elsewhere,
    s3, …) so it becomes a first-class, shareable, poolable store object —
    without re-uploading bytes. The store is CID-agnostic by design."""
    owner = require_terms(require_authorized(authorization))
    cid = body.cid.strip()
    if not cid:
        raise HTTPException(400, 'cid required')
    scheme = body.scheme or infer_scheme(cid)
    store_mod.register(cid=cid, backend=body.backend, owner=owner, key=body.key,
                       size=body.size, scheme=scheme, url=body.url)
    ACCESS.set_acl(cid, owner=owner, scheme=scheme, backend=body.backend, url=body.url,
                   visibility='public' if body.public else 'private')
    if body.pool:
        if ACCESS.member_role(body.pool, owner) not in ('owner', 'editor'):
            raise HTTPException(403, f'need owner/editor in pool {body.pool}')
        ACCESS.add_object(body.pool, cid, backend=body.backend, scheme=scheme,
                          key=body.key, added_by=owner)
    return {'cid': cid, 'scheme': scheme, 'backend': body.backend, 'url': body.url,
            'public': body.public, 'pool': body.pool, 'registered': True}


@app.get('/get')
def get(cid: str, backend: Optional[str] = None, token: Optional[str] = None,
        authorization: Optional[str] = Header(default=None)):
    # Read gating: public objects (and pre-ACL unknowns) are open; private ones
    # need owner / live grant / shared pool. Token may ride in a header OR the
    # ?token= query so a scanned QR or a plain <a> link can authenticate.
    caller = optional_session(authorization, token)
    if not ACCESS.can_read(caller, cid):
        raise HTTPException(403, 'not authorized to read this object'
                            if caller else 'authentication required (private object)')
    # CID-agnostic: an external object resolves via its registered gateway url.
    acl = ACCESS.get_acl(cid)
    if acl and acl.get('url'):
        return RedirectResponse(acl['url'])
    out = Path(os.path.expanduser(f'~/.store-mod/cache/{cid}'))
    out.parent.mkdir(parents=True, exist_ok=True)
    r = store_mod.get(cid=cid, backend=backend, out=str(out))
    if 'error' in r:
        raise HTTPException(404, r['error'])
    return FileResponse(out, filename=cid)


class PublishBody(BaseModel):
    cid: str
    public: bool


@app.get('/preview')
def preview(cid: str, max_bytes: int = 65536, token: Optional[str] = None,
            authorization: Optional[str] = Header(default=None)):
    """Peek at an object's content for in-app display. Returns up to `max_bytes`
    (text decoded when possible) plus the full size + a `truncated` flag, so the
    UI can show a preview of huge content and offer 'copy all' (which fetches the
    rest via /get). Read-gated exactly like /get."""
    caller = optional_session(authorization, token)
    if not ACCESS.can_read(caller, cid):
        raise HTTPException(403, 'not authorized to read this object'
                            if caller else 'authentication required (private object)')
    acl = ACCESS.get_acl(cid)
    if acl and acl.get('url'):
        return {'cid': cid, 'external': True, 'url': acl['url'],
                'is_text': False, 'truncated': False}
    out = Path(os.path.expanduser(f'~/.store-mod/cache/{cid}'))
    out.parent.mkdir(parents=True, exist_ok=True)
    got = store_mod.get(cid=cid, backend=None, out=str(out))
    if 'error' in got:
        raise HTTPException(404, got['error'])
    size = out.stat().st_size
    cap = max(0, min(int(max_bytes), 1 << 20))
    with open(out, 'rb') as f:
        sample = f.read(cap)
    is_text, text = False, None
    if b'\x00' not in sample:
        try:
            text = sample.decode('utf-8')
            is_text = True
        except UnicodeDecodeError:
            is_text = False
    return {'cid': cid, 'size': size, 'truncated': size > len(sample),
            'preview_bytes': len(sample), 'is_text': is_text,
            'text': text, 'external': False}


@app.get('/object')
def object_info(cid: str, authorization: Optional[str] = Header(default=None)):
    """Full profile of one object: when it was stored, who stored it, where, its
    visibility + semantic hash, and who currently has access (grants + pools).
    Anyone who can read the object sees the basics; the detailed access roster
    (grantees, pool members) is shown to the owner/admin only."""
    caller = optional_session(authorization, None)
    if not ACCESS.can_read(caller, cid):
        raise HTTPException(403, 'not authorized to view this object')
    all_objs = store_mod.list(limit=100000)
    rows = [o for o in all_objs if o.get('cid') == cid]
    rows.sort(key=lambda o: o.get('timestamp') or 0)
    acl = ACCESS.get_acl(cid) or {}
    owner = acl.get('owner') or (rows[0].get('owner') if rows else None)
    first = rows[0] if rows else {}
    backends = sorted({o.get('backend') for o in rows if o.get('backend')})
    key_by_cid = {o['cid']: o.get('key') for o in all_objs if o.get('cid')}

    def _linked(cids: list) -> list:
        # Only surface CIDs the caller may actually see — the graph must not
        # leak the existence of private objects to non-owners.
        return [{'cid': c, 'key': key_by_cid.get(c)} for c in cids if ACCESS.can_read(caller, c)]

    info = {
        'cid': cid,
        'owner': owner,
        'stored_at': first.get('timestamp'),
        'key': first.get('key'),
        'size': first.get('size'),
        'backends': backends or ([acl['backend']] if acl.get('backend') else []),
        'scheme': acl.get('scheme') or infer_scheme(cid),
        'visibility': acl.get('visibility', 'public'),
        'semhash': acl.get('semhash'),
        'external_url': acl.get('url'),
        'pinned': ACCESS.is_pinned(cid),
        'links': {
            'out': _linked(ACCESS.refs_out(cid)),  # what this object was mapped from
            'in': _linked(ACCESS.refs_in(cid)),    # what was mapped from this object
        },
    }
    is_owner = bool(caller and (caller == (owner or '').lower() or is_admin(caller)))
    info['is_owner'] = is_owner
    if is_owner:
        info['grants'] = ACCESS.grants_on(cid, owner=owner)
        info['pools'] = [
            {'id': p['id'], 'name': p['name'], 'owner': p['owner'],
             'members': [{'address': mm['address'], 'role': mm['role'],
                          'expires_in': mm['expires_in'], 'expired': mm['expired']}
                         for mm in p['members']]}
            for p in ACCESS.pools_containing(cid)
        ]
    else:
        # Non-owners just learn whether THEY have access and how.
        info['grants'] = []
        info['pools'] = []
        info['you_can_read'] = ACCESS.can_read(caller, cid)
    return info


@app.post('/publish')
def publish(body: PublishBody, authorization: Optional[str] = Header(default=None)):
    """Flip an object between private and public. Owner only."""
    addr = require_session(authorization)
    if not (caller_owns(body.cid, addr) or is_admin(addr)):
        raise HTTPException(403, 'only the object owner may change visibility')
    acl = ACCESS.set_acl(body.cid, owner=addr, visibility='public' if body.public else 'private') \
        if ACCESS.get_acl(body.cid) is None else ACCESS.set_visibility(body.cid, body.public)
    return {'cid': body.cid, 'visibility': acl['visibility']}


class PinBody(BaseModel):
    cid: str
    backend: str = 'filecoin'


@app.post('/pin')
def pin(body: PinBody, authorization: Optional[str] = Header(default=None)):
    owner = require_authorized(authorization)
    r = store_mod.pin(cid=body.cid, backend=body.backend, owner=owner)
    ACCESS.add_pin(body.cid, body.backend, owner=owner)   # track for management
    return {**(r if isinstance(r, dict) else {'result': r}), 'pinned': True}


@app.get('/pins')
def pins_list(authorization: Optional[str] = Header(default=None)):
    """The caller's pinned objects, annotated with size/key/visibility."""
    owner = require_session(authorization)
    index = {o.get('cid'): o for o in store_mod.list(limit=100000)}
    pins = ACCESS.pins_for(owner)
    for p in pins:
        meta = index.get(p['cid'], {})
        p['size'] = meta.get('size')
        p['key'] = meta.get('key')
        acl = ACCESS.get_acl(p['cid'])
        p['visibility'] = acl['visibility'] if acl else 'public'
    return {'owner': owner, 'pins': pins}


@app.delete('/pin')
def unpin(cid: str, backend: Optional[str] = None,
          authorization: Optional[str] = Header(default=None)):
    """Unpin: best-effort backend unpin + drop the pin record."""
    require_authorized(authorization)
    removed = ACCESS.remove_pin(cid, backend)
    return {'cid': cid, 'backend': backend, 'unpinned': removed}


def _annotate(objects: list) -> list:
    """Attach ACL info (visibility, scheme, external url, semantic hash) to rows."""
    for o in objects:
        acl = ACCESS.get_acl(o.get('cid'))
        o['visibility'] = acl['visibility'] if acl else 'public'
        o['scheme'] = (acl or {}).get('scheme') or infer_scheme(o.get('cid', ''))
        o['url'] = (acl or {}).get('url')
        o['semhash'] = (acl or {}).get('semhash')
    return objects


@app.get('/list')
def list_objects(
    backend: Optional[str] = None,
    limit: int = 100,
    authorization: Optional[str] = Header(default=None),
):
    owner = require_session(authorization)
    objs = _annotate(store_mod.list(owner=owner, backend=backend, limit=limit))
    return {'owner': owner, 'objects': objs}


def _shared_objects(addr: str) -> dict:
    """Resolve everything shared *with* `addr` (grants + pool membership)."""
    s = ACCESS.shared_with(addr)
    objs = []
    seen = set()
    # Explicit CIDs (specific grants + pool objects): resolve from the index.
    index = {o.get('cid'): o for o in store_mod.list(limit=100000)}
    for cid, info in s['cids'].items():
        if cid in seen:
            continue
        seen.add(cid)
        base = dict(index.get(cid) or {'cid': cid, 'backend': info.get('backend'),
                                       'key': info.get('key'), 'owner': info.get('from')})
        base['shared_via'] = info.get('via')
        objs.append(base)
    # Wildcard grantors: every object they own is shared.
    for grantor in s['wildcard_grantors']:
        for o in store_mod.list(owner=grantor, limit=1000):
            if o['cid'] in seen:
                continue
            seen.add(o['cid'])
            o['shared_via'] = 'grant'
            objs.append(o)
    return {'objects': _annotate(objs), 'wildcard_grantors': s['wildcard_grantors']}


@app.get('/shared')
def shared(authorization: Optional[str] = Header(default=None)):
    """Objects shared *with* the caller via grants or pool membership."""
    addr = require_session(authorization)
    return {'address': addr, **_shared_objects(addr)}


@app.get('/search')
def search(
    q: str = '',
    backend: Optional[str] = None,
    scheme: Optional[str] = None,
    visibility: Optional[str] = None,
    semantic_q: Optional[str] = None,   # query for 1-bit semantic (Hamming) ranking
    scope: str = 'mine',                # 'mine' | 'shared' | 'all'
    limit: int = 200,
    authorization: Optional[str] = Header(default=None),
):
    """Search/filter objects. `q` matches CID or key (case-insensitive substring);
    optional exact filters on backend/scheme/visibility. When `semantic_q` is
    given, results are ranked by Hamming distance between the query's 1-bit
    semantic hash and each object's — nearest (most semantically similar) first.
    `scope` selects the caller's own objects, those shared with them, or both."""
    addr = require_session(authorization)
    pool = {}
    if scope in ('mine', 'all'):
        for o in _annotate(store_mod.list(owner=addr, limit=100000)):
            pool[o['cid']] = {**o, 'shared_via': None}
    if scope in ('shared', 'all'):
        for o in _shared_objects(addr)['objects']:
            pool.setdefault(o['cid'], o)  # don't overwrite an owned hit
    ql = (q or '').strip().lower()
    out = []
    for o in pool.values():
        if ql and ql not in (o.get('cid') or '').lower() and ql not in (o.get('key') or '').lower():
            continue
        if backend and o.get('backend') != backend:
            continue
        if scheme and o.get('scheme') != scheme:
            continue
        if visibility and o.get('visibility') != visibility:
            continue
        out.append(o)

    if semantic_q and semantic_q.strip():
        qfp = semantic.encode(semantic_q.strip())
        bits = semantic.DEFAULT_BITS
        for o in out:
            sh = o.get('semhash')
            if sh:
                d = semantic.hamming(qfp, semantic.from_hex(sh))
                o['distance'] = d
                o['similarity'] = round(1 - d / bits, 4)
            else:
                o['distance'] = None
                o['similarity'] = None
        # Nearest first; objects lacking a semantic hash sink to the bottom.
        out.sort(key=lambda o: (o['distance'] is None, o['distance'] if o['distance'] is not None else 1 << 30))
    else:
        out.sort(key=lambda o: o.get('timestamp') or 0, reverse=True)
    return {'address': addr, 'query': q, 'semantic_q': semantic_q, 'scope': scope,
            'count': len(out), 'objects': out[:max(1, int(limit))]}


@app.delete('/rm')
def rm(cid: str, reason: Optional[str] = None,
       authorization: Optional[str] = Header(default=None)):
    """Delete a stored object. Callers may remove their OWN objects; the
    module owner (admin) may remove ANY object — e.g. illegal or infringing
    content — and such takedowns are appended to an off-chain audit log."""
    addr = require_session(authorization)
    own = caller_owns(cid, addr)
    if not own and not is_admin(addr):
        raise HTTPException(403, 'not your object (only the module owner may remove others\' content)')
    r = store_mod.rm(cid)
    if not own:
        log_takedown(cid, by=addr, reason=reason)
        if isinstance(r, dict):
            r['takedown'] = True
    return r


@app.get('/takedowns')
def takedowns(authorization: Optional[str] = Header(default=None)):
    """Owner-only moderation audit log of admin content removals."""
    require_owner(authorization)
    entries = read_takedowns()
    return {'count': len(entries), 'takedowns': entries}


# ── grants (timed access) ──────────────────────────────────────────

class GrantBody(BaseModel):
    grantee: str
    cid: Optional[str] = None            # None/'*' ⇒ all of my objects
    scope: str = 'read'                  # 'read' | 'write'
    ttl_seconds: Optional[int] = None    # relative expiry
    expires_at: Optional[int] = None     # absolute unix-secs expiry


@app.post('/grants')
def grant_create(body: GrantBody, authorization: Optional[str] = Header(default=None)):
    grantor = require_session(authorization)
    grantee = body.grantee.strip().lower()
    if not grantee.startswith('0x') or len(grantee) != 42:
        raise HTTPException(400, 'grantee must be a 0x address')
    # Granting a *specific* CID requires you own it; '*' shares your own set.
    if body.cid and body.cid != '*' and not (caller_owns(body.cid, grantor) or is_admin(grantor)):
        raise HTTPException(403, 'you can only grant access to objects you own')
    return ACCESS.create_grant(grantor, grantee, cid=body.cid, scope=body.scope,
                               ttl_seconds=body.ttl_seconds, expires_at=body.expires_at)


@app.get('/grants')
def grant_list(authorization: Optional[str] = Header(default=None)):
    addr = require_session(authorization)
    ACCESS.prune()
    return {'address': addr, 'granted': ACCESS.grants_by(addr),
            'received': ACCESS.grants_to(addr, active_only=False)}


@app.delete('/grants/{gid}')
def grant_revoke(gid: str, authorization: Optional[str] = Header(default=None)):
    addr = require_session(authorization)
    ok = ACCESS.revoke_grant(gid, grantor=None if is_admin(addr) else addr)
    if not ok:
        raise HTTPException(404, 'grant not found or not yours')
    return {'revoked': gid}


# ── data pools (mutual access) ─────────────────────────────────────

class PoolBody(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None


@app.post('/pools')
def pool_create(body: PoolBody, authorization: Optional[str] = Header(default=None)):
    owner = require_authorized(authorization)
    return ACCESS.create_pool(owner, name=body.name, description=body.description)


@app.get('/pools')
def pool_list(authorization: Optional[str] = Header(default=None)):
    addr = require_session(authorization)
    return {'address': addr, 'pools': ACCESS.list_pools_for(addr)}


def _require_pool_member(pid: str, addr: str) -> dict:
    pool = ACCESS.get_pool(pid)
    if not pool:
        raise HTTPException(404, 'pool not found')
    if not ACCESS.is_member(pid, addr) and not is_admin(addr):
        raise HTTPException(403, 'not a member of this pool')
    return pool


@app.get('/pools/{pid}')
def pool_get(pid: str, authorization: Optional[str] = Header(default=None)):
    addr = require_session(authorization)
    pool = _require_pool_member(pid, addr)
    # Resolve pooled objects against the index for size/owner display.
    index = {o.get('cid'): o for o in store_mod.list(limit=100000)}
    for o in pool['objects']:
        meta = index.get(o['cid'], {})
        o['size'] = meta.get('size')
        o['owner'] = meta.get('owner')
    pool['role'] = ACCESS.member_role(pid, addr)
    return pool


@app.delete('/pools/{pid}')
def pool_delete(pid: str, authorization: Optional[str] = Header(default=None)):
    """Delete a pool entirely (members + objects). Pool owner (or admin) only."""
    addr = require_session(authorization)
    pool = ACCESS.get_pool(pid)
    if not pool:
        raise HTTPException(404, 'pool not found')
    if pool['owner'] != addr and not is_admin(addr):
        raise HTTPException(403, 'only the pool owner may delete it')
    ACCESS.delete_pool(pid)
    return {'deleted': pid}


class MemberBody(BaseModel):
    address: str
    role: str = 'viewer'                 # 'owner' | 'editor' | 'viewer'
    ttl_seconds: Optional[int] = None
    expires_at: Optional[int] = None


@app.post('/pools/{pid}/members')
def pool_add_member(pid: str, body: MemberBody, authorization: Optional[str] = Header(default=None)):
    addr = require_session(authorization)
    pool = ACCESS.get_pool(pid)
    if not pool:
        raise HTTPException(404, 'pool not found')
    if ACCESS.member_role(pid, addr) not in ('owner', 'editor') and not is_admin(addr):
        raise HTTPException(403, 'owner/editor only')
    member = body.address.strip().lower()
    if not member.startswith('0x') or len(member) != 42:
        raise HTTPException(400, 'address must be a 0x address')
    # Only the pool owner may mint other owners/editors.
    role = body.role if body.role in ('owner', 'editor', 'viewer') else 'viewer'
    if role in ('owner', 'editor') and pool['owner'] != addr and not is_admin(addr):
        raise HTTPException(403, 'only the pool owner may add owners/editors')
    return ACCESS.add_member(pid, member, role=role,
                             ttl_seconds=body.ttl_seconds, expires_at=body.expires_at)


@app.delete('/pools/{pid}/members')
def pool_remove_member(pid: str, address: str, authorization: Optional[str] = Header(default=None)):
    addr = require_session(authorization)
    pool = ACCESS.get_pool(pid)
    if not pool:
        raise HTTPException(404, 'pool not found')
    target = address.strip().lower()
    # Pool owner can remove anyone; a member may remove themselves (leave).
    if pool['owner'] != addr and target != addr.lower() and not is_admin(addr):
        raise HTTPException(403, 'owner only (or remove yourself)')
    if target == pool['owner']:
        raise HTTPException(400, 'cannot remove the pool owner')
    ACCESS.remove_member(pid, target)
    return {'pool': pid, 'removed': target}


class PoolObjectBody(BaseModel):
    cid: str
    backend: Optional[str] = None
    scheme: Optional[str] = None
    key: Optional[str] = None


@app.post('/pools/{pid}/objects')
def pool_add_object(pid: str, body: PoolObjectBody, authorization: Optional[str] = Header(default=None)):
    addr = require_session(authorization)
    pool = ACCESS.get_pool(pid)
    if not pool:
        raise HTTPException(404, 'pool not found')
    if ACCESS.member_role(pid, addr) not in ('owner', 'editor') and not is_admin(addr):
        raise HTTPException(403, 'owner/editor only')
    # You may only pool an object you can already read (own / granted / public).
    if not (ACCESS.can_read(addr, body.cid) or is_admin(addr)):
        raise HTTPException(403, 'you do not have access to that object')
    return ACCESS.add_object(pid, body.cid, backend=body.backend,
                             scheme=body.scheme, key=body.key, added_by=addr)


@app.delete('/pools/{pid}/objects')
def pool_remove_object(pid: str, cid: str, authorization: Optional[str] = Header(default=None)):
    addr = require_session(authorization)
    pool = ACCESS.get_pool(pid)
    if not pool:
        raise HTTPException(404, 'pool not found')
    if ACCESS.member_role(pid, addr) not in ('owner', 'editor') and not is_admin(addr):
        raise HTTPException(403, 'owner/editor only')
    ACCESS.remove_object(pid, cid)
    return {'pool': pid, 'removed': cid}


# ── QR auth handoff (computer ↔ phone) ─────────────────────────────

class HandoffBody(BaseModel):
    ttl_seconds: int = 120


@app.post('/handoff')
def handoff_create(body: HandoffBody, authorization: Optional[str] = Header(default=None)):
    """Mint a one-time code that carries *this* session's token to another
    device. The desktop shows it as a QR; the phone scans, claims, and is signed
    in — no MetaMask needed on the phone. Code is single-use and short-lived."""
    addr = require_session(authorization)
    token = authorization[7:].strip()
    ttl = max(30, min(600, int(body.ttl_seconds)))
    h = ACCESS.create_handoff(token, address=addr, ttl_seconds=ttl)
    return {**h, 'address': addr}


@app.get('/handoff/{code}')
def handoff_claim(code: str):
    """Redeem a handoff code → the original bearer token (once)."""
    r = ACCESS.claim_handoff(code)
    if not r:
        raise HTTPException(404, 'invalid, expired, or already-claimed code')
    return r


# ── one-time access tickets (single-use, anti-replay) ──────────────

class TicketBody(BaseModel):
    cid: str
    backend: Optional[str] = None
    ttl_seconds: int = 10        # short by design; default 10s


@app.post('/tickets')
def ticket_create(body: TicketBody, authorization: Optional[str] = Header(default=None)):
    """Mint a single-use, short-TTL ticket that grants exactly one fetch of a CID
    — even a private one. The link/QR works once and only within the window, so a
    captured ticket cannot be replayed. Caller must already be able to read it."""
    addr = require_session(authorization)
    if not (ACCESS.can_read(addr, body.cid) or is_admin(addr)):
        raise HTTPException(403, 'you do not have access to that object')
    ttl = max(1, min(3600, int(body.ttl_seconds or 10)))
    return ACCESS.create_ticket(body.cid, backend=body.backend, issuer=addr, ttl_seconds=ttl)


@app.get('/tickets')
def ticket_list(authorization: Optional[str] = Header(default=None)):
    addr = require_session(authorization)
    ACCESS.prune()
    return {'address': addr, 'tickets': ACCESS.tickets_by(addr, active_only=True)}


@app.get('/ticket/{code}')
def ticket_claim(code: str):
    """Redeem a one-time access ticket → serve the object, exactly once."""
    r = ACCESS.claim_ticket(code)
    if not r:
        raise HTTPException(404, 'invalid, expired, or already-used ticket')
    cid, backend = r['cid'], r['backend']
    acl = ACCESS.get_acl(cid)
    if acl and acl.get('url'):
        return RedirectResponse(acl['url'])
    out = Path(os.path.expanduser(f'~/.store-mod/cache/{cid}'))
    out.parent.mkdir(parents=True, exist_ok=True)
    got = store_mod.get(cid=cid, backend=backend, out=str(out))
    if 'error' in got:
        raise HTTPException(404, got['error'])
    return FileResponse(out, filename=cid)


# ── marketplace ────────────────────────────────────────────────────

def _market_annotate(listings: list, caller: Optional[str]) -> list:
    """Attach object metadata (size/key/scheme/visibility) + caller-relative
    flags (liked/owned/acquired/can_read) to raw listings."""
    index = {o.get('cid'): o for o in store_mod.list(limit=100000)}
    acquired = MARKET.acquired_by(caller) if caller else {}
    for l in listings:
        cid = l['cid']
        meta = index.get(cid, {})
        acl = ACCESS.get_acl(cid) or {}
        l['key'] = meta.get('key')
        l['size'] = meta.get('size')
        l['scheme'] = acl.get('scheme') or infer_scheme(cid)
        l['visibility'] = acl.get('visibility', 'public')
        l['external_url'] = acl.get('url')
        l['liked'] = MARKET.liked(cid, caller) if caller else False
        l['owned'] = bool(caller and l['seller'] == caller)
        l['acquired'] = cid in acquired
        l['can_read'] = ACCESS.can_read(caller, cid)
    return listings


@app.get('/market')
def market_browse(
    q: str = '',
    tag: str = '',
    seller: str = '',
    sort: str = 'hot',            # 'hot' | 'new' | 'top'
    free: Optional[str] = None,   # truthy ⇒ free listings only
    limit: int = 100,
    authorization: Optional[str] = Header(default=None),
):
    """Public storefront — no auth needed to window-shop. Signed-in callers
    additionally see liked/owned/acquired/can_read flags per listing."""
    caller = optional_session(authorization)
    listings = MARKET.browse(q=q, tag=tag, seller=seller, sort=sort,
                             free_only=_truthy(free) if free is not None else False,
                             limit=limit)
    return {'count': len(listings), 'sort': sort, 'tags': MARKET.tag_counts(),
            'listings': _market_annotate(listings, caller)}


class MarketListBody(BaseModel):
    cid: str
    title: str
    description: str = ''
    tags: list = []
    price_bloc: float = 0.0       # 0 = free; >0 = buyer must HOLD this much BlocTime


@app.post('/market/list')
def market_list(body: MarketListBody, authorization: Optional[str] = Header(default=None)):
    """List (or re-list with new metadata) an object you own on the market.
    The object keeps its own visibility: listing a PRIVATE object sells access
    (acquire mints a read grant); a public one is a discoverable free drop."""
    addr = require_terms(require_authorized(authorization))
    cid = body.cid.strip()
    if not cid:
        raise HTTPException(400, 'cid required')
    if not (caller_owns(cid, addr) or is_admin(addr)):
        raise HTTPException(403, 'you can only list objects you own')
    existing = MARKET.get(cid)
    if existing and existing['seller'] != addr and not is_admin(addr):
        raise HTTPException(403, 'already listed by someone else')
    try:
        row = MARKET.upsert(cid, seller=addr, title=body.title,
                            description=body.description, tags=body.tags,
                            price_bloc=body.price_bloc)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _market_annotate([row], addr)[0]


@app.delete('/market/list')
def market_delist(cid: str, reason: Optional[str] = None,
                  authorization: Optional[str] = Header(default=None)):
    """Remove a listing. Sellers delist their own; the admin may delist ANY
    listing (moderation) — logged to the takedown audit. Bytes stay stored."""
    addr = require_session(authorization)
    row = MARKET.get(cid)
    if not row:
        raise HTTPException(404, 'not listed')
    own = row['seller'] == addr
    if not own and not is_admin(addr):
        raise HTTPException(403, 'not your listing')
    MARKET.delist(cid)
    if not own:
        log_takedown(cid, by=addr, reason=reason or 'market delisting')
    return {'cid': cid, 'delisted': True, 'takedown': not own}


class MarketAcquireBody(BaseModel):
    cid: str


@app.post('/market/acquire')
def market_acquire(body: MarketAcquireBody, authorization: Optional[str] = Header(default=None)):
    """Get a listed item. Free ⇒ instant. Priced ⇒ the caller must HOLD at
    least price_bloc BlocTime on-chain (holdings ARE the ticket — no custody).
    Access lands as a permanent read grant, so the item appears under /shared
    and every read path (get/preview/QR tickets) just works."""
    addr = require_session(authorization)
    cid = body.cid.strip()
    row = MARKET.get(cid)
    if not row:
        raise HTTPException(404, 'not listed on the market')
    if row['seller'] == addr:
        return {'cid': cid, 'acquired': True, 'note': 'you are the seller'}
    price = row['price_bloc']
    if price > 0:
        balance = float(ONCHAIN.bloctime_balance(addr) or 0)
        if balance < price:
            raise HTTPException(
                402,
                f'this item needs {price} BlocTime held on-chain — '
                f'you hold {balance}. Stake BlocTime to unlock it.',
            )
    # Idempotent: skip the grant if one from the seller already covers it.
    if not ACCESS.can_read(addr, cid):
        ACCESS.create_grant(row['seller'], addr, cid=cid, scope='read')
    MARKET.record_acquire(cid, addr)
    fresh = MARKET.get(cid)
    return {'cid': cid, 'acquired': True, 'price_bloc': price,
            'downloads': fresh['downloads'] if fresh else row['downloads']}


class MarketLikeBody(BaseModel):
    cid: str


@app.post('/market/like')
def market_like(body: MarketLikeBody, authorization: Optional[str] = Header(default=None)):
    """Toggle a like on a listing (one per address — it's wallet-signed)."""
    addr = require_session(authorization)
    r = MARKET.toggle_like(body.cid.strip(), addr)
    if not r:
        raise HTTPException(404, 'not listed on the market')
    return r


@app.get('/market/mine')
def market_mine(authorization: Optional[str] = Header(default=None)):
    """The caller's market activity: their listings + what they've acquired."""
    addr = require_session(authorization)
    listings = _market_annotate(MARKET.listings_by(addr), addr)
    acquired_ts = MARKET.acquired_by(addr)
    acquired = [{**row, 'acquired_at': ts}
                for cid, ts in sorted(acquired_ts.items(), key=lambda kv: kv[1], reverse=True)
                if (row := MARKET.get(cid))]
    return {'address': addr, 'listings': listings,
            'acquired': _market_annotate(acquired, addr)}


# ── on-chain (BlocTime + Registry) ─────────────────────────────────

@app.get('/onchain')
def onchain_status():
    """Discovery: network, BlocTime gate, and Registry registration state."""
    return ONCHAIN.status()


@app.get('/onchain/bloctime')
def onchain_bloctime(authorization: Optional[str] = Header(default=None)):
    """Caller's on-chain BlocTime balance + holder flag."""
    addr = require_session(authorization)
    return {'address': addr, 'holder': ONCHAIN.is_bloctime_holder(addr),
            'balance': ONCHAIN.bloctime_balance(addr), 'network': ONCHAIN.network}


class OnchainRegisterBody(BaseModel):
    data: Optional[str] = None       # defaults to the config schema CID
    force: bool = False


@app.post('/onchain/register')
def onchain_register(body: OnchainRegisterBody, authorization: Optional[str] = Header(default=None)):
    """Register the store in the chain Registry. Owner only (spends gas)."""
    require_owner(authorization)
    data = body.data or CONFIG.get('schema') or CONFIG.get('urls', {}).get('app') or 'store'
    try:
        return ONCHAIN.register(data=data, force=body.force)
    except Exception as e:
        raise HTTPException(502, f'on-chain registration failed: {e}')


@app.on_event('startup')
def _maybe_register_onchain():
    """Auto-register on boot when config onchain_registry is true (off by default
    so the service never spends gas unprompted)."""
    if not CONFIG.get('onchain_registry'):
        return
    try:
        data = CONFIG.get('schema') or CONFIG.get('urls', {}).get('app') or 'store'
        ONCHAIN.register(data=data)
    except Exception:
        pass  # never let chain/RPC issues block startup


@app.get('/')
def root():
    return {
        'name': 'store',
        'description': CONFIG.get('description'),
        'app': CONFIG.get('urls', {}).get('app'),
        'auth': 'mod-protocol',
        'endpoints': sorted(CONFIG.get('endpoints', {}).keys()),
    }


# MCP (Model Context Protocol) over Streamable HTTP: POST /mcp exposes the
# operations above as tools for LLM agents. Imported last so the router can
# wrap every endpoint function already defined in this module.
from api.mcp import router as mcp_router  # noqa: E402
app.include_router(mcp_router)
