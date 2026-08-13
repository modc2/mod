"""
Many methods, one verdict.

A single verifier says true or false and you either trust it or you don't.
This module's whole position is that you shouldn't have to: run the check
through every method that can do it, keep each answer separately, and let the
disagreements be visible rather than averaged away.

    method      where it runs                           independent of
    ──────────────────────────────────────────────────────────────────────
    native      this process, py_ecc + hand arithmetic   node, the network
    snarkjs     a node subprocess, iden3's code          this module's math
    node        a node subprocess, BigInt from spec      the python arithmetic
    evm         a public ethereum node, bn128 precompiles  this box entirely
    solidity    the same node, running the real verifier
                contract through an eth_call override    this box entirely
    browser     the visitor's tab, snarkjs wasm          this box entirely
    attest      somebody else's box, signed              everything here

The first five are *authoritative*: this box either ran them or watched an
RPC run them, so their answers decide the status. The last two are *witness*
answers — a browser or a peer reporting what it saw. They are recorded, they
are shown, and they never silently promote a proof, because a claim about a
verification is not a verification. What they can do is contradict, and a
contradiction is worth surfacing even when it comes from a stranger.

    unverified   nothing has checked it yet
    claimed      exactly one authoritative method says valid
    verified     `quorum` (default 2) independent methods agree it is valid
    invalid      an authoritative method says false, and none disagree
    disputed     they disagree — the interesting case, and the reason for all this
"""
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

from . import systems
from .methods import evm, native, node, snarkjs, solidity

QUORUM = 2
MODULES = {'native': native, 'snarkjs': snarkjs, 'node': node,
           'evm': evm, 'solidity': solidity}
AUTHORITATIVE = ('native', 'snarkjs', 'node', 'evm', 'solidity')
WITNESS = ('browser', 'attest')
METHODS = AUTHORITATIVE + WITNESS


class VerifyError(Exception):
    pass


def methods() -> List[Dict[str, Any]]:
    """Every method, whether it can run right now, and what it can verify."""
    out = []
    for name, spec, where in (
        ('native', native.available(), 'this process (python)'),
        ('snarkjs', snarkjs.available(), 'a node subprocess on this box'),
        ('node', node.available(), 'a node subprocess on this box'),
        ('evm', evm.available(), "a public ethereum node's bn128 precompiles"),
        ('solidity', solidity.available(),
         'a public ethereum node, running the verifier contract itself'),
    ):
        out.append({'method': name, 'authoritative': True, 'runs_in': where, **spec})
    out.append({
        'method': 'browser', 'authoritative': False, 'available': True,
        'runs_in': "the visitor's own tab, with the snarkjs wasm bundle",
        'systems': ['groth16', 'plonk', 'fflonk', 'merkle', 'schnorr', 'dleq', 'pedersen'],
        'independent': True,
        'note': 'the only method whose answer you can check without trusting this '
                'box — and therefore the only one this box cannot vouch for. '
                'Recorded as a witness, never as a verification',
    })
    out.append({
        'method': 'attest', 'authoritative': False, 'available': True,
        'runs_in': "someone else's deployment of this module",
        'systems': systems.names(), 'independent': True,
        'note': 'a signed verdict from another address — replay by a peer. Weighs '
                'nothing on its own and everything when it disagrees',
    })
    return out


def methods_for(system: str) -> List[str]:
    """The authoritative methods that can actually check this system, now."""
    system = systems.resolve(system)
    usable = []
    for name, module in MODULES.items():
        if not module.supports(system):
            continue
        if not module.available().get('available'):
            continue
        usable.append(name)
    return usable


def run_method(method: str, system: str, proof: Dict[str, Any],
               vkey: Optional[Dict[str, Any]], public_signals: Optional[List[Any]],
               by: str = '', **options) -> Dict[str, Any]:
    """One method, one verdict, never an exception.

    Four outcomes, kept distinct because collapsing them is how verifiers end
    up lying: `valid`, `invalid`, `error` (the proof is malformed, or the
    method broke — not a statement about truth) and `unavailable` (the method
    could not run at all, which says nothing whatsoever).

    `by` is who asked for this run. It is attribution and not authority: the
    maths was done by the method, and the address is only the answer to "who
    made this box go and check, and when".
    """
    system = systems.resolve(system)
    started = time.time()
    base = {'method': method, 'system': system, 'at': time.time()}
    if by:
        base['by'] = by
    module = MODULES.get(method)
    if module is None:
        return {**base, 'status': 'unavailable',
                'error': f'{method} is a witness method — submit it through /attest'}
    if not module.supports(system):
        return {**base, 'status': 'unavailable',
                'error': f'{method} does not verify {system}'}
    if not module.available().get('available'):
        return {**base, 'status': 'unavailable',
                'error': module.available().get('note', 'not available on this box')}
    try:
        answer = module.verify(system, proof, vkey, public_signals, **options)
    except Exception as e:
        # An error is deliberately not `invalid`. A verifier that reports every
        # crash as a false proof will eventually mark a good proof false.
        return {**base, 'status': 'error', 'error': f'{type(e).__name__}: {e}',
                'ms': int((time.time() - started) * 1000)}
    return {**base,
            'status': 'valid' if answer['ok'] else 'invalid',
            'ok': bool(answer['ok']),
            'detail': answer.get('detail') or {},
            'ms': int((time.time() - started) * 1000)}


