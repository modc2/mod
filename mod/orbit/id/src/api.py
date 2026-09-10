"""The HTTP surface — the same functions, over the wire.

Nothing is decided here. This file turns a request into a call into
`identity`, and an exception into a status code. The rules about who may join
an identity live in one place, and it is not this one.

Two things are deliberately not offered: there is no endpoint that returns a
private key, because none is ever held, and there is no endpoint that links an
account without a signature, because that would make every other guarantee in
the module decorative. `/rebuild` is loopback-only — it rewrites the index, and
while the index is only a cache of the logs, nothing off this machine needs to
touch it.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src import accounts, chains, demo as demo_run, identity, store

app = FastAPI(title='id', version='1.0.0',
              description='One identity, many accounts. Wallets included.')
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_methods=['*'],
                   allow_headers=['*'])

LOOPBACK = {'127.0.0.1', '::1', 'localhost'}


def _local_only(request: Request) -> None:
    host = request.client.host if request.client else ''
    if host not in LOOPBACK:
        raise HTTPException(403, 'that can only be done from the machine the store is on')


@app.exception_handler(identity.IdError)
@app.exception_handler(chains.ProofError)
@app.exception_handler(chains.AddressError)
@app.exception_handler(accounts.ProofError)
@app.exception_handler(accounts.AccountError)
@app.exception_handler(ValueError)
def _bad_request(request: Request, exc: Exception) -> JSONResponse:
    missing = isinstance(exc, identity.IdError) and str(exc).startswith('no identity')
    return JSONResponse({'error': str(exc), 'kind': type(exc).__name__},
                        status_code=404 if missing else 400)


@app.get('/')
def root() -> Dict[str, Any]:
    return {
        'name': 'id',
        'description': 'An identity is a set of accounts, and the set is a log of '
                       'signatures. Wallets on eleven chains, plus accounts that '
                       'prove themselves by publishing.',
        'chains': [c['chain'] for c in chains.known()],
        'services': [s['service'] for s in accounts.known()],
        'flow': ['POST /challenge — get the exact text to sign',
                 'sign it in the wallet',
                 'POST /submit — hand back the signature',
                 'GET /whois — see the whole identity'],
        'endpoints': {
            'chains': 'GET /chains | GET /services',
            'address': 'GET /address?chain=&address=',
            'challenge': 'POST /challenge {chain, address, op, id, other, name, ttl}',
            'submit': 'POST /submit {nonce, signature, pubkey, source, session}',
            'verify': 'POST /verify {chain, address, message, signature} — one-shot, stores nothing',
            'whois': 'GET /whois?account=solana:9xQe… | ?chain=eth&address=0x…',
            'identity': 'GET /id/{id} | /id/{id}/log | /id/{id}/audit?live=',
            'list': 'GET /ids',
            'merge': 'GET /merge?id=&other= — what each side has to sign',
            'move': 'GET /export/{id} | POST /import',
            'demo': 'GET /demo — the whole flow with throwaway keys',
            'health': 'GET /health',
        },
        'state': store.stats(),
    }


@app.get('/health')
def health() -> Dict[str, Any]:
    return {'ok': True, **store.stats()}


# ── what can be linked ───────────────────────────────────────────────

@app.get('/chains')
def chain_list() -> List[Dict[str, Any]]:
    return chains.known()


@app.get('/services')
def service_list() -> List[Dict[str, Any]]:
    return accounts.known()


@app.get('/address')
def address(chain: str, address: str) -> Dict[str, Any]:
    canonical = chains.parse(chain, address)
    name = chains.get(chain).name
    return {'chain': name, 'address': canonical,
            'equivalents': chains.equivalents(chain, canonical),
            'linked_to': store.resolve(f'{name}:{canonical}')}


# ── proving ──────────────────────────────────────────────────────────

@app.post('/challenge')
def challenge(body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    kind = body.get('kind') or body.get('chain') or body.get('service')
    handle = body.get('handle') or body.get('address')
    if not kind or not handle:
        raise HTTPException(400, 'pass a chain (or service) and an address (or handle)')
    return identity.challenge(kind=kind, handle=handle,
                              op=body.get('op', 'link'), id=body.get('id'),
                              other=body.get('other'), name=body.get('name'),
                              target=body.get('target'), ttl=int(body.get('ttl', 900)))


@app.post('/submit')
def submit(request: Request, body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    nonce = body.get('nonce')
    if not nonce:
        raise HTTPException(400, 'which challenge is this answering? pass the nonce')
    session = body.get('session') or _bearer(request)
    return identity.submit(nonce, signature=body.get('signature'),
                           pubkey=body.get('pubkey'), source=body.get('source'),
                           session=session)


def _bearer(request: Request) -> Optional[str]:
    header = request.headers.get('authorization') or ''
    return header[7:].strip() if header.lower().startswith('bearer ') else None


@app.post('/verify')
def verify(body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """One signature, one address, no identity involved and nothing written."""
    for field in ('chain', 'address', 'message', 'signature'):
        if not body.get(field):
            raise HTTPException(400, f'{field} is required')
    return chains.verify(body['chain'], body['address'], body['message'],
                         body['signature'], pubkey=body.get('pubkey'))


@app.get('/session')
def session(request: Request) -> Dict[str, Any]:
    held = identity.session_of(_bearer(request) or '')
    if not held:
        return {'valid': False, 'note': 'no session, or it has expired'}
    return {'valid': True, 'id': held['id'], 'held_by': held['account'],
            'expires_at': held['expires_at']}


@app.delete('/session')
def end_session(request: Request) -> Dict[str, Any]:
    token = _bearer(request)
    return {'ok': bool(token) and identity.drop_session(token)}


# ── reading ──────────────────────────────────────────────────────────

@app.get('/whois')
def whois(account: str = None, chain: str = None,
          address: str = None) -> Dict[str, Any]:
    if not account and not (chain and address):
        raise HTTPException(400, 'pass account=kind:handle, or chain= and address=')
    return identity.whois(kind=chain, handle=address, account=account)


@app.get('/ids')
def ids() -> List[Dict[str, Any]]:
    return identity.listing()


@app.get('/id/{id}')
def one(id: str, proofs: bool = False) -> Dict[str, Any]:
    return identity.document(id, proofs=proofs)


@app.get('/id/{id}/log')
def log(id: str) -> List[Dict[str, Any]]:
    return store.events(store.follow(id))


@app.get('/id/{id}/audit')
def audit(id: str, live: bool = False) -> Dict[str, Any]:
    return identity.audit(id, live=live)


@app.get('/merge')
def merge(id: str, other: str) -> Dict[str, Any]:
    survivor, absorbed = identity.merge_order(id, other)
    return {'survivor': survivor, 'absorbed': absorbed,
            'sign': [{'identity': side,
                      'any_of': [a['account'] for a in
                                 identity.document(side, proofs=False)['accounts']]}
                     for side in (survivor, absorbed)],
            'note': 'a member of each side signs the same pair'}


# ── moving ───────────────────────────────────────────────────────────

@app.get('/export/{id}')
def export(id: str) -> Dict[str, Any]:
    return identity.export(id)


@app.post('/import')
def load(request: Request, body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    _local_only(request)
    document = body.get('document') or body
    return identity.import_document(document, overwrite=bool(body.get('overwrite')))


@app.post('/rebuild')
def rebuild(request: Request) -> Dict[str, Any]:
    _local_only(request)
    return identity.rebuild()


@app.get('/demo')
def demo() -> Dict[str, Any]:
    """The whole flow with keys made on the spot, in a directory that is deleted."""
    return demo_run.run()


def main() -> None:
    import uvicorn
    parser = argparse.ArgumentParser(description='id — the API')
    parser.add_argument('--port', type=int, default=50650)
    parser.add_argument('--host', default='0.0.0.0')
    options = parser.parse_args()
    store.ensure()
    uvicorn.run(app, host=options.host, port=options.port, log_level='info')


if __name__ == '__main__':
    main()
