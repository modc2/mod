"""Running contracts OFF chain and proving it: the pqc1 ABI and receipts.

The chain stores contracts (pq_contract deploy: public code in the vault,
contract/<name> = SHA3-256(code)) and does not execute them. This module is
the other half — how a stored wasm contract is RUN, by whom, and what a
third party gets to check afterwards.

THE MODEL
    A contract is a pure state transition in one importless wasm blob:

        pqc_run(state, state_len, input, input_len, out, out_cap) -> i32

    next-state length on success, a negative reason code to revert. No
    imports (parsed from the binary before anything runs, re-checked by the
    host per call) means no clock, no randomness, no host call: the output
    is a function of (code bytes, state bytes, input bytes) and nothing else.
    That is the whole proof system. Every node that holds the same three
    blobs lands on the same fourth one.

THE RECEIPT (pqr1)
    One run produces one receipt — a canonical JSON object `over`

        {v, chain_id, contract, abi, code, n, prev, state_in, input,
         input_sha3, state_out, out_bytes, ret, ts}

    where code / state_in / state_out / prev are SHA3-256 content ids of
    blobs in the vault (state bytes are stored public, like code), signed by
    the runner's post-quantum wallet under RECEIPT_CTX (never TX_CONTEXT, so
    a receipt signature can never double as a transaction witness). The
    receipt itself is a blob; its cid is anchored at contract/<name>/receipt
    by the owner of contract/<name>. Receipts chain through `prev`, so the
    anchored latest one commits to the whole execution history.

VERIFYING
    verify() = check the signature, fetch code + state_in by hash, RE-RUN
    the wasm, compare the output hash to state_out, and check the chain
    side: is the signer the contract's owner, is this receipt the anchored
    one, is the code it ran the code the anchor currently commits to. All
    of it is refutation: a receipt that does not replay is simply false.

WHAT THIS IS NOT
    Not a STARK. The verifier re-executes rather than checks a succinct
    proof, which is fine for a counter and wrong for a minute of compute —
    Cairo contracts (lang=cairo/sierra) are the succinct path, and a STARK
    proof hash would sit in the same receipt slot. And it is not consensus:
    the chain enforces who may anchor, not what the contract means.
"""

from __future__ import annotations

import hashlib
import json
import os
import time

import keys as K
from pq import algos, wasmtype, wasmvm
from state import StateError
import vault as V

ABI = 'pqc1'
RECEIPT_V = 'pqr1'
RECEIPT_CTX = b'postquant/contract-receipt/v1'

# OUT_CAP is part of the protocol, not a tuning knob: it is written into
# every receipt and replay uses the receipt's value, because a contract that
# reverts -2 (output too small) under one cap and succeeds under another
# would make the same run come out two ways on two verifiers. 48 KiB leaves
# room for state + input inside the examples' 128 KiB arena.
OUT_CAP = 48 << 10
MAX_STATE = OUT_CAP
MAX_INPUT = int(os.environ.get('POSTQUANT_CONTRACT_INPUT_MAX', 16 << 10))
RUN_BUDGET_MS = int(os.environ.get('POSTQUANT_CONTRACT_RUN_MS', 5000))

HERE = os.path.dirname(os.path.abspath(__file__))
EXAMPLES_DIR = os.path.join(HERE, 'pq', 'wasm-src', 'examples', 'contracts')

EXAMPLES = {
    'counter': {
        'what': 'a u64 that goes up and down — the smallest pqc1 contract',
        'state': '8 bytes u64 LE (empty = 0)',
        'input': '00 inc | 01 dec | 02 <u64 LE> add | 03 reset',
        'reverts': '-1 bad input, -2 out too small, -3 bad state, '
                   '-4 under/overflow',
        'try': ['00', '00', '02' + (40).to_bytes(8, 'little').hex(), '01'],
    },
    'ledger': {
        'what': 'a token ledger: mint, transfer, burn over 32-byte account '
                'ids — authorisation is the receipt',
        'state': 'n rows of <id:32><balance:u64 LE>, sorted, no zeros',
        'input': '00 <to:32><amt:8> mint | 01 <from:32><to:32><amt:8> '
                 'transfer | 02 <from:32><amt:8> burn',
        'reverts': '-1 bad input, -2 out too small, -3 bad state, '
                   '-4 insufficient/overflow, -5 full, -6 zero/self',
        'try': ['00' + 'aa' * 32 + (100).to_bytes(8, 'little').hex(),
                '01' + 'aa' * 32 + 'bb' * 32 + (40).to_bytes(8, 'little').hex()],
    },
}


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(',', ':')).encode()


