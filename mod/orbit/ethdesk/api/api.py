"""
eth api — Ethereum operations and contract deployment over HTTP.

Every route is a thin call into the engine (`ops.py`, `wallet.py`,
`compiler.py`, `ledger.py`, `chains.py`). Nothing chain-shaped is implemented
here, so the console, the CLI and the MCP tools cannot drift from each other —
there is one implementation and three doors.

The split that matters is not REST-shaped, it is money-shaped:

    open        reads. Balances, blocks, transactions, gas, event logs, ERC-20
                metadata, view calls, and compiling Solidity. No token, no
                account, nothing to lose. A console renders its whole first
                screen before anyone signs in.

    signed      anything that touches a key or your private index: accounts,
                deploys, sends, contract writes, history. The caller is the
                address that signed the mod-protocol token, and accounts are
                namespaced by it — two people on one deployment are two vaults.

    confirmed   a write on a chain where `testnet` is false. It needs
                `confirm: true` in the body on top of everything else. This is
                enforced in ops.py rather than here, so the API, the CLI and an
                agent's MCP call all hit the same wall.

Run:
    uvicorn api.api:app --host 0.0.0.0 --port 50750
    python3 api/api.py --port 50750
"""
import importlib.util
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Body, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import builder  # noqa: E402
import catalog  # noqa: E402
import chains  # noqa: E402
import compiler  # noqa: E402
import harness  # noqa: E402
import identity  # noqa: E402
import ledger  # noqa: E402
import mcp as mcp_server  # noqa: E402  — this module's mcp.py; ROOT leads sys.path
import ops  # noqa: E402
import projects  # noqa: E402
import wallet  # noqa: E402
from store_link import LINK, StoreError  # noqa: E402

CONFIG = json.loads((ROOT / 'config.json').read_text())
VERSION = CONFIG.get('version', '1.0.0')
BASE = CONFIG.get('base_path', '/ethdesk')

app = FastAPI(title='ethdesk', version=VERSION,
              description=CONFIG.get('description', 'Ethereum operations'))
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_methods=['*'],
                   allow_headers=['*'], allow_credentials=False)


@app.middleware('http')
async def api_alias(request: Request, call_next):
    """`/ethdesk/_api/*` is this same API, one path segment along.

    Behind the gateway the API answers on /api/eth and the console on /eth; on
    a bare port both are here. The page always asks its *own origin* for
    `_api`, so one build works in both places with no CORS preflight on the hot
    path, no API url baked into the console, and no token crossing an origin.
    """
    path = request.scope.get('path', '')
    request.scope['eth_path'] = path      # the rewrite below destroys it
    prefix = f'{BASE}/_api'
    if path.startswith(prefix):
        request.scope['path'] = path[len(prefix):] or '/'
    return await call_next(request)


@app.exception_handler(ops.OpError)
async def op_error(request: Request, exc: ops.OpError):
    # 400, not 500: these are all "the request cannot be carried out as asked",
    # and the message is written to be shown to a person or read by a model.
    return JSONResponse(status_code=400, content={'detail': str(exc)})


@app.exception_handler(wallet.WalletError)
async def wallet_error(request: Request, exc: wallet.WalletError):
    return JSONResponse(status_code=400, content={'detail': str(exc)})


@app.exception_handler(chains.ChainError)
async def chain_error(request: Request, exc: chains.ChainError):
    return JSONResponse(status_code=404, content={'detail': str(exc)})


@app.exception_handler(projects.ProjectError)
async def project_error(request: Request, exc: projects.ProjectError):
    return JSONResponse(status_code=400, content={'detail': str(exc)})


@app.exception_handler(harness.TestError)
async def test_error(request: Request, exc: harness.TestError):
    return JSONResponse(status_code=400, content={'detail': str(exc)})


@app.exception_handler(builder.BuildError)
async def build_error(request: Request, exc: builder.BuildError):
    return JSONResponse(status_code=exc.status if exc.status >= 400 else 502,
                        content={'detail': exc.message})


@app.exception_handler(StoreError)
async def store_error(request: Request, exc: StoreError):
    # The store's own status code, kept: a 403 there is a 403 here, and the
    # console tells the caller which module said no.
    return JSONResponse(status_code=exc.status if 400 <= exc.status < 600 else 502,
                        content={'detail': exc.message, 'module': 'store'})


