"""
mcp api — the MCP hub over HTTP.

A thin skin over mod.py: every endpoint is one call into the Mod class, so the
Python fn surface, the REST API and the hub's own MCP tool server can never
drift apart. The gateway routes /api/mcp → this service and /mcp → the app.

Auth
    Publishing is gated by the mod **protocol auth system**: the client signs a
    `{data, time}` envelope with its wallet — a browser extension or a keypair
    the browser minted locally, the server can't tell them apart — and sends it
    as `Authorization: Bearer <token>`. Reading is open to everyone.

Publishing
    A submitted manifest is pinned to the **store** mod with the publisher's
    own token, so the canonical artifact is a CID *they* own. store requires a
    signed terms-of-service acceptance first; /store/terms proxies that so the
    whole flow fits in one page.

Endpoints
    GET  /health
    GET  /info                     module identity + wired providers
    GET  /sources                  provider catalog (what each one indexes)
    GET  /search                   merged, ranked search across every provider
    GET  /server?id=               one merged server card (+ last probe)
    POST /probe                    live MCP handshake → real tool list
    GET  /client_config?id=        paste-ready client config / `claude mcp add`
    GET  /stats                    hub totals
    GET  /store/terms              store's publisher terms (+accepted if auth)
    POST /store/terms/accept       sign-accept them (auth)
    GET  /submissions              community-published servers (?mine=1 w/ auth)
    POST /submit                   publish a server (auth)
    POST /submissions/repin        retry a failed manifest pin (auth)
    DELETE /submissions?id=        delist your own (owner may delist any)
    POST /mcp                      the hub as an MCP server (see api/mcp.py)

Run (pm2 — see ecosystem.config.js):
    uvicorn api.api:app --host 0.0.0.0 --port 50360
"""
import importlib.util
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

MODULE_DIR = Path(__file__).resolve().parent.parent


def _load_mod():
    """Load the module's mod.py by file path.

    Not `import mod`: this module's directory is on sys.path under uvicorn and
    its mod.py would shadow the top-level `mod` package. mod.py itself knows
    how to un-shadow it (mod_pkg) once loaded under a private name.
    """
    spec = importlib.util.spec_from_file_location('_mcphub_mod', MODULE_DIR / 'mod.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules['_mcphub_mod'] = module
    spec.loader.exec_module(module)
    return module


mod_impl = _load_mod()
HUB = mod_impl.Mod()
CONFIG = HUB.config
SubmitError = mod_impl.SubmitError
StoreError = mod_impl.index_mod.StoreError

app = FastAPI(title='mcp — a hub of MCP servers',
              version=str(CONFIG.get('version') or '1.0.0'),
              description=str(CONFIG.get('description') or ''))
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_credentials=True,
                   allow_methods=['*'], allow_headers=['*'])


# ── auth ────────────────────────────────────────────────────────────

def require_session(authorization: Optional[str]) -> str:
    """Verify a protocol-auth bearer token → lowercase signer address."""
    if not authorization or not authorization.startswith('Bearer '):
        raise HTTPException(401, 'missing bearer token — sign in with your wallet')
    try:
        return HUB.verify(authorization[7:].strip())
    except Exception as e:
        raise HTTPException(401, f'invalid or expired token: {e}')


def optional_session(authorization: Optional[str]) -> Optional[str]:
    if not authorization or not authorization.startswith('Bearer '):
        return None
    try:
        return HUB.verify(authorization[7:].strip())
    except Exception:
        return None


def bearer(authorization: Optional[str]) -> Optional[str]:
    return authorization[7:].strip() if authorization and \
        authorization.startswith('Bearer ') else None


# ── read ────────────────────────────────────────────────────────────

@app.get('/health')
def health():
    return {'ok': True, 'service': 'mcp', 'version': CONFIG.get('version')}


@app.get('/info')
def info():
    return HUB.info()


@app.get('/sources')
def sources():
    return {'sources': HUB.sources()}


