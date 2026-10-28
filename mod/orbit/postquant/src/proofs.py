"""Customizable off-chain proofs for state machines: the pqp1 layer.

contracts.py gave a machine one way to be believed: re-run it (`replay`).
That is right for a counter and wrong for a minute of compute, or for a
machine that is not wasm at all. This module makes the way a transition is
accepted a CHOICE the machine's owner makes, and pins it in the receipt.

A PROOF SYSTEM answers one question, off chain and without trusting the
prover:  is (state_in, input) -> state_out a legal step?  Three ship.

replay   Re-execute the pqc1 wasm on the same state and input and compare.
         Needs the code; the proof is empty. (contracts.replay)

wasm     A VERIFIER: one importless wasm blob exporting the pqp1 ABI

             pqp_verify(state_in, sl, input, il, state_out, ol, proof, pl)
                 -> i32        1 = valid, anything else = refused

         Importless (parsed from the binary, re-checked by the host per call)
         means no clock, no randomness, no host call, so the verdict is a
         function of five byte strings and the blob's hash. Anyone deploys
         their own: a spec checker that never runs the machine, a PoW check,
         a Merkle-path check, a SNARK/STARK verifier compiled to wasm — the
         chain does not care what the proof IS, only that the blob accepts it.

quorum   k-of-n post-quantum attestations. Signers vouch for the transition
         (ML-DSA/SLH-DSA/any registered key type, under ATTEST_CTX) and the
         verifier counts distinct allowed signers. Nothing is executed: trust
         moves from code to people, which is the honest model for machines
         whose steps cannot be re-run (oracles, human decisions, hardware).

THE POLICY is the owner's commitment to one of these: a public JSON blob
anchored at contract/<name>/proof ({system, verifier?, k?, signers?}). With
a policy set, `submit` refuses transitions proved any other way, and verify
reports whether an old receipt matches the policy NOW (soft, like
code_is_current). The receipt carries the system it was accepted under in
over.proof, so a receipt is verifiable without the policy; a receipt with no
over.proof is replay (every pre-0.15 receipt verifies unchanged).

THE RECEIPT is still pqr1, still signed by the machine's owner under
RECEIPT_CTX; the owner is vouching that they RAN the acceptance check. The
check itself is refutation-only: a proof that does not verify makes the
receipt false, never "secure".
"""

from __future__ import annotations

import hashlib
import json
import os
import time

import keys as K
from pq import algos, wasmtype, wasmvm
from state import StateError
import contracts as C

ABI = 'pqp1'
ATTEST_V = 'pqa1'
ATTEST_CTX = b'postquant/transition-attest/v1'
SYSTEMS = ('replay', 'wasm', 'quorum')
MAX_PROOF = int(os.environ.get('POSTQUANT_PROOF_MAX', 64 << 10))
VERIFY_BUDGET_MS = int(os.environ.get('POSTQUANT_PROOF_MS', 2000))
MAX_SIGNERS = 32

HERE = os.path.dirname(os.path.abspath(__file__))
PROVERS_DIR = os.path.join(HERE, 'pq', 'wasm-src', 'examples', 'provers')

SYSTEM_INFO = {
    'replay': {
        'title': 'Replay',
        'what': 'Re-run the wasm contract on the same state and input and '
                'compare the result.',
        'proof': 'none (empty)',
        'cost': 'as much as the contract itself',
        'trust': 'nobody — anyone with the code reproduces it',
    },
    'wasm': {
        'title': 'Custom wasm verifier',
        'what': 'A verifier blob you choose (or write) checks the step. It '
                'never has to run the machine: the proof carries the work.',
        'proof': 'whatever the verifier accepts (bytes)',
        'cost': 'whatever the verifier needs — often a few hashes',
        'trust': 'the verifier blob, pinned by hash',
    },
    'quorum': {
        'title': 'Quorum of signers',
        'what': 'k of n named post-quantum keys sign that the step is right. '
                'Nothing is executed.',
        'proof': 'k signatures',
        'cost': 'k signature checks',
        'trust': 'the signers (a threshold of them)',
    },
}

VERIFIER_EXAMPLES = {
    'counter-spec': {
        'what': 'checks counter steps from their rules alone — no proof, no '
                'counter code, any implementation',
        'file': 'counter-spec.wasm', 'source': 'counter_spec.rs',
        'machine': 'counter', 'proof': 'empty',
    },
    'pow-ladder': {
        'what': 'accepts a step when the proof is a nonce making '
                'SHAKE256(state_in|input|nonce) = state_out with 16 leading '
                'zero bits — ~65k hashes to find, one to check',
        'file': 'pow-ladder.wasm', 'source': 'pow_ladder.rs',
        'machine': 'ladder', 'proof': 'a nonce (1-64 bytes)',
    },
}