@app.exception_handler(compiler.CompileError)
async def compile_error(request: Request, exc: compiler.CompileError):
    return JSONResponse(status_code=422,
                        content={'detail': str(exc), 'errors': exc.errors})


# ── callers ──────────────────────────────────────────────────────────

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


# ── bodies ───────────────────────────────────────────────────────────

class NewAccount(BaseModel):
    name: str
    password: str
    mnemonic: bool = False
    secret: Optional[str] = None       # present = import rather than create


class Unlock(BaseModel):
    password: str
    ttl: int = 300


class Secret(BaseModel):
    password: str
    confirm: bool = False


class SignBody(BaseModel):
    account: str
    message: str
    password: Optional[str] = None


class VerifyBody(BaseModel):
    message: str
    signature: str


class SendBody(BaseModel):
    account: str
    to: str
    value: Any = 0
    network: Optional[str] = None
    password: Optional[str] = None
    data: Optional[str] = None
    gas: Optional[int] = None
    gas_price: Optional[Any] = None
    max_fee: Optional[Any] = None
    priority_fee: Optional[Any] = None
    confirm: bool = False
    wait: bool = True


class RawBody(BaseModel):
    signed: str
    network: Optional[str] = None
    confirm: bool = False


class EstimateBody(BaseModel):
    to: Optional[str] = None
    data: Optional[str] = None
    value: Any = 0
    sender: Optional[str] = None
    network: Optional[str] = None


class CallBody(BaseModel):
    to: str
    data: str
    sender: Optional[str] = None
    network: Optional[str] = None


class CompileBody(BaseModel):
    source: Optional[str] = None
    sources: Optional[Dict[str, str]] = None
    template: Optional[str] = None
    filename: str = 'Contract.sol'
    solc: Optional[str] = None
    optimize: bool = True
    runs: int = 200
    evm_version: Optional[str] = None
    libraries: Optional[Dict[str, str]] = None
    full: bool = False                 # keep the bytecode in the response


class DeployBody(BaseModel):
    account: str
    template: Optional[str] = None
    source: Optional[str] = None
    sources: Optional[Dict[str, str]] = None
    contract: Optional[str] = None
    abi: Optional[Any] = None
    bytecode: Optional[str] = None
    args: List[Any] = []
    value: Any = 0
    network: Optional[str] = None
    password: Optional[str] = None
    solc: Optional[str] = None
    optimize: bool = True
    runs: int = 200
    name: Optional[str] = None
    note: Optional[str] = None
    gas: Optional[int] = None
    confirm: bool = False
    wait: bool = True


class AttachBody(BaseModel):
    address: str
    abi: Any
    network: Optional[str] = None
    name: Optional[str] = None


class ReadBody(BaseModel):
    function: str
    args: List[Any] = []
    network: Optional[str] = None
    abi: Optional[Any] = None
    block: Optional[Any] = None


class WriteBody(BaseModel):
    account: str
    function: str
    args: List[Any] = []
    value: Any = 0
    network: Optional[str] = None
    password: Optional[str] = None
    abi: Optional[Any] = None
    gas: Optional[int] = None
    confirm: bool = False
    wait: bool = True


class TokenMove(BaseModel):
    account: str
    to: Optional[str] = None
    spender: Optional[str] = None
    amount: Any
    network: Optional[str] = None
    password: Optional[str] = None
    confirm: bool = False


class NetworkBody(BaseModel):
    name: str
    rpc: str
    chain_id: Optional[int] = None
    testnet: bool = True
    explorer: Optional[str] = None
    currency: str = 'ETH'
    label: Optional[str] = None


# ── open: the shape of the deployment ────────────────────────────────

@app.get('/health')
def health():
    return {'ok': True, 'service': 'ethdesk', 'time': int(time.time())}