@app.get('/search')
def search(q: str = '', sources: str = '', limit: int = Query(40, ge=1, le=200),
           oss: bool = True, transport: str = '', license: str = '',
           tag: str = '', category: str = '', sort: str = 'relevance'):
    """Every provider at once, merged and ranked. `oss=false` widens the search
    to servers with no public source."""
    try:
        return HUB.search(q=q, sources=sources or None, limit=limit, oss=oss,
                          transport=transport, license=license, tag=tag,
                          category=category, sort=sort)
    except Exception as e:
        raise HTTPException(502, f'search failed: {type(e).__name__}: {e}')


@app.get('/server')
def server(id: str):
    try:
        return HUB.server(id)
    except KeyError as e:
        raise HTTPException(404, str(e))


class ProbeBody(BaseModel):
    url: Optional[str] = None
    id: Optional[str] = None
    token: Optional[str] = None     # auth for the *probed* server, not the hub
    refresh: bool = False


@app.post('/probe')
def probe(body: ProbeBody):
    """Speak MCP to a live server and report its real tools."""
    try:
        return HUB.probe(url=body.url, id=body.id, token=body.token,
                         refresh=body.refresh)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get('/client_config')
def client_config(id: str, client: str = 'claude'):
    try:
        return HUB.client_config(id, client=client)
    except KeyError as e:
        raise HTTPException(404, str(e))


@app.get('/stats')
def stats():
    return HUB.stats()


# ── store terms (publishing prerequisite) ───────────────────────────

@app.get('/store/terms')
def store_terms(authorization: Optional[str] = Header(None)):
    try:
        return HUB.index.terms(bearer(authorization))
    except StoreError as e:
        raise HTTPException(502, str(e))


@app.post('/store/terms/accept')
def store_terms_accept(authorization: Optional[str] = Header(None)):
    require_session(authorization)
    try:
        return HUB.index.accept_terms(bearer(authorization))
    except StoreError as e:
        raise HTTPException(502, str(e))


# ── publish ─────────────────────────────────────────────────────────

class SubmitBody(BaseModel):
    name: str
    description: str
    slug: Optional[str] = None
    title: Optional[str] = None
    repo: Optional[str] = None
    homepage: Optional[str] = None
    license: Optional[str] = None
    version: Optional[str] = None
    tags: List[str] = []
    transports: List[str] = []
    remote_url: Optional[str] = None
    remotes: List[Dict[str, Any]] = []
    packages: List[Dict[str, Any]] = []
    npm: Optional[str] = None
    pypi: Optional[str] = None
    install: Dict[str, Any] = {}


@app.post('/submit')
def submit(body: SubmitBody, authorization: Optional[str] = Header(None)):
    """Publish an MCP server. The manifest is pinned to store as the caller's
    own object; the hub records the CID."""
    address = require_session(authorization)
    try:
        return HUB.index.submit(address, body.model_dump(exclude_none=False),
                                bearer(authorization))
    except SubmitError as e:
        raise HTTPException(400, str(e))


class RepinBody(BaseModel):
    id: str


@app.post('/submissions/repin')
def repin(body: RepinBody, authorization: Optional[str] = Header(None)):
    address = require_session(authorization)
    try:
        return HUB.index.repin(body.id, address, bearer(authorization))
    except KeyError:
        raise HTTPException(404, f'no such submission: {body.id}')
    except PermissionError as e:
        raise HTTPException(403, str(e))


@app.get('/submissions')
def submissions(mine: bool = False, authorization: Optional[str] = Header(None)):
    address = optional_session(authorization)
    if mine and not address:
        raise HTTPException(401, 'sign in to list your own submissions')
    items = HUB.index.list(author=address if mine else None)
    return {'count': len(items), 'address': address, 'servers': items}


@app.delete('/submissions')
def delist(id: str, authorization: Optional[str] = Header(None)):
    address = require_session(authorization)
    try:
        return HUB.index.remove(id, address, admin=(address == HUB.owner()))
    except KeyError:
        raise HTTPException(404, f'no such submission: {id}')
    except PermissionError as e:
        raise HTTPException(403, str(e))


# The hub is itself an MCP server. Imported last so every name above exists.
from api import mcp as mcp_router  # noqa: E402

app.include_router(mcp_router.router)