def cid_of(b: bytes) -> str:
    return hashlib.sha3_256(b).hexdigest()


# ── examples ──────────────────────────────────────────────────────


def examples():
    out = []
    for name, e in EXAMPLES.items():
        path = os.path.join(EXAMPLES_DIR, name + '.wasm')
        item = {'name': name, **e, 'source': f'src/pq/wasm-src/examples/'
                                             f'contracts/{name}.rs'}
        try:
            with open(path, 'rb') as f:
                blob = f.read()
            item.update(cid=cid_of(blob), bytes=len(blob),
                        **inspect(blob))
        except OSError:
            item['missing'] = True
        out.append(item)
    return out


def example_blob(name) -> bytes:
    if name not in EXAMPLES:
        raise StateError(f'no example contract {name!r} — '
                         f'{", ".join(EXAMPLES)}', code='no_example',
                         status=404)
    with open(os.path.join(EXAMPLES_DIR, name + '.wasm'), 'rb') as f:
        return f.read()


# ── the blob ──────────────────────────────────────────────────────


def inspect(blob: bytes) -> dict:
    """Is this a pqc1 contract? Structural answer, before it runs."""
    try:
        info = wasmtype.inspect(blob)
    except wasmtype.WasmTypeError as e:
        return {'abi': None, 'runnable': False, 'reason': str(e)}
    missing = [x for x in ('memory', 'pq_alloc', 'pq_reset', 'pqc_run')
               if x not in info['exports']]
    reason = None
    if info['imports']:
        reason = (f'declares {info["imports"]} import(s) — a contract may '
                 'import nothing (no clock, no randomness, no host), or its '
                 'runs are not reproducible')
    elif missing:
        reason = f'missing export(s) {", ".join(missing)} (pqc1 ABI)'
    return {'abi': ABI if reason is None else None,
            'runnable': reason is None and wasmvm.ENABLED
            and bool(wasmvm.NODE),
            'imports': info['imports'], 'exports': info['exports'],
            'reason': reason}


def execute(path: str, sha3: str, state: bytes, inp: bytes,
            out_cap: int = OUT_CAP) -> dict:
    """One pqc1 call through the hash-enforced host. Returns
    {ret, out, ms, reverted, trap}; raises StateError only when the run
    could not be attempted at all (no host, bad blob)."""
    if not wasmvm.ENABLED or not wasmvm.NODE:
        raise StateError('this node has no wasm host (POSTQUANT_WASM=off or '
                         'no node runtime) — it can store contracts, not '
                         'run them', code='no_wasm', status=503)
    if len(state) > MAX_STATE:
        raise StateError(f'state is {len(state)} bytes, cap {MAX_STATE}',
                         code='too_big', status=413)
    if len(inp) > MAX_INPUT:
        raise StateError(f'input is {len(inp)} bytes, cap {MAX_INPUT}',
                         code='too_big', status=413)
    if wasmvm._file_hash(path) != sha3:
        raise StateError('code on disk does not hash to its cid',
                         code='bad_code')
    try:
        resp = wasmvm._HOST.call({'wasm': path, 'sha3': sha3, 'op': 'run',
                                  'pure': True, 'state': state.hex(),
                                  'input': inp.hex(), 'out_cap': int(out_cap)})
    except Exception as e:                                # noqa: BLE001
        raise StateError(f'wasm host failed: {e}', code='wasm_host',
                         status=503) from None
    if not resp.get('ok'):
        err = resp.get('error') or 'wasm host error'
        if 'import' in err or 'does not export' in err or 'hashes to' in err:
            raise StateError(err, code='bad_code')
        # a trap (panic -> unreachable, out of bounds) is a revert
        return {'ret': None, 'out': b'', 'ms': None, 'reverted': True,
                'trap': err}
    ms = resp.get('ms')
    if ms is not None and ms > RUN_BUDGET_MS:
        raise StateError(f'run took {ms:.0f}ms, over the {RUN_BUDGET_MS}ms '
                         'budget — a contract this slow is a denial of '
                         'service on every verifier', code='too_slow',
                         status=413)
    ret = int(resp['ret'])
    return {'ret': ret, 'out': bytes.fromhex(resp.get('out') or ''),
            'ms': ms, 'reverted': ret < 0, 'trap': None}