@app.get('/status')
def status(network: Optional[str] = None,
           authorization: Optional[str] = Header(default=None)):
    """Everything a console needs before the visitor does anything."""
    who = caller(authorization, required=False)
    out: Dict[str, Any] = {
        'service': 'ethdesk',
        'version': VERSION,
        'base_path': BASE,
        'network': chains.reachable(network),
        'networks': chains.summary(),
        'solc': compiler.status(),
        'templates': catalog.listing(),
        'auth': identity.status(),
        'address': who,
        'mcp': {'endpoint': 'POST /mcp', 'schema': 'GET /mcp',
                'transport': 'Streamable HTTP (JSON-RPC 2.0)',
                'stdio': 'python3 mcp.py', 'tools': len(mcp_server.TOOLS)},
        'safety': CONFIG.get('safety'),
    }
    if who:
        out['accounts'] = wallet.listing(who)
        out['index'] = ledger.counts(who)
        out['projects'] = projects.counts(who)
        out['is_owner'] = identity.is_owner(who)
    # Storage is the store module, so its reachability is part of this
    # module's state — a console that only finds out when a save fails is a
    # console that loses somebody's work to find out. Reported without waking
    # it: this is the console's first call, and /projects (the one that is
    # about to upload something) does the waking.
    out['store'] = LINK.status(_token(authorization), wake=False)
    return out


@app.get('/me')
def me(authorization: Optional[str] = Header(default=None)):
    who = caller(authorization)
    return {'address': who, 'is_owner': identity.is_owner(who),
            'owner': identity.owner(), 'accounts': wallet.listing(who),
            'unlocked': wallet.sessions(who), 'index': ledger.counts(who),
            'projects': projects.counts(who),
            'store': LINK.status(_token(authorization))}


@app.get('/endpoints')
def endpoints():
    return {'base_path': BASE, 'endpoints': CONFIG.get('endpoints', {})}


# ── networks ─────────────────────────────────────────────────────────

@app.get('/networks')
def networks(check: bool = False):
    if check:
        return {'networks': [chains.reachable(n['name']) for n in chains.summary()]}
    return {'networks': chains.summary()}


@app.get('/networks/{name}')
def network(name: str):
    return chains.reachable(name)


@app.post('/networks')
def add_network(body: NetworkBody,
                authorization: Optional[str] = Header(default=None)):
    """Teach this deployment about another chain. Owner only — an rpc url is
    where everyone's transactions go, so it is not a per-caller setting."""
    owner_caller(authorization)
    return chains.add(body.name, body.rpc, body.chain_id, body.testnet,
                      body.explorer, body.currency, body.label)


@app.delete('/networks/{name}')
def remove_network(name: str, authorization: Optional[str] = Header(default=None)):
    owner_caller(authorization)
    return {'removed': chains.remove(name), 'name': name}


# ── accounts ─────────────────────────────────────────────────────────

@app.get('/accounts')
def accounts(authorization: Optional[str] = Header(default=None)):
    who = caller(authorization)
    return {'owner': who, 'accounts': wallet.listing(who),
            'unlocked': wallet.sessions(who)}


@app.post('/accounts')
def new_account(body: NewAccount,
                authorization: Optional[str] = Header(default=None)):
    """Create a key, or import one by passing `secret`."""
    who = caller(authorization)
    if body.secret:
        return wallet.import_key(who, body.name, body.password, body.secret)
    return wallet.create(who, body.name, body.password, mnemonic=body.mnemonic)


@app.delete('/accounts/{name}')
def delete_account(name: str, confirm: bool = False,
                   authorization: Optional[str] = Header(default=None)):
    who = caller(authorization)
    if not confirm:
        raise HTTPException(400, 'pass confirm=true — without a backup of the '
                                 'keystore or the mnemonic, this is final')
    return wallet.delete(who, name)


@app.post('/accounts/{name}/unlock')
def unlock(name: str, body: Unlock,
           authorization: Optional[str] = Header(default=None)):
    who = caller(authorization)
    return wallet.unlock(who, name, body.password, body.ttl)


@app.post('/accounts/{name}/lock')
def lock(name: str, authorization: Optional[str] = Header(default=None)):
    who = caller(authorization)
    return wallet.lock(who, name)


@app.post('/accounts/{name}/export')
def export_account(name: str, body: Secret,
                   authorization: Optional[str] = Header(default=None)):
    """The raw private key. Password and an explicit confirm, because this is
    the whole account in one string."""
    who = caller(authorization)
    if not body.confirm:
        raise HTTPException(400, 'pass confirm=true — this returns a key that '
                                 'owns funds, in plaintext')
    return wallet.export(who, name, body.password)


@app.post('/accounts/{name}/keystore')
def keystore(name: str, body: Secret,
             authorization: Optional[str] = Header(default=None)):
    """The encrypted keystore file — a backup that is safe to move around.

    The password is required not to decrypt it but to prove you can: handing
    back ciphertext to someone who cannot open it is a favour to nobody except
    an attacker with a long time horizon.
    """
    who = caller(authorization)
    wallet.signer(who, name, body.password)
    return {'name': name, 'keystore': wallet.keystore(who, name),
            'note': 'keystore v3 — restore with POST /accounts {secret: <private key>} '
                    'or any wallet that reads this format'}