POW_BITS = 16


def canonical(obj) -> bytes:
    return C.canonical(obj)


def cid_of(b: bytes) -> str:
    return hashlib.sha3_256(b).hexdigest()


# ── verifier blobs ────────────────────────────────────────────────


def inspect(blob: bytes) -> dict:
    """Is this a pqp1 verifier? Structural answer before it runs."""
    try:
        info = wasmtype.inspect(blob)
    except wasmtype.WasmTypeError as e:
        return {'abi': None, 'runnable': False, 'reason': str(e)}
    missing = [x for x in ('memory', 'pq_alloc', 'pq_reset', 'pqp_verify')
               if x not in info['exports']]
    reason = None
    if info['imports']:
        reason = (f'declares {info["imports"]} import(s) — a verifier may '
                  'import nothing, or its verdicts are not reproducible')
    elif missing:
        reason = f'missing export(s) {", ".join(missing)} (pqp1 ABI)'
    return {'abi': ABI if reason is None else None,
            'runnable': reason is None and wasmvm.ENABLED
            and bool(wasmvm.NODE),
            'imports': info['imports'], 'exports': info['exports'],
            'reason': reason}


def example_verifier(name) -> bytes:
    if name not in VERIFIER_EXAMPLES:
        raise StateError(f'no example verifier {name!r} — '
                         f'{", ".join(VERIFIER_EXAMPLES)}',
                         code='no_example', status=404)
    with open(os.path.join(PROVERS_DIR,
                           VERIFIER_EXAMPLES[name]['file']), 'rb') as f:
        return f.read()


def verifier_examples():
    out = []
    for name, e in VERIFIER_EXAMPLES.items():
        item = {'name': name, 'what': e['what'], 'machine': e['machine'],
                'proof': e['proof'], 'example': True,
                'source': f'src/pq/wasm-src/examples/provers/{e["source"]}'}
        try:
            blob = example_verifier(name)
            item.update(cid=cid_of(blob), bytes=len(blob),
                        pqp1=inspect(blob))
        except OSError:
            item['missing'] = True
        out.append(item)
    return out


def known_example(cid):
    for e in verifier_examples():
        if e.get('cid') == cid:
            return e['name']
    return None


# ── policy ────────────────────────────────────────────────────────


def policy_key(name):
    return 'contract/' + name + '/proof'


def make_policy(system, *, verifier=None, k=None, signers=None) -> dict:
    if system not in SYSTEMS:
        raise StateError(f'unknown proof system {system!r} — '
                         f'{", ".join(SYSTEMS)}', code='bad_system')
    p = {'v': 'pqp-policy1', 'system': system}
    if system == 'wasm':
        if not verifier or len(verifier) != 64:
            raise StateError('system=wasm needs verifier= (a verifier name '
                             'or cid)', code='missing_arg')
        p['verifier'] = verifier
    elif system == 'quorum':
        signers = sorted({str(s).strip() for s in (signers or []) if s})
        if not signers or len(signers) > MAX_SIGNERS:
            raise StateError(f'quorum needs 1..{MAX_SIGNERS} signers= '
                             '(addresses)', code='missing_arg')
        try:
            k = int(k or len(signers))
        except (TypeError, ValueError):
            raise StateError('k must be an integer', code='bad_args') from None
        if not 1 <= k <= len(signers):
            raise StateError(f'k must be 1..{len(signers)}',
                             code='bad_args')
        p.update(k=k, signers=signers)
    return p


def parse_policy(blob: bytes) -> dict:
    try:
        p = json.loads(blob.decode())
        assert p['v'] == 'pqp-policy1' and p['system'] in SYSTEMS
        return p
    except (ValueError, KeyError, AssertionError, TypeError, AttributeError):
        raise StateError('not a proof policy', code='bad_policy') from None


# ── the claim an attestation signs ────────────────────────────────


def claim_message(*, chain_id, contract, code, prev, state_in: bytes,
                  inp: bytes, state_out: bytes) -> bytes:
    return canonical({'v': ATTEST_V, 'chain_id': chain_id,
                      'contract': contract, 'code': code, 'prev': prev,
                      'state_in': cid_of(state_in),
                      'input_sha3': cid_of(inp),
                      'state_out': cid_of(state_out)})


def make_attestation(wallet, message: bytes) -> dict:
    algo = algos.get(wallet['scheme'])
    sig = algo.sign(K.secret_key(wallet), message, ATTEST_CTX)
    return {'by': wallet['address'], 'scheme': wallet['scheme'],
            'pk': wallet['pk'], 'sig': sig.hex()}