# ── receipts ──────────────────────────────────────────────────────


def make_receipt(wallet, *, chain_id, contract, code_cid, n, prev,
                 state_in: bytes, inp: bytes, result: dict) -> dict:
    over = {
        'v': RECEIPT_V, 'chain_id': chain_id, 'contract': contract,
        'abi': ABI, 'code': code_cid, 'n': n, 'prev': prev,
        'state_in': cid_of(state_in), 'input': inp.hex(),
        'input_sha3': cid_of(inp),
        'state_out': cid_of(result['out']) if not result['reverted']
        else cid_of(state_in),
        'out_bytes': len(result['out']) if not result['reverted']
        else len(state_in),
        'ret': result['ret'], 'reverted': bool(result['reverted']),
        'out_cap': OUT_CAP, 'ts': int(time.time()),
    }
    algo = algos.get(wallet['scheme'])
    sig = algo.sign(K.secret_key(wallet), canonical(over), RECEIPT_CTX)
    return {'over': over, 'by': wallet['address'], 'scheme': wallet['scheme'],
            'pk': wallet['pk'], 'sig': sig.hex(),
            'context': RECEIPT_CTX.decode()}


def receipt_blob(receipt: dict) -> bytes:
    return canonical(receipt)


def parse_receipt(blob: bytes) -> dict:
    try:
        r = json.loads(blob.decode())
        over = r['over']
        assert over['v'] == RECEIPT_V and over['abi'] == ABI
        for k in ('chain_id', 'contract', 'code', 'n', 'state_in', 'input',
                  'state_out', 'ret', 'reverted'):
            assert k in over
        for k in ('by', 'scheme', 'pk', 'sig'):
            assert k in r
        return r
    except (ValueError, KeyError, AssertionError, AttributeError, TypeError):
        raise StateError('not a pqr1 receipt', code='bad_receipt') from None


def check_signature(r: dict) -> dict:
    """Signature + address binding, no chain, no re-run."""
    out = {'scheme': r['scheme'], 'by': r['by']}
    algo = algos.get(r['scheme'])
    if algo is None:
        out.update(sig_valid=False, reason=f'unknown scheme {r["scheme"]}')
        return out
    try:
        pk = bytes.fromhex(r['pk'])
        sig = bytes.fromhex(r['sig'])
        addr_ok = K.address(pk, r['scheme']) == r['by']
        sig_ok = bool(algo.verify(pk, canonical(r['over']), sig, RECEIPT_CTX))
    except Exception as e:                                # noqa: BLE001
        out.update(sig_valid=False, reason=f'malformed: {e}')
        return out
    out.update(sig_valid=sig_ok and addr_ok, address_matches_pk=addr_ok)
    if not sig_ok:
        out['reason'] = 'signature does not verify over the receipt'
    elif not addr_ok:
        out['reason'] = 'pk does not derive the by= address'
    return out


def replay(r: dict, store: V.Store) -> dict:
    """Fetch code + state_in by hash, re-run, compare to what the receipt
    claims. Refutation only."""
    over = r['over']
    out = {'code': over['code'], 'state_in': over['state_in'],
           'state_out_claimed': over['state_out']}
    try:
        code_path = store._p(over['code'])
        if not store.has(over['code']):
            raise V.VaultError('no code blob', 'no_blob', 404)
        state_in = b'' if over['state_in'] == cid_of(b'') \
            else store.get(over['state_in'])
    except V.VaultError as e:
        out.update(replayed=False, matches=None,
                   reason=f'cannot replay here: {e} — fetch the blobs from '
                          'any node by cid')
        return out
    inp = bytes.fromhex(over['input'])
    if cid_of(inp) != over.get('input_sha3'):
        out.update(replayed=False, matches=False,
                   reason='input does not hash to input_sha3')
        return out
    res = execute(code_path, over['code'], state_in, inp,
                  out_cap=int(over.get('out_cap') or OUT_CAP))
    got = cid_of(res['out']) if not res['reverted'] else cid_of(state_in)
    out.update(replayed=True, ret=res['ret'], reverted=res['reverted'],
               ms=res['ms'], state_out_replayed=got,
               matches=(got == over['state_out']
                        and bool(res['reverted']) == bool(over['reverted'])
                        and (res['reverted'] or res['ret'] == over['ret'])))
    if not out['matches']:
        out['reason'] = 're-execution does not reproduce the receipt'
    return out