@app.post('/sign')
def sign(body: SignBody, authorization: Optional[str] = Header(default=None)):
    who = caller(authorization)
    return wallet.sign_message(who, body.account, body.message, body.password)


@app.post('/verify')
def verify(body: VerifyBody):
    return wallet.verify_message(body.message, body.signature)


# ── open: reading the chain ──────────────────────────────────────────

@app.get('/balance')
def balance(address: str, network: Optional[str] = None,
            token: Optional[str] = None,
            authorization: Optional[str] = Header(default=None)):
    return ops.balance(address, network, caller(authorization, required=False), token)


@app.get('/portfolio')
def portfolio(networks: Optional[str] = None, address: Optional[str] = None,
              authorization: Optional[str] = Header(default=None)):
    who = caller(authorization)
    names = [n.strip() for n in networks.split(',')] if networks else None
    return ops.portfolio(who, address, names)


@app.get('/block')
def block(number: str = 'latest', network: Optional[str] = None, full: bool = False):
    return ops.block(number, network, full)


@app.get('/tx')
def tx(hash: str, network: Optional[str] = None, wait: bool = False,
       timeout: int = 120):
    if wait:
        return ops.wait(hash, network, timeout)
    return ops.transaction(hash, network)


@app.get('/gas')
def gas(network: Optional[str] = None):
    return ops.fees(network)


@app.get('/nonce')
def nonce(address: str, network: Optional[str] = None,
          authorization: Optional[str] = Header(default=None)):
    return ops.nonce(address, network, caller(authorization, required=False))


@app.get('/code')
def code(address: str, network: Optional[str] = None,
         authorization: Optional[str] = Header(default=None)):
    return ops.code(address, network, caller(authorization, required=False))


@app.get('/storage')
def storage(address: str, slot: str = '0', network: Optional[str] = None,
            authorization: Optional[str] = Header(default=None)):
    return ops.storage_at(address, slot, network, caller(authorization, required=False))


@app.get('/logs')
def logs(network: Optional[str] = None, address: Optional[str] = None,
         from_block: str = 'latest', to_block: str = 'latest',
         topics: Optional[str] = None, limit: int = Query(default=200, le=1000),
         authorization: Optional[str] = Header(default=None)):
    parsed = json.loads(topics) if topics else None
    return ops.logs(network, address, from_block, to_block, parsed,
                    caller(authorization, required=False), limit)


@app.post('/estimate')
def estimate(body: EstimateBody,
             authorization: Optional[str] = Header(default=None)):
    return ops.estimate(body.to, body.data, body.value, body.network, body.sender,
                        caller(authorization, required=False))


@app.post('/call')
def call(body: CallBody, authorization: Optional[str] = Header(default=None)):
    return ops.call_raw(body.to, body.data, body.network, body.sender,
                        caller(authorization, required=False))


# ── signed: spending ─────────────────────────────────────────────────

@app.post('/send')
def send(body: SendBody, authorization: Optional[str] = Header(default=None)):
    who = caller(authorization)
    return ops.send(who, body.account, body.to, body.value, body.network,
                    body.password, data=body.data, gas=body.gas,
                    gas_price=body.gas_price, max_fee=body.max_fee,
                    priority_fee=body.priority_fee, confirm=body.confirm,
                    wait_for=body.wait)


@app.post('/send/raw')
def send_raw(body: RawBody, authorization: Optional[str] = Header(default=None)):
    who = caller(authorization)
    return ops.send_raw(body.signed, body.network, who, body.confirm)


# ── contracts ────────────────────────────────────────────────────────

@app.get('/templates')
def templates(compile: bool = False):
    return {'templates': catalog.listing(compile_it=compile)}


@app.get('/templates/{name}')
def template(name: str, source: bool = True):
    try:
        out = catalog.describe(name, compile_it=True)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    if source:
        out['source'] = catalog.source(name)
    return out