def _check_quorum(policy, message: bytes, attestations) -> dict:
    allowed = set(policy.get('signers') or [])
    k = int(policy.get('k') or 0)
    good, bad = [], []
    for at in attestations or []:
        try:
            by, scheme = at['by'], at['scheme']
            pk, sig = bytes.fromhex(at['pk']), bytes.fromhex(at['sig'])
            algo = algos.get(scheme)
            ok = (algo is not None and by in allowed
                  and K.address(pk, scheme) == by
                  and bool(algo.verify(pk, message, sig, ATTEST_CTX)))
        except Exception:                                  # noqa: BLE001
            ok, by = False, (at.get('by') if isinstance(at, dict) else None)
        (good if ok else bad).append(by)
    distinct = sorted(set(good))
    valid = len(distinct) >= k > 0
    out = {'valid': valid, 'k': k, 'signers': len(allowed),
           'good': distinct, 'rejected': len(bad)}
    if not valid:
        out['reason'] = (f'{len(distinct)} valid attestation(s) from '
                         f'allowed signers, need {k}')
    return out


def parse_attestations(raw) -> list:
    if isinstance(raw, (bytes, bytearray)):
        try:
            raw = json.loads(bytes(raw).decode())
        except ValueError:
            raise StateError('attestations are not JSON',
                             code='bad_proof') from None
    if not isinstance(raw, list):
        raise StateError('attestations must be a list', code='bad_proof')
    return raw


# ── the wasm verdict ──────────────────────────────────────────────


def run_verifier(path: str, sha3: str, state_in: bytes, inp: bytes,
                 state_out: bytes, proof: bytes) -> dict:
    if not wasmvm.ENABLED or not wasmvm.NODE:
        raise StateError('this node has no wasm host — it can store '
                         'verifiers, not run them', code='no_wasm',
                         status=503)
    if len(proof) > MAX_PROOF:
        raise StateError(f'proof is {len(proof)} bytes, cap {MAX_PROOF}',
                         code='too_big', status=413)
    if wasmvm._file_hash(path) != sha3:
        raise StateError('verifier on disk does not hash to its cid',
                         code='bad_code')
    try:
        resp = wasmvm._HOST.call({
            'wasm': path, 'sha3': sha3, 'op': 'prove', 'pure': True,
            'state_in': state_in.hex(), 'input': inp.hex(),
            'state_out': state_out.hex(), 'proof': proof.hex()})
    except Exception as e:                                # noqa: BLE001
        raise StateError(f'wasm host failed: {e}', code='wasm_host',
                         status=503) from None
    if not resp.get('ok'):
        err = resp.get('error') or 'wasm host error'
        if 'import' in err or 'does not export' in err or 'hashes to' in err:
            raise StateError(err, code='bad_code')
        return {'valid': False, 'ret': None, 'ms': None,
                'reason': f'verifier trapped: {err}'}
    ms = resp.get('ms')
    if ms is not None and ms > VERIFY_BUDGET_MS:
        raise StateError(f'verifier took {ms:.0f}ms, over the '
                         f'{VERIFY_BUDGET_MS}ms budget', code='too_slow',
                         status=413)
    out = {'valid': bool(resp.get('valid')), 'ret': resp.get('ret'),
           'ms': ms}
    if not out['valid']:
        out['reason'] = (f'verifier refused the step (ret {resp.get("ret")})')
    return out


def check_wasm(store, verifier_cid, state_in, inp, state_out, proof) -> dict:
    import vault as V
    if not store.has(verifier_cid):
        return {'valid': False, 'reason': 'this node does not hold the '
                'verifier blob — fetch it from any node by cid',
                'held': False}
    info = inspect(store.get(verifier_cid))
    if not info['abi']:
        return {'valid': False, 'reason': info['reason']}
    try:
        res = run_verifier(store._p(verifier_cid), verifier_cid, state_in,
                           inp, state_out, proof)
    except V.VaultError as e:
        return {'valid': False, 'reason': str(e)}
    res['verifier'] = verifier_cid
    return res


# ── check a claimed transition (no receipt yet) ───────────────────


def check_transition(system, *, store, policy, claim: dict, state_in: bytes,
                     inp: bytes, state_out: bytes, proof: bytes = b'',
                     attestations=None, code_path=None) -> dict:
    """The acceptance test. `claim` is the claim_message() kwargs."""
    out = {'system': system}
    if system == 'replay':
        if not code_path:
            return {**out, 'valid': False,
                    'reason': 'replay needs a pqc1 wasm contract'}
        res = C.execute(code_path, claim['code'], state_in, inp)
        ok = (not res['reverted']) and res['out'] == state_out
        out.update(valid=ok, ms=res['ms'], ret=res['ret'])
        if not ok:
            out['reason'] = ('the contract reverts on this input'
                             if res['reverted'] else
                             're-execution produces a different state')
        return out
    if system == 'wasm':
        out.update(check_wasm(store, policy['verifier'], state_in, inp,
                              state_out, proof))
        return out
    if system == 'quorum':
        msg = claim_message(state_in=state_in, inp=inp, state_out=state_out,
                            **claim)
        out.update(_check_quorum(policy, msg, attestations))
        return out
    raise StateError(f'unknown proof system {system!r}', code='bad_system')


