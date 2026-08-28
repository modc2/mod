"""
0xprof — a market for zero-knowledge proofs, and four ways to check one.

The verifier comes first and the market second, because a market for proofs
nobody re-checks is a market for JSON. Every proof that lands here is run
through every method this box can bring to bear — its own pairing arithmetic,
iden3's snarkjs in a subprocess, and Ethereum's own bn128 precompiles over
eth_call — and each answer is kept separately, so agreement is visible and
disagreement is louder.

    /systems             the proof systems, and what each one actually claims
    /methods             the verifiers, where each one runs, and what it can do
    /verify              check a proof. No account, no charge, no record
    /proofs              publish, browse, buy
    /proofs/{id}/verify  run the methods again — signed, and filed under your name
    /proofs/{id}/checks  every run of the methods on it, and who asked for each
    /proofs/{id}/attest  tell this box what your own verifier saw
    /verifiers           the addresses that have checked things here
    /bounties            pay for a proof that does not exist yet
    /prove               make one, for the systems small enough to prove here

This is the API half. The console is its own service (src/app/server.py,
:50611) and reaches this through its own origin at /0xprof/_api, so one page
works behind the gateway and on a bare port alike.

WHAT THIS SERVER WILL NOT DO
    It will not report a method's crash as a false proof. It will not call
    something verified because one implementation liked it. It will not serve
    a priced proof's bytes to somebody who has not bought it. And it will not
    quietly promote a proof because a browser said it was fine.
"""
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Body, FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response

SRC = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SRC.parent))

from src import bounties, identity, market, proofs, storage, systems, verify  # noqa: E402
from src.methods import native, snarkjs  # noqa: E402

VERSION = '1.0.0'
BASE = '/0xprof'
APP_DIR = SRC / 'app'
ROOT = SRC.parent

app = FastAPI(title='0xprof', version=VERSION,
              description='A marketplace for zk proofs, verified several ways')
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_methods=['*'],
                   allow_headers=['*'])


@app.middleware('http')
async def api_alias(request, call_next):
    """/0xprof/_api/* is this same API, one path segment along.

    Behind the gateway the API answers on /api/0xprof and the console on
    /0xprof; on a bare port both are here. The page always asks its own origin
    for `_api` — one build, no CORS preflight, no baked-in API URL.
    """
    path = request.scope.get('path', '')
    if path.startswith(f'{BASE}/_api'):
        request.scope['path'] = path[len(f'{BASE}/_api'):] or '/'
    return await call_next(request)


def caller(authorization: Optional[str], required: bool = True) -> Optional[str]:
    try:
        return (identity.require(authorization) if required
                else identity.whoami(authorization))
    except identity.AuthError as e:
        raise HTTPException(401, str(e))


def owner_only(authorization: Optional[str]) -> str:
    try:
        return identity.require_owner(authorization)
    except identity.AuthError as e:
        raise HTTPException(403, str(e))


def fail(e: Exception) -> HTTPException:
    return HTTPException(400, f'{type(e).__name__}: {e}')


# ── what this is ─────────────────────────────────────────────────────

@app.get('/')
def root():
    return {
        'module': '0xprof',
        'version': VERSION,
        'description': 'A marketplace for zero-knowledge proofs, where every '
                       'proof is checked by several independent verifiers and '
                       'the disagreements are published',
        'systems': systems.names(),
        'methods': [m['method'] for m in verify.methods()],
        'available_methods': [m['method'] for m in verify.methods() if m.get('available')],
        'statuses': ['unverified', 'claimed', 'verified', 'invalid', 'disputed'],
        'quorum': verify.QUORUM,
        'storage': 'the store mod — no private database',
        'console': BASE,
        'endpoints': ['/systems', '/methods', '/verify', '/prove', '/proofs',
                      '/proofs/{id}', '/proofs/{id}/verify', '/proofs/{id}/checks',
                      '/proofs/{id}/attest', '/proofs/{id}/buy', '/proofs/{id}/refund',
                      '/verifiers', '/bounties', '/bounties/{id}/submit',
                      '/account/{address}', '/auth/*'],
        'signed': {'/proofs/{id}/verify': 'needs a personal_sign naming the proof — '
                                          'the re-run is published under that address'},
    }