@app.post('/compile')
def compile_(body: CompileBody):
    """Solidity in, ABI and bytecode out. Nothing is sent anywhere.

    Open on purpose: compiling is a pure function of the source, costs this box
    a subprocess, and being able to check what a contract compiles to before
    trusting a deployment is the entire point of publishing source.
    """
    sources = body.sources
    if not sources:
        text = catalog.source(body.template) if body.template else body.source
        if not text:
            raise HTTPException(400, 'give me source, sources or template')
        name = f'{body.template}.sol' if body.template else body.filename
        sources = {name: text}
    out = compiler.compile_sources(sources, version=body.solc,
                                   optimize=body.optimize, runs=body.runs,
                                   evm_version=body.evm_version,
                                   libraries=body.libraries)
    if not body.full:
        for contract in out['contracts']:
            contract['bytecode_preview'] = contract['bytecode'][:66] + '…'
            contract.pop('bytecode', None)
            contract.pop('deployed_bytecode', None)
    return out


@app.post('/deploy')
def deploy(body: DeployBody, authorization: Optional[str] = Header(default=None)):
    """Compile and deploy in one step; the ABI is kept against the address."""
    who = caller(authorization)
    source = body.source
    name = body.name
    if body.template:
        source = catalog.source(body.template)
        name = name or catalog.describe(body.template)['contract']
    return ops.deploy(who, body.account, network=body.network, source=source,
                      sources=body.sources, contract=body.contract, abi=body.abi,
                      bytecode=body.bytecode, args=body.args, value=body.value,
                      password=body.password, solc=body.solc,
                      optimize=body.optimize, runs=body.runs, gas=body.gas,
                      name=name, confirm=body.confirm, note=body.note,
                      wait_for=body.wait)


@app.get('/contracts')
def contracts(network: Optional[str] = None,
              authorization: Optional[str] = Header(default=None)):
    who = caller(authorization)
    return {
        'deployed': [{k: v for k, v in row.items() if k not in ('bytecode', 'source')}
                     for row in ledger.deployments(who, network)],
        'attached': ledger.attached(who, network),
    }


@app.post('/contracts')
def attach(body: AttachBody, authorization: Optional[str] = Header(default=None)):
    who = caller(authorization)
    spec = chains.resolve(body.network)
    abi = json.loads(body.abi) if isinstance(body.abi, str) else body.abi
    row = ledger.attach(who, ops.to_checksum(body.address), abi, spec['name'],
                        spec.get('chain_id'), body.name)
    return row


@app.get('/contracts/{address}')
def contract(address: str, network: Optional[str] = None,
             authorization: Optional[str] = Header(default=None)):
    return ops.interface(address, network, None, caller(authorization, required=False))


@app.get('/contracts/{address}/source')
def contract_source(address: str,
                    authorization: Optional[str] = Header(default=None)):
    """The source and compiler settings this box recorded for a deployment.

    Scoped to the caller: the ABI of a contract is public, but the source you
    fed a compiler is yours until you publish it.
    """
    who = caller(authorization)
    for row in ledger.deployments(who, None, limit=1000):
        if row['address'].lower() == address.lower():
            return {'address': row['address'], 'name': row['name'],
                    'source': row.get('source'), 'source_name': row.get('source_name'),
                    'compiler': row.get('compiler'), 'abi': row.get('abi'),
                    'constructor_args': row.get('constructor_args')}
    raise HTTPException(404, f'no deployment of yours at {address}')


@app.delete('/contracts/{address}')
def forget(address: str, authorization: Optional[str] = Header(default=None)):
    """Drop the row. The contract stays deployed — nothing on chain changes."""
    who = caller(authorization)
    spec_removed = ledger.forget_deployment(who, address)
    detached = ledger.detach(who, address)
    return {'address': address, 'forgot_deployment': spec_removed,
            'detached_abi': detached,
            'note': 'the contract is still on chain; only this box forgot it'}


@app.post('/contracts/{address}/read')
def read(address: str, body: ReadBody,
         authorization: Optional[str] = Header(default=None)):
    return ops.read(address, body.function, body.args, body.network, body.abi,
                    caller(authorization, required=False), body.block)


@app.post('/contracts/{address}/write')
def write(address: str, body: WriteBody,
          authorization: Optional[str] = Header(default=None)):
    who = caller(authorization)
    return ops.write(who, body.account, address, body.function, body.args,
                     body.network, body.abi, body.value, body.password,
                     gas=body.gas, confirm=body.confirm, wait_for=body.wait)


# ── tokens ───────────────────────────────────────────────────────────