# ── re-check a stored receipt ─────────────────────────────────────


def descriptor(system, policy, proof: bytes) -> dict | None:
    """What goes into over.proof (nothing for replay: old shape kept)."""
    if system == 'replay':
        return None
    d = {'system': system, 'proof_sha3': cid_of(proof),
         'proof_bytes': len(proof)}
    if system == 'wasm':
        d['verifier'] = policy['verifier']
    else:
        d.update(k=policy['k'], signers=policy['signers'])
    return d


def check_receipt(r: dict, store) -> dict:
    """Receipt-side verification dispatch (replay is contracts.replay)."""
    over = r['over']
    desc = over.get('proof')
    if not desc or desc.get('system') == 'replay':
        out = C.replay(r, store)
        out['system'] = 'replay'
        return out
    import vault as V
    system = desc['system']
    out = {'system': system, 'proof': desc.get('proof_sha3'),
           'state_out_claimed': over['state_out'], 'replayed': False}
    try:
        state_in = b'' if over['state_in'] == cid_of(b'') \
            else store.get(over['state_in'])
        state_out = b'' if over['state_out'] == cid_of(b'') \
            else store.get(over['state_out'])
        proof = b'' if desc.get('proof_sha3') == cid_of(b'') \
            else store.get(desc['proof_sha3'])
    except V.VaultError as e:
        out.update(matches=None, valid=None,
                   reason=f'cannot re-check here: {e} — fetch the blobs '
                          'from any node by cid')
        return out
    inp = bytes.fromhex(over['input'])
    if cid_of(inp) != over.get('input_sha3'):
        out.update(matches=False, valid=False,
                   reason='input does not hash to input_sha3')
        return out
    if cid_of(proof) != desc.get('proof_sha3'):
        out.update(matches=False, valid=False,
                   reason='proof does not hash to proof_sha3')
        return out
    if system == 'wasm':
        res = check_wasm(store, desc['verifier'], state_in, inp, state_out,
                         proof)
        res['verifier'] = desc['verifier']
    elif system == 'quorum':
        try:
            ats = parse_attestations(proof)
        except StateError as e:
            out.update(matches=False, valid=False, reason=str(e))
            return out
        msg = claim_message(chain_id=over['chain_id'],
                            contract=over['contract'], code=over['code'],
                            prev=over['prev'], state_in=state_in, inp=inp,
                            state_out=state_out)
        res = _check_quorum(desc, msg, ats)
    else:
        res = {'valid': False, 'reason': f'unknown proof system {system!r}'}
    out.update(res)
    out['matches'] = bool(res.get('valid'))
    return out


# ── bundled provers (what a prover RUNS, to make the examples usable) ──


def _counter_step(state: bytes, inp: bytes) -> bytes:
    v = int.from_bytes(state, 'little') if state else 0
    if not inp:
        raise StateError('empty input', code='bad_args')
    op = inp[0]
    if op == 0 and len(inp) == 1:
        v += 1
    elif op == 1 and len(inp) == 1:
        v -= 1
    elif op == 2 and len(inp) == 9:
        v += int.from_bytes(inp[1:], 'little')
    elif op == 3 and len(inp) == 1:
        v = 0
    else:
        raise StateError('not a counter input (00 inc, 01 dec, '
                         '02+u64 add, 03 reset)', code='bad_args')
    if not 0 <= v < 1 << 64:
        raise StateError('the counter would under/overflow — nothing to '
                         'prove', code='bad_args')
    return v.to_bytes(8, 'little')


def _mine_ladder(state: bytes, inp: bytes):
    prev = state if state else b'\x00' * 32
    n = 0
    while True:
        nonce = n.to_bytes(8, 'little')
        h = hashlib.shake_256(prev + inp + nonce).digest(32)
        if int.from_bytes(h, 'big') >> (256 - POW_BITS) == 0:
            return h, nonce
        n += 1


def solve_example(name, state: bytes, inp: bytes):
    """(state_out, proof) for a bundled verifier, or None."""
    if name == 'counter-spec':
        return _counter_step(state, inp), b''
    if name == 'pow-ladder':
        return _mine_ladder(state, inp)
    return None