@app.get('/health')
def health():
    ready = [m['method'] for m in verify.methods()
             if m.get('available') and m.get('authoritative')]
    return {
        'ok': bool(ready),
        'authoritative_methods': ready,
        'quorum_reachable': len(ready) >= verify.QUORUM,
        'note': ('a proof can reach `verified` here'
                 if len(ready) >= verify.QUORUM else
                 'only one authoritative method is available, so nothing can get '
                 'past `claimed` — install node, or set ZKPROF_RPC'),
        'ts': time.time(),
    }


@app.get('/status')
def status():
    return {
        'version': VERSION,
        'auth': identity.status(),
        'storage': storage.stats(),
        'market': proofs.stats(),
        'methods': verify.methods(),
        'bounties': {'open': len(bounties.search(state='open'))},
    }


@app.get('/systems')
def list_systems():
    return {'systems': systems.catalog(),
            'note': 'zero_knowledge=false entries verify perfectly well and hide '
                    'nothing — the flag is there so nobody is misled'}


@app.get('/systems/{name}')
def one_system(name: str):
    try:
        spec = systems.get(name)
    except KeyError as e:
        raise HTTPException(404, str(e))
    return {**spec, 'available_here': verify.methods_for(spec['system'])}


@app.get('/methods')
def list_methods():
    return {'methods': verify.methods(), 'quorum': verify.QUORUM,
            'authoritative': list(verify.AUTHORITATIVE),
            'witness': list(verify.WITNESS),
            'note': 'witness methods are recorded and can contest a proof; they '
                    'can never promote one'}


# ── verify, with nothing attached ────────────────────────────────────

@app.post('/verify')
def verify_now(payload: Dict[str, Any] = Body(...)):
    """Check a proof and keep nothing.

    Open to anyone, unauthenticated, on purpose: the check is the useful part
    of this module and putting it behind a sign-in would mean the honest thing
    to do with a suspicious proof is the thing with friction.
    """
    proof = payload.get('proof')
    if not isinstance(proof, dict):
        raise HTTPException(400, 'send {"proof": {...}} — the proof object itself')
    statement = (payload.get('statement') or payload.get('vkey')
                 or payload.get('verification_key') or {})
    signals = (payload.get('public_signals') or payload.get('publicSignals') or [])
    try:
        system = (systems.resolve(payload['system']) if payload.get('system')
                  else systems.sniff({'proof': proof, 'vkey': statement}))
        return verify.verify(system, proof, statement, signals,
                             method_names=payload.get('methods'),
                             quorum=int(payload.get('quorum') or verify.QUORUM))
    except (KeyError, verify.VerifyError) as e:
        raise HTTPException(400, str(e))


@app.post('/prove')
def prove(payload: Dict[str, Any] = Body(...)):
    """Make a proof, for the systems whose prover fits in this module.

    schnorr, dleq and merkle are a hash and a couple of multiplications, so they
    are proved here. The snark systems need a proving key and a compiled
    circuit; send them as base64 (`zkey`, `wasm`) with the witness `inputs` and
    snarkjs does the work.
    """
    system = (payload.get('system') or '').lower()
    try:
        if system == 'schnorr':
            made = native.prove_schnorr(payload['secret'], payload.get('context', ''))
        elif system == 'dleq':
            made = native.prove_dleq(payload['secret'], payload.get('H'),
                                     payload.get('context', ''))
        elif system == 'merkle':
            made = native.prove_merkle(payload['leaves'], int(payload.get('index', 0)),
                                       payload.get('hash', 'sha256'),
                                       bool(payload.get('sorted', False)))
        elif system in ('groth16', 'plonk', 'fflonk'):
            import base64
            zkey = payload.get('zkey')
            wasm = payload.get('wasm')
            if not zkey or not wasm:
                raise HTTPException(400, 'a snark proof needs `zkey` and `wasm` '
                                         '(base64) plus `inputs`')
            zkey_bytes = base64.b64decode(zkey)
            made = snarkjs.prove(zkey_bytes, base64.b64decode(wasm),
                                 payload.get('inputs') or {}, system)
            made['statement'] = snarkjs.export_vkey(zkey_bytes)
        else:
            raise HTTPException(400, f'cannot prove {system!r} here — '
                                     'schnorr, dleq, merkle, groth16, plonk, fflonk')
    except HTTPException:
        raise
    except Exception as e:
        raise fail(e)

    statement = made.get('statement') or {}
    checked = verify.verify(made['system'], made['proof'], statement,
                            made.get('public_signals') or [])
    return {**made, 'verification': checked,
            'note': 'proved and immediately re-checked — a prover that cannot '
                    'convince the verifiers is a bug worth failing loudly on'}