@app.get('/tokens/{address}')
def token(address: str, network: Optional[str] = None, holder: Optional[str] = None,
          authorization: Optional[str] = Header(default=None)):
    who = caller(authorization, required=False)
    out = ops.token_info(address, network, who)
    if holder:
        out['holder'] = ops.token_balance(address, holder, network, who)
    return out


@app.post('/tokens/{address}/transfer')
def token_transfer(address: str, body: TokenMove,
                   authorization: Optional[str] = Header(default=None)):
    who = caller(authorization)
    if not body.to:
        raise HTTPException(400, 'to is required')
    return ops.token_transfer(who, body.account, address, body.to, body.amount,
                              body.network, body.password, body.confirm)


@app.post('/tokens/{address}/approve')
def token_approve(address: str, body: TokenMove,
                  authorization: Optional[str] = Header(default=None)):
    who = caller(authorization)
    if not body.spender:
        raise HTTPException(400, 'spender is required')
    return ops.token_approve(who, body.account, address, body.spender, body.amount,
                             body.network, body.password, body.confirm)


# ── projects: write a contract, keep it, share it ─────────────────────
#
# The bytes live in the store module, addressed by CID. These routes are the
# index and the door; `projects.py` is the round trip.


class ProjectBody(BaseModel):
    name: Optional[str] = None
    files: Optional[Dict[str, str]] = None
    source: Optional[str] = None
    entry: Optional[str] = None
    tests: Optional[List[dict]] = None
    settings: Optional[dict] = None
    note: Optional[str] = None
    public: Optional[bool] = None


class ForkBody(BaseModel):
    cid: str
    name: Optional[str] = None


def _token(authorization: Optional[str]) -> Optional[str]:
    """The caller's own token, forwarded to the store verbatim.

    This module never mints one on somebody's behalf: the store's whitelist,
    quota and terms apply to the person asking, not to this box.
    """
    return identity.strip(authorization)


@app.get('/projects')
def list_projects(limit: int = Query(default=100, le=500),
                  authorization: Optional[str] = Header(default=None)):
    who = caller(authorization)
    return {'projects': projects.listing(who, limit),
            'counts': projects.counts(who),
            'store': LINK.status(_token(authorization))}


@app.post('/projects')
def create_project(body: ProjectBody,
                   authorization: Optional[str] = Header(default=None)):
    who = caller(authorization)
    return projects.save(who, _token(authorization), name=body.name,
                         files=body.files, source=body.source, entry=body.entry,
                         tests=body.tests, settings=body.settings,
                         note=body.note, public=body.public)


@app.get('/projects/{project}')
def get_project(project: str,
                authorization: Optional[str] = Header(default=None)):
    who = caller(authorization)
    return projects.get(who, project)


@app.put('/projects/{project}')
def update_project(project: str, body: ProjectBody,
                   authorization: Optional[str] = Header(default=None)):
    who = caller(authorization)
    return projects.save(who, _token(authorization), project=project,
                         name=body.name, files=body.files, source=body.source,
                         entry=body.entry, tests=body.tests,
                         settings=body.settings, note=body.note,
                         public=body.public)


@app.delete('/projects/{project}')
def delete_project(project: str, from_store: bool = False,
                   authorization: Optional[str] = Header(default=None)):
    who = caller(authorization)
    return projects.delete(who, project, _token(authorization), from_store)


@app.post('/projects/{project}/share')
def share_project(project: str,
                  authorization: Optional[str] = Header(default=None)):
    who = caller(authorization)
    return projects.share(who, _token(authorization), project)


@app.post('/projects/{project}/unshare')
def unshare_project(project: str,
                    authorization: Optional[str] = Header(default=None)):
    who = caller(authorization)
    return projects.unshare(who, _token(authorization), project)


@app.get('/open')
def open_cid(cid: str, authorization: Optional[str] = Header(default=None)):
    """Read a shared project straight out of the store.

    Open on purpose: a public CID is public, and a share link that demanded a
    sign-in would not be a share link.
    """
    return projects.open_bundle(_token(authorization), cid)


@app.post('/fork')
def fork_project(body: ForkBody,
                 authorization: Optional[str] = Header(default=None)):
    who = caller(authorization)
    return projects.fork(who, _token(authorization), body.cid, body.name)


@app.get('/store')
def store_status(authorization: Optional[str] = Header(default=None)):
    """Where storage stands for this caller, and what is blocking it."""
    return LINK.status(_token(authorization))


