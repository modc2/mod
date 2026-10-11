"""Bring-your-own key types: the wasm door, its determinism rule, its gate.

The decisive tests put a stranger's scheme on the wire: the worked example
(Lamport over SHAKE256, compiled from pq/wasm-src/examples/lamport.rs) is
registered through the real tool, opens a wallet, witnesses a transaction,
and is then protected from deletion like any key type history depends on.
The refusals matter just as much: a verify that always says yes, a blob that
declares imports, a blob missing the pq2 ABI — each must be turned away with
the reason, and must leave no name squatted behind it.
"""

import base64
import hashlib
import os

import pytest

import keys as K
import mcp as mcpsrv
from pq import algos, complexity, compose, wasmtype
from state import StateError

EXAMPLES = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), 'src', 'pq', 'wasm-src', 'examples')

LAMPORT_SIZES = {'pk': 16384, 'sig': 8192, 'sk': 16384, 'seed': 32}


def _blob(name):
    with open(os.path.join(EXAMPLES, name), 'rb') as f:
        return f.read()


@pytest.fixture
def cleanup():
    """Every key type a test creates dies with the test — names can never be
    rebound, so a leaked registration would poison the next test."""
    before = set(algos.REGISTRY)
    yield
    for name in set(algos.REGISTRY) - before:
        algos.REGISTRY.pop(name, None)
        complexity.clear_cache(name)
    wasmtype._write_store([s for s in wasmtype._read_store()
                           if s['name'] in algos.REGISTRY])


def test_lamport_admitted_and_measured(cleanup):
    algo, card = wasmtype.create(
        't.lamport', _blob('lamport.wasm'), sizes=LAMPORT_SIZES,
        family='Lamport', quantum_safe=True, quantum_bits=128,
        note='one-time — test')
    assert card['ok'] and card['determinism']
    assert algo.origin == 'wasm'
    assert algos.refusal('t.lamport') is None
    s = complexity.summary(algo)
    assert s['quantum'] == '2^128 (Grover)' and not s['quantum_polynomial']
    assert s['classical_bits'] == 256          # nothing cheaper declared
    # the blob is pinned content-addressed and the registry carries the hash
    sha3 = hashlib.sha3_256(_blob('lamport.wasm')).hexdigest()
    assert algo.wasmtype['sha3_256'] == sha3
    assert os.path.exists(wasmtype._blob_path(sha3))
    # and the whole scheme runs through the blob: sign/verify on real bytes
    pk, sk = algo.keygen(b'\x05' * 32)
    sig = algo.sign(sk, b'msg', b'ctx')
    assert len(pk) == 16384 and len(sig) == 8192
    assert algo.verify(pk, b'msg', sig, b'ctx')
    assert not algo.verify(pk, b'msg!', sig, b'ctx')
    assert not algo.verify(pk, b'msg', sig, b'ctx2')


def test_always_yes_verifier_is_refused_and_unwound(cleanup):
    blob = _blob('alwaysyes.wasm')
    sha3 = hashlib.sha3_256(blob).hexdigest()
    with pytest.raises(wasmtype.WasmTypeError) as e:
        wasmtype.create('t.liar', blob,
                        sizes={'pk': 32, 'sig': 32, 'sk': 32, 'seed': 32})
    assert 'tampered_message' in str(e.value)
    assert algos.maybe('t.liar') is None               # name not squatted
    assert not any(s['name'] == 't.liar' for s in wasmtype.specs())
    assert not os.path.exists(wasmtype._blob_path(sha3))   # blob unwound


def test_imports_are_refused_before_the_blob_runs(cleanup):
    # a minimal module declaring one import: (module (import "a" "b" (func)))
    blob = (b'\x00asm\x01\x00\x00\x00'
            b'\x01\x04\x01\x60\x00\x00'                 # type ()->()
            b'\x02\x07\x01\x01a\x01b\x00\x00')          # import a.b func
    assert wasmtype.inspect(blob)['imports'] == 1
    with pytest.raises(wasmtype.WasmTypeError) as e:
        wasmtype.create('t.impure', blob,
                        sizes={'pk': 32, 'sig': 32, 'sk': 32, 'seed': 32})
    assert 'import' in str(e.value)


def test_pq1_only_blob_lacks_the_abi(cleanup):
    """A builtin verifier (pq1: verify only) is honest wasm but not a key
    type — no keygen, no sign, nothing the node could probe."""
    wasm_dir = os.path.join(os.path.dirname(EXAMPLES), '..', 'wasm')
    with open(os.path.join(wasm_dir, 'mldsa44.wasm'), 'rb') as f:
        blob = f.read()
    with pytest.raises(wasmtype.WasmTypeError) as e:
        wasmtype.create('t.pq1', blob,
                        sizes={'pk': 32, 'sig': 32, 'sk': 32, 'seed': 32})
    assert 'pq_keygen' in str(e.value)


def test_validation_refuses_the_nonsense(cleanup):
    blob = _blob('lamport.wasm')
    with pytest.raises(wasmtype.WasmTypeError):        # not wasm at all
        wasmtype.create('t.junk', b'not wasm', sizes=LAMPORT_SIZES)
    with pytest.raises(wasmtype.WasmTypeError):        # bad name
        wasmtype.create('no spaces', blob, sizes=LAMPORT_SIZES)
    with pytest.raises(wasmtype.WasmTypeError):        # seed must be 32
        wasmtype.create('t.seed', blob,
                        sizes={**LAMPORT_SIZES, 'seed': 16})
    with pytest.raises(wasmtype.WasmTypeError):        # sizes are required
        wasmtype.create('t.nosize', blob, sizes={'seed': 32})
    wasmtype.create('t.once', blob, sizes=LAMPORT_SIZES, quantum_safe=True)
    with pytest.raises(wasmtype.WasmTypeError):        # names cannot rebind
        wasmtype.create('t.once', blob, sizes=LAMPORT_SIZES)