# ── proofs ───────────────────────────────────────────────────────────

@app.get('/sample')
def sample(system: str = 'groth16', circuit: str = 'threshold'):
    """A real proof to try the verifiers on.

    Not a mock: these are the fixtures in `fixtures/`, produced by a real
    circom compile, a real (single-contributor, so not production-safe) setup
    and a real prover. A console whose demo button loads something that cannot
    actually be verified teaches you nothing about the verifiers.
    """
    tag = {'groth16': 'g16', 'plonk': 'plonk', 'fflonk': 'fflonk'}.get(
        systems.resolve(system))
    if not tag:
        raise HTTPException(400, f'no sample for {system} — try groth16, plonk or fflonk')
    circuit = 'threshold' if circuit not in ('threshold', 'multiplier') else circuit
    directory = ROOT / 'fixtures'
    try:
        return {
            'system': systems.resolve(system),
            'circuit': circuit,
            'about': ('"the number I committed to is at least 1000", proved without '
                      'revealing the number' if circuit == 'threshold'
                      else '"I know two numbers whose product is 33"'),
            'proof': json.loads((directory / f'{circuit}_{tag}_proof.json').read_text()),
            'statement': json.loads((directory / f'{circuit}_{tag}_vkey.json').read_text()),
            'public_signals': json.loads((directory / f'{circuit}_{tag}_public.json').read_text()),
            'source': f'circuits/{circuit}.circom',
        }
    except FileNotFoundError:
        raise HTTPException(404, 'the fixtures are not built here — run '
                                 './fixtures/build.sh in the module directory')


@app.get('/proofs')
def list_proofs(system: str = None, status: str = None, tag: str = None,
                author: str = None, q: str = None, free: bool = None,
                for_sale: bool = None, limit: int = Query(100, le=500)):
    return {'proofs': proofs.search(system=system, status=status, tag=tag,
                                    author=author, q=q, free=free,
                                    for_sale=for_sale, limit=limit)}


@app.post('/proofs')
def publish_proof(payload: Dict[str, Any] = Body(...),
                  authorization: Optional[str] = Header(None)):
    who = caller(authorization)
    proof = payload.get('proof')
    if not isinstance(proof, dict):
        raise HTTPException(400, 'send {"proof": {...}}')
    try:
        return proofs.publish(
            payload.get('system'), proof,
            payload.get('statement') or payload.get('vkey') or {},
            payload.get('public_signals') or payload.get('publicSignals') or [],
            author=who, title=payload.get('title', ''),
            description=payload.get('description', ''),
            price=float(payload.get('price') or 0),
            tags=payload.get('tags') or [],
            methods=payload.get('methods'),
            public_proof=payload.get('public_proof'))
    except (proofs.ProofError, KeyError, verify.VerifyError) as e:
        raise fail(e)