@app.post('/store/terms')
def store_terms(authorization: Optional[str] = Header(default=None)):
    """Sign-accept the store's terms with the caller's own token."""
    token = _token(authorization)
    if not token:
        raise HTTPException(401, 'sign in first')
    return LINK.accept_terms(token)


# ── the agent door: hand a project to the build module ───────────────


class AgentRunBody(BaseModel):
    prompt: str
    project: Optional[str] = None
    name: Optional[str] = None
    template: Optional[str] = None
    model: Optional[str] = None


class AgentVerifyBody(BaseModel):
    account: str = 'default'
    password: Optional[str] = None
    network: Optional[str] = None
    confirm: bool = False


@app.get('/agent')
def agent_status():
    """Is the build module up, and where workspaces are checked out."""
    return builder.status()


@app.post('/agent/run')
def agent_run(body: AgentRunBody,
              authorization: Optional[str] = Header(default=None)):
    """Materialize a project as a folder of code and set an agent on it.

    With `project` the agent edits that one; without, a new project is seeded
    first (from `template`, or a compilable stub). The job runs in the build
    module under the CALLER'S token — build's own whitelist, credits and
    sandbox decide, exactly as if they had typed the prompt there.
    """
    who = caller(authorization)
    return builder.run(who, _token(authorization), body.prompt,
                       project=body.project, name=body.name,
                       template=body.template, model=body.model)


@app.get('/agent/runs')
def agent_runs(limit: int = Query(default=30, le=200),
               project: Optional[str] = None,
               authorization: Optional[str] = Header(default=None)):
    who = caller(authorization)
    return {'runs': builder.runs(who, limit, project)}


@app.get('/agent/runs/{run_id}')
def agent_poll(run_id: int,
               authorization: Optional[str] = Header(default=None)):
    """One run: build's live view of the job, and — once, when the job ends —
    the sync back into a new project version plus a free compile check."""
    who = caller(authorization)
    return builder.poll(who, _token(authorization), run_id)


@app.post('/agent/runs/{run_id}/verify')
def agent_verify(run_id: int, body: AgentVerifyBody,
                 authorization: Optional[str] = Header(default=None)):
    """Run the edited project's suites on a chain — real deploys, real
    receipts. This is what 'the agent's work is verified' means here."""
    who = caller(authorization)
    return builder.verify(who, _token(authorization), run_id, body.account,
                          password=body.password, network=body.network,
                          confirm=body.confirm)


@app.post('/agent/runs/{run_id}/cancel')
def agent_cancel(run_id: int,
                 authorization: Optional[str] = Header(default=None)):
    who = caller(authorization)
    return builder.cancel(who, _token(authorization), run_id)


@app.get('/projects/{project}/workspace')
def project_workspace(project: str,
                      authorization: Optional[str] = Header(default=None)):
    """Check the project out as a mod-shaped folder and say what landed where."""
    who = caller(authorization)
    row = projects.get(who, project)
    return builder.materialize(who, row)


# ── tests: put it on a chain and push it ─────────────────────────────


class TestBody(BaseModel):
    project: Optional[str] = None
    files: Optional[Dict[str, str]] = None
    source: Optional[str] = None
    contract: Optional[str] = None
    suites: Optional[Any] = None
    account: str = 'default'
    password: Optional[str] = None
    network: Optional[str] = None
    args: Optional[List[Any]] = None
    value: Any = 0
    address: Optional[str] = None
    abi: Optional[Any] = None
    solc: Optional[str] = None
    optimize: bool = True
    confirm: bool = False
    store_report: bool = True


@app.post('/test')
def run_tests(body: TestBody, authorization: Optional[str] = Header(default=None)):
    """Deploy to a testnet, run the suite, report every case.

    Slow by nature — a real chain has to mine each write — so a client should
    give it the same patience it gives `/deploy`.
    """
    who = caller(authorization)
    return harness.run(who, body.account, network=body.network,
                       files=body.files, source=body.source,
                       project=body.project, contract=body.contract,
                       suites=body.suites, args=body.args, value=body.value,
                       password=body.password, address=body.address,
                       abi=body.abi, solc=body.solc, optimize=body.optimize,
                       confirm=body.confirm, token=_token(authorization),
                       store_report=body.store_report)