def verify(system: str, proof: Dict[str, Any], vkey: Optional[Dict[str, Any]] = None,
           public_signals: Optional[List[Any]] = None,
           method_names: Optional[List[str]] = None,
           quorum: int = QUORUM, by: str = '') -> Dict[str, Any]:
    """Check a proof with everything that can check it, and say what happened."""
    system = systems.resolve(system)
    wanted = [m for m in (method_names or methods_for(system)) if m in AUTHORITATIVE]
    if not wanted:
        raise VerifyError(
            f'no method on this box can verify {system} — '
            f'{systems.get(system)["methods"]} can, in principle')
    # Concurrently, because these are independent by construction: two
    # subprocesses, two round trips to a chain and one pairing computation that
    # do not share a byte. Serially this is the slowest thing in the module.
    with ThreadPoolExecutor(max_workers=len(wanted)) as pool:
        verdicts = list(pool.map(
            lambda name: run_method(name, system, proof, vkey, public_signals, by=by),
            wanted))
    return {'system': system, 'verdicts': verdicts, **consensus(verdicts, quorum)}


def consensus(verdicts: List[Dict[str, Any]], quorum: int = QUORUM) -> Dict[str, Any]:
    """Fold a pile of verdicts into one word, and show the working.

    Witness verdicts are counted separately and can only ever raise `contested`
    — a browser saying "invalid" about a proof three authoritative methods like
    is a reason for a human to look, not a reason to change the answer.
    """
    authoritative = [v for v in verdicts if v.get('method') in AUTHORITATIVE]
    witnesses = [v for v in verdicts if v.get('method') in WITNESS]
    valid = sorted({v['method'] for v in authoritative if v.get('status') == 'valid'})
    invalid = sorted({v['method'] for v in authoritative if v.get('status') == 'invalid'})
    errored = sorted({v['method'] for v in authoritative if v.get('status') == 'error'})

    if valid and invalid:
        status, why = 'disputed', (
            f'{", ".join(valid)} say valid, {", ".join(invalid)} say invalid — '
            'one of these implementations is wrong and that is worth knowing')
    elif invalid:
        status, why = 'invalid', f'{", ".join(invalid)} rejected it'
    elif len(valid) >= max(1, quorum):
        status, why = 'verified', (
            f'{len(valid)} independent methods agree: {", ".join(valid)}')
    elif valid:
        status, why = 'claimed', (
            f'only {valid[0]} has checked it — one method is a claim, '
            f'{quorum} agreeing is a verification')
    elif errored:
        status, why = 'unverified', f'every method errored: {", ".join(errored)}'
    else:
        status, why = 'unverified', 'nothing has checked this yet'

    witness_valid = sorted({(v.get('by') or v.get('method')) for v in witnesses
                            if v.get('status') == 'valid'})
    witness_invalid = sorted({(v.get('by') or v.get('method')) for v in witnesses
                              if v.get('status') == 'invalid'})
    contested = bool(witness_invalid and status in ('verified', 'claimed'))
    return {
        'status': status,
        'why': why,
        'quorum': quorum,
        'agree': valid,
        'disagree': invalid,
        'errors': errored,
        'witnesses': {'valid': witness_valid, 'invalid': witness_invalid},
        'contested': contested,
        'checked': len(verdicts),
    }


def merge(existing: List[Dict[str, Any]], fresh: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Newest verdict per method wins; every distinct witness is kept.

    Re-verifying should update what a method said, not append a second opinion
    from the same method — and that stays true when two different people ask
    for the run, because `native` has one current answer about one proof no
    matter who pressed the button. Who pressed it is on the verdict, and the
    history of who pressed it is the check log on the record.

    Witnesses are the exception, and the reason the key is not just the method:
    two browsers attesting are two witnesses, and the second one must not
    overwrite the first — especially when they disagree.
    """
    by_key: Dict[Any, Dict[str, Any]] = {}
    for verdict in list(existing or []) + list(fresh or []):
        witness = verdict.get('method') in WITNESS
        key = (verdict.get('method'),
               (verdict.get('by') or '').lower() if witness else '')
        if key not in by_key or verdict.get('at', 0) >= by_key[key].get('at', 0):
            by_key[key] = verdict
    return sorted(by_key.values(), key=lambda v: v.get('at', 0))