@app.post('/proofs/upload')
async def upload_proof(proof: UploadFile = File(...),
                       vkey: Optional[UploadFile] = File(None),
                       public: Optional[UploadFile] = File(None),
                       system: Optional[str] = Form(None),
                       title: str = Form(''), description: str = Form(''),
                       price: float = Form(0.0), tags: str = Form(''),
                       authorization: Optional[str] = Header(None)):
    """The snarkjs three-file shape, as files: proof.json, vkey, public.json.

    This is how proofs actually arrive — as the three files a `snarkjs
    fullprove` wrote — so the console lets you drop them rather than making you
    paste JSON into a form.
    """
    who = caller(authorization)

    async def read(upload):
        if upload is None:
            return None
        try:
            return json.loads((await upload.read()).decode())
        except Exception as e:
            raise HTTPException(400, f'{upload.filename} is not JSON: {e}')

    try:
        return proofs.publish(
            system, await read(proof), await read(vkey) or {},
            await read(public) or [], author=who, title=title,
            description=description, price=float(price or 0),
            tags=[t for t in (tags or '').split(',') if t.strip()])
    except (proofs.ProofError, KeyError, verify.VerifyError) as e:
        raise fail(e)


@app.get('/proofs/{proof_id}')
def one_proof(proof_id: str, authorization: Optional[str] = Header(None)):
    who = caller(authorization, required=False) or ''
    try:
        record = proofs.view(proof_id, who)
    except proofs.ProofError as e:
        raise HTTPException(404, str(e))
    record['refundable_to'] = market.refundable(proof_id, record.get('status'))
    return record


@app.post('/proofs/{proof_id}/verify/challenge')
def reverify_challenge(proof_id: str, payload: Dict[str, Any] = Body(...)):
    """The message to sign before the methods will run again on this proof."""
    address = payload.get('address')
    if not address:
        raise HTTPException(400, 'which address is asking?')
    try:
        proofs.get(proof_id)
    except proofs.ProofError as e:
        raise HTTPException(404, str(e))
    return identity.action_challenge(proof_id, address)


@app.post('/proofs/{proof_id}/verify')
def reverify(proof_id: str, payload: Dict[str, Any] = Body(default={})):
    """Re-run every method — signed, because the answer is published as yours.

    /verify checks a loose proof for anyone with no account and no signature,
    and that stays true: the check is the useful part of this module. This is a
    different act. It rewrites what a *listing* says its verifiers think, under
    a name, in front of the people deciding whether to buy it — so it costs one
    personal_sign over a message naming this proof, and the signature is filed
    next to the run it paid for. Anyone can then recover the address from it
    without asking this box anything.

    ZKPROF_OPEN does not open this one, and that is not an oversight. Open mode
    says "believe whoever the caller claims to be", which is a thing a server
    can decide to do. A signature is not a claim about identity that this box
    could choose to accept — it is evidence a reader checks later, without this
    box in the room, and no environment variable can manufacture it. A dev box
    with no wallet has `m 0xprof/recheck`, which signs with the box's own key.
    """
    payload = payload or {}
    if not payload.get('signature'):
        raise HTTPException(401,
                            'a re-verification is published under an address, so it '
                            'has to be signed: POST /proofs/{id}/verify/challenge '
                            'with {"address"}, sign the message it returns with '
                            'personal_sign, and send it back here as '
                            '{"address", "message", "signature"}. From a shell, '
                            '`m 0xprof/recheck id=…` signs with this box\'s key')
    try:
        signed = identity.from_action(proof_id, payload.get('address', ''),
                                      payload.get('message', ''),
                                      payload['signature'])
    except identity.AuthError as e:
        raise HTTPException(401, str(e))
    try:
        record = proofs.recheck(proof_id, payload.get('methods'),
                                by=signed['address'], signature=signed['signature'])
    except proofs.ProofError as e:
        raise HTTPException(404 if 'no proof' in str(e) else 409, str(e))
    return {**proofs.listing(record), 'rechecked_by': signed['address'],
            'signature': signed['signature']}