@app.post('/test/generate')
def generate_suite(body: TestBody):
    """A starter suite read off a compiled ABI — every free getter, no guesses."""
    abi = body.abi
    if abi is None:
        files = body.files or ({'Contract.sol': body.source} if body.source else None)
        if not files:
            raise HTTPException(400, 'give me source, files, or an abi')
        compiled = compiler.compile_sources(files, version=body.solc,
                                            optimize=body.optimize)
        deployable = [c for c in compiled['contracts'] if c['deployable']]
        chosen = next((c for c in deployable if c['name'] == body.contract),
                      deployable[0] if deployable else None)
        if chosen is None:
            raise HTTPException(422, 'this source has nothing deployable in it')
        abi = chosen['abi']
    if isinstance(abi, str):
        abi = json.loads(abi)
    return harness.generate(abi, name=body.contract or 'smoke')


@app.get('/tests')
def test_runs(limit: int = Query(default=30, le=200), project: Optional[str] = None,
              authorization: Optional[str] = Header(default=None)):
    who = caller(authorization)
    return {'runs': harness.runs(who, limit, project)}


@app.get('/tests/{run_id}')
def test_report(run_id: int, authorization: Optional[str] = Header(default=None)):
    who = caller(authorization)
    return harness.report(who, run_id)


# ── history ──────────────────────────────────────────────────────────

@app.get('/history')
def history(network: Optional[str] = None, limit: int = Query(default=50, le=500),
            authorization: Optional[str] = Header(default=None)):
    who = caller(authorization)
    return {'txs': ledger.txs(who, network, limit)}


# ── MCP ──────────────────────────────────────────────────────────────

def _mcp_url(request: Request) -> str:
    """The url an MCP client should use, as the caller actually reached us.

    Three deployments, one answer. On a bare port the path is already right.
    Behind the console the page asked its own origin for `/ethdesk/_api/...`, which
    the alias middleware recorded before rewriting it. Behind the gateway,
    caddy has *stripped* `/api/eth` before we ever saw the request — so the
    path we hold is right for us and wrong for the client, and the prefix has
    to be put back. A client handed `https://host/mcp` would 404 forever.
    """
    scheme = request.headers.get('x-forwarded-proto') or request.url.scheme
    host = (request.headers.get('x-forwarded-host')
            or request.headers.get('host') or request.url.netloc)
    path = request.scope.get('eth_path') or str(request.url.path)
    prefix = request.headers.get('x-forwarded-prefix') or ''
    if not prefix and not path.startswith(BASE) and f':{CONFIG.get("port")}' not in host:
        # Reached over a name rather than our own port: that is the gateway,
        # whose convention for every module's API is /api/{name}.
        prefix = f'/api/{CONFIG["name"]}'
    # Always name the /mcp endpoint itself, whichever route asked.
    root = path.split('/mcp', 1)[0]
    return f"{scheme}://{host}{prefix}{root}/mcp"


@app.get('/mcp')
def mcp_schema(request: Request):
    """The whole MCP server as a document — no client needed to read it."""
    return mcp_server.describe(_mcp_url(request))


@app.get('/mcp/tools')
def mcp_tools():
    doc = mcp_server.describe()
    return {'count': doc['count'], 'tools': doc['tools']}


@app.get('/mcp/config')
def mcp_config(request: Request):
    return mcp_server.client_config(_mcp_url(request))


@app.post('/mcp')
async def mcp_call(request: Request,
                   authorization: Optional[str] = Header(default=None)):
    """Streamable HTTP transport. The caller's token rides into the tools.

    `local=False`: an HTTP caller does not share this box's filesystem or its
    mod key, so tools that would act as the box refuse rather than quietly
    acting as somebody else.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content=mcp_server._error(
            None, -32700, 'parse error: body is not valid JSON'))
    ctx = mcp_server.Ctx(token=authorization, local=False)
    if isinstance(body, list):                      # a JSON-RPC batch
        out = [r for r in (mcp_server.handle(item, ctx) for item in body)
               if r is not None]
        return JSONResponse(content=out or None, status_code=200 if out else 202)
    response = mcp_server.handle(body, ctx)
    if response is None:
        return JSONResponse(content=None, status_code=202)
    return JSONResponse(content=response)


if __name__ == '__main__':
    import argparse

    import uvicorn
    parser = argparse.ArgumentParser(description='eth api')
    parser.add_argument('--port', type=int,
                        default=int(CONFIG.get('port', 50750)))
    parser.add_argument('--host', default='0.0.0.0')
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port)