def test_wrong_sizes_fail_the_battery_not_the_node(cleanup):
    """Declared sizes are the host's allocation contract — lying about them
    must end in refusal, not a wedged node."""
    with pytest.raises(wasmtype.WasmTypeError):
        wasmtype.create('t.short', _blob('lamport.wasm'),
                        sizes={'pk': 16384, 'sig': 4096, 'sk': 16384,
                               'seed': 32})
    assert algos.maybe('t.short') is None


def test_store_persists_shares_blobs_and_reloads(cleanup):
    blob = _blob('lamport.wasm')
    wasmtype.create('t.wa', blob, sizes=LAMPORT_SIZES, quantum_safe=True)
    wasmtype.create('t.wb', blob, sizes=LAMPORT_SIZES, quantum_safe=True)
    sha3 = hashlib.sha3_256(blob).hexdigest()
    # one blob on disk, two specs pinned to it
    assert sum(1 for s in wasmtype.specs()
               if s['sha3_256'] == sha3) == 2
    wasmtype.delete('t.wa')
    assert os.path.exists(wasmtype._blob_path(sha3))   # t.wb still needs it
    # a reload must re-register exactly what was stored, once
    algos.REGISTRY.pop('t.wb')
    complexity.clear_cache('t.wb')
    wasmtype.load_store()
    assert algos.maybe('t.wb') is not None
    wasmtype.load_store()                              # idempotent
    assert not any(e['keytype'] == 't.wb' for e in wasmtype.STORE_ERRORS)
    wasmtype.delete('t.wb')
    assert not os.path.exists(wasmtype._blob_path(sha3))   # last spec gone


def test_wasm_keytype_composes_like_any_other(cleanup):
    wasmtype.create('t.wl', _blob('lamport.wasm'), sizes=LAMPORT_SIZES,
                    quantum_safe=True, quantum_bits=128)
    c = compose.create('t.wl+ed', 'all', ['t.wl', 'ed25519'])
    assert algos.refusal('t.wl+ed') is None
    assert c.quantum_safe                      # the lamport part carries it
    pk, sk = c.keygen(b'\x09' * 32)
    sig = c.sign(sk, b'm', b'c')
    assert c.verify(pk, b'm', sig, b'c')
    with pytest.raises(wasmtype.WasmTypeError):    # composite depends on it
        wasmtype.delete('t.wl')
    compose.delete('t.wl+ed')
    wasmtype.delete('t.wl')


def test_the_tool_surface_and_a_witness_on_chain():
    """The whole door through the real tool, ending in a block. No cleanup
    fixture on purpose: t.wired's witness is in the shared test chain, so
    the key type must stay registered for any later replay in this run."""
    created = mcpsrv.call_tool('pq_keytype', {
        'action': 'create', 'name': 't.wired',
        'wasm': base64.b64encode(_blob('lamport.wasm')).decode(),
        'pk_bytes': 16384, 'sig_bytes': 8192, 'sk_bytes': 16384,
        'family': 'Lamport', 'quantum_safe': True, 'quantum_bits': 128,
        'note': 'one-time — wire test'})
    assert created['accepted'] and created['admission']['ok']
    assert created['admission']['determinism']

    listed = mcpsrv.call_tool('pq_keytype', {})
    row = next(k for k in listed['keytypes'] if k['name'] == 't.wired')
    assert row['kind'] == 'wasm' and row['accepted']

    shown = mcpsrv.call_tool('pq_keytype', {'action': 'show',
                                            'name': 't.wired'})
    assert shown['wasmtype']['sha3_256'] == \
        hashlib.sha3_256(_blob('lamport.wasm')).hexdigest()

    tested = mcpsrv.call_tool('pq_keytype_test', {'scheme': 't.wired'})
    assert tested['ok'] and tested['sizes'] == {'pk': 16384, 'sig': 8192}

    # one wallet, one witness — Lamport is one-time, and one is all we need
    mcpsrv.call_tool('pq_wallet', {'action': 'create', 'name': 'wiredw',
                                   'scheme': 't.wired'})
    mcpsrv.call_tool('pq_faucet', {'wallet': 'wiredw', 'amount': '200',
                                   'mine': True})
    mcpsrv.call_tool('pq_set', {'key': 'test/wasmtype', 'data': 'hello',
                                'wallet': 'wiredw', 'days': 1, 'mine': True})
    with pytest.raises(StateError) as e:
        mcpsrv.call_tool('pq_keytype', {'action': 'delete',
                                        'name': 't.wired'})
    assert e.value.code == 'keytype_in_use'
    mcpsrv.call_tool('pq_wallet', {'action': 'remove', 'name': 'wiredw'})


def test_bad_base64_through_the_tool(cleanup):
    with pytest.raises(StateError) as e:
        mcpsrv.call_tool('pq_keytype', {'action': 'create', 'name': 't.b64',
                                        'wasm': 'not base64!!',
                                        'pk_bytes': 32, 'sig_bytes': 32,
                                        'sk_bytes': 32})
    assert e.value.code == 'bad_args'