@app.post('/proofs/{proof_id}/attest')
def attest(proof_id: str, payload: Dict[str, Any] = Body(...),
           authorization: Optional[str] = Header(None)):
    """Report what your own verifier saw — the console posts this after the
    browser has checked a proof with the snarkjs wasm bundle."""
    who = caller(authorization, required=False) or 'anonymous'
    try:
        record = proofs.attest(proof_id, who, bool(payload.get('ok')),
                               payload.get('method', 'browser'),
                               payload.get('detail'))
    except proofs.ProofError as e:
        raise HTTPException(404, str(e))
    return {'attested': True, 'by': who, **proofs.listing(record)}


@app.post('/proofs/{proof_id}/buy')
def buy(proof_id: str, authorization: Optional[str] = Header(None)):
    who = caller(authorization)
    try:
        return proofs.buy(proof_id, who)
    except (proofs.ProofError, market.MarketError) as e:
        raise fail(e)


@app.post('/proofs/{proof_id}/refund')
def refund(proof_id: str, authorization: Optional[str] = Header(None)):
    """Unwind a purchase of a proof that no longer verifies. Rechecked first."""
    who = caller(authorization)
    try:
        return proofs.refund(proof_id, who)
    except (proofs.ProofError, market.MarketError) as e:
        raise fail(e)


@app.delete('/proofs/{proof_id}')
def unpublish(proof_id: str, authorization: Optional[str] = Header(None)):
    who = caller(authorization)
    try:
        return proofs.unpublish(proof_id, who)
    except proofs.ProofError as e:
        raise fail(e)


# ── bounties ─────────────────────────────────────────────────────────

@app.get('/bounties')
def list_bounties(state: str = None, system: str = None, poster: str = None,
                  limit: int = Query(100, le=500)):
    return {'bounties': bounties.search(state=state, system=system,
                                        poster=poster, limit=limit)}


@app.post('/bounties')
def create_bounty(payload: Dict[str, Any] = Body(...),
                  authorization: Optional[str] = Header(None)):
    who = caller(authorization)
    try:
        return bounties.create(
            who, payload.get('system', 'groth16'), float(payload.get('reward') or 0),
            vkey=payload.get('vkey') or payload.get('statement') or {},
            require=payload.get('require') or [],
            title=payload.get('title', ''), description=payload.get('description', ''),
            status=payload.get('accept_status', 'verified'),
            ttl=float(payload.get('ttl') or bounties.DEFAULT_TTL))
    except (bounties.BountyError, market.MarketError, KeyError) as e:
        raise fail(e)


@app.get('/bounties/{bounty_id}')
def one_bounty(bounty_id: str):
    try:
        return bounties.get(bounty_id)
    except bounties.BountyError as e:
        raise HTTPException(404, str(e))


@app.post('/bounties/{bounty_id}/submit')
def submit(bounty_id: str, payload: Dict[str, Any] = Body(...),
           authorization: Optional[str] = Header(None)):
    who = caller(authorization)
    try:
        return bounties.submit(bounty_id, who, payload.get('proof') or {},
                               payload.get('public_signals')
                               or payload.get('publicSignals') or [],
                               payload.get('statement'))
    except (bounties.BountyError, proofs.ProofError, verify.VerifyError) as e:
        raise fail(e)


@app.post('/bounties/{bounty_id}/cancel')
def cancel_bounty(bounty_id: str, authorization: Optional[str] = Header(None)):
    who = caller(authorization)
    try:
        return bounties.cancel(bounty_id, who)
    except (bounties.BountyError, market.MarketError) as e:
        raise fail(e)


@app.post('/bounties/sweep')
def sweep_bounties():
    """Return the escrow on everything that expired. Safe to call by anyone —
    it only ever hands money back to the address that put it up."""
    return bounties.sweep()


# ── the people, as opposed to the methods ────────────────────────────

@app.get('/verifiers')
def verifiers(limit: int = Query(50, le=200)):
    """Who has made this box check something, and what they saw.

    Deliberately not a leaderboard of trust. Nobody on this list verified
    anything — the methods did. What the list shows is who put their name on a
    run and who reported a disagreement from their own machine, which is the
    part a reader has to weigh themselves.
    """
    return {
        'verifiers': proofs.people(limit=limit),
        'note': 'a signed re-verification is an address asking this box to run '
                'every method again and publishing the answer under its name; a '
                'witness is somebody\'s own verifier reporting what it saw. Only '
                'the second one can contradict this box',
    }


@app.get('/proofs/{proof_id}/checks')
def proof_checks(proof_id: str):
    """Every run of the methods on this proof, and who asked for it."""
    try:
        record = proofs.get(proof_id)
    except proofs.ProofError as e:
        raise HTTPException(404, str(e))
    return {'proof': proof_id, 'checks': proofs.checks_of(record),
            'verifiers': proofs.roster(record)}


# ── accounts ─────────────────────────────────────────────────────────

@app.get('/account/{address}')
def account(address: str):
    return market.summary(address)


@app.get('/me')
def me(authorization: Optional[str] = Header(None)):
    who = caller(authorization)
    return {**market.summary(who), 'owner': identity.is_owner(who)}


@app.post('/credits/grant')
def grant(payload: Dict[str, Any] = Body(...),
          authorization: Optional[str] = Header(None)):
    who = owner_only(authorization)
    try:
        acct = market.grant(payload['address'], float(payload['amount']),
                            payload.get('reason', f'granted by {who}'))
    except (market.MarketError, KeyError) as e:
        raise fail(e)
    return market.summary(acct['address'])


# ── auth ─────────────────────────────────────────────────────────────

@app.post('/auth/challenge')
def auth_challenge(payload: Dict[str, Any] = Body(...)):
    address = payload.get('address')
    if not address:
        raise HTTPException(400, 'which address?')
    return identity.challenge(address)


@app.post('/auth/verify')
def auth_verify(payload: Dict[str, Any] = Body(...)):
    try:
        address = identity.from_signature(payload.get('address', ''),
                                          payload.get('message', ''),
                                          payload.get('signature', ''))
    except identity.AuthError as e:
        raise HTTPException(401, str(e))
    session = identity.mint_session(address)
    if not identity.owner():
        identity.claim(address)
    return {**session, 'owner': identity.is_owner(address)}


@app.get('/auth/me')
def auth_me(authorization: Optional[str] = Header(None)):
    who = caller(authorization, required=False)
    return {'address': who, 'signed_in': bool(who),
            'owner': identity.is_owner(who), **identity.status()}


# ── the console, and the bundle it verifies with ─────────────────────

@app.get('/vendor/snarkjs.min.js')
def snarkjs_bundle():
    """The same verifier the server runs, shipped to the tab.

    This is what makes the browser method meaningful rather than decorative:
    the page checks the proof itself, in wasm, and the box only finds out what
    the answer was afterwards.
    """
    bundle = ROOT / 'node_modules' / 'snarkjs' / 'build' / 'snarkjs.min.js'
    if not bundle.exists():
        raise HTTPException(404, 'snarkjs is not installed here — `npm install` '
                                 'in the module directory')
    return FileResponse(bundle, media_type='text/javascript')


@app.get(BASE)
@app.get(BASE + '/')
def console():
    index = APP_DIR / 'index.html'
    if not index.exists():
        return JSONResponse({'error': 'console not built'}, status_code=404)
    return FileResponse(index, media_type='text/html')


@app.get(BASE + '/{asset:path}')
def console_asset(asset: str):
    target = (APP_DIR / asset).resolve()
    if not str(target).startswith(str(APP_DIR.resolve())) or not target.exists():
        raise HTTPException(404, asset)
    media = {'.js': 'text/javascript', '.css': 'text/css',
             '.html': 'text/html', '.json': 'application/json'}
    return FileResponse(target, media_type=media.get(target.suffix, 'text/plain'))


if __name__ == '__main__':
    import argparse
    import uvicorn
    parser = argparse.ArgumentParser(description='0xprof API')
    parser.add_argument('--port', type=int, default=int(
        __import__('os').environ.get('ZKPROF_PORT', 50610)))
    parser.add_argument('--host', default='0.0.0.0')
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port)
