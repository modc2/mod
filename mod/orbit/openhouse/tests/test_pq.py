"""
Post-quantum capacity — pq.py and the PQ half of every receipt.

ed25519 and secp256k1 fall to Shor; keccak does not. Each receipt carries an
OHX-KECCAK hash-based signature (checked on Ethereum and NEAR too) and an
ML-DSA-65 signature next to the classical pair. The shared vector
tests/vectors/ohx.json is the same bytes contracts/test/PQ.t.sol and
near/src/lib.rs verify, so the three verifiers cannot drift.
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

MODULE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(MODULE_DIR.parent.parent.parent))
sys.path.insert(0, str(MODULE_DIR / 'api'))


def _load(name, path):
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


pq = _load('openhouse_pq', MODULE_DIR / 'pq.py')
openhouse_mod = _load('openhouse_under_test', MODULE_DIR / 'mod.py')
proofs = _load('openhouse_proofs', MODULE_DIR / 'proofs.py')
CAST = _load('openhouse_demo_cast', MODULE_DIR / 'demo.py').CAST
needs_scarb = pytest.mark.skipif(not proofs.scarb(), reason='scarb not installed')
VEC = json.loads((MODULE_DIR / 'tests' / 'vectors' / 'ohx.json').read_text())


def _b(h):
    return bytes.fromhex(h[2:])


@pytest.fixture
def oh(tmp_path):
    o = openhouse_mod.Mod(store=tmp_path)
    o.set_terms(model='full_credit', fee_pct=2.5, home_price=250, monthly_rent=0.62,
                owner=CAST['owner'], treasury=CAST['owner'])
    return o


# ── the scheme ──────────────────────────────────────────────────

def test_shared_vector_verifies():
    ps, root = _b(VEC['pub_seed']), _b(VEC['root'])
    assert pq.key_id(ps, root) == VEC['id']
    assert pq.verify(ps, root, _b(VEC['fact']), _b(VEC['sig']))
    assert pq.verify(ps, root, bytes(32), _b(VEC['sig2']))
    assert pq.sig_index(_b(VEC['sig'])) == 257 and len(_b(VEC['sig'])) == pq.SIG_BYTES == 4804


def test_vector_is_reproducible_from_the_seeds():
    """The signer is deterministic per leaf — re-deriving gives the same bytes."""
    sk, ps = _b(VEC['sk_seed']), _b(VEC['pub_seed'])
    assert pq.sign_at(sk, ps, _b(VEC['fact']), 257) == _b(VEC['sig'])


def test_forgeries_fail():
    ps, root, f, sig = _b(VEC['pub_seed']), _b(VEC['root']), _b(VEC['fact']), _b(VEC['sig'])
    assert not pq.verify(ps, root, bytes(32), sig), 'other message'
    assert not pq.verify(ps, bytes(32), f, sig), 'other root'
    assert not pq.verify(bytes(32), root, f, sig), 'other seed'
    for at in (0, 3, 4, 1000, 2147, 2148, 4 + 67 * 32 + 3, 4803):
        bad = bytearray(sig)
        bad[at] ^= 1
        assert not pq.verify(ps, root, f, bytes(bad)), f'byte {at}'
    assert not pq.verify(ps, root, f, sig[:-1]) and not pq.verify(ps, root, f, sig + b'\0')
    huge = (pq.CAPACITY).to_bytes(4, 'big') + sig[4:]
    assert not pq.verify(ps, root, f, huge)


def test_signer_is_stateful_and_private(tmp_path):
    s = pq.Signer(tmp_path)
    pub = s.public()
    assert oct((tmp_path / 'proof_pq.json').stat().st_mode & 0o777) == '0o600'
    a, b = s.sign('0x' + '01' * 32), s.sign('0x' + '01' * 32)
    ia, ib = pq.sig_index(_b(a['ohx'])), pq.sig_index(_b(b['ohx']))
    assert ib == ia + 1, 'a leaf never signs twice'
    assert s.capacity()['used'] == ib + 1
    assert pq.check('0x' + '01' * 32, pub, a)['safe']
    assert 'sk_seed' not in json.dumps(pub) and 'mldsa_seed' not in json.dumps(pub)
    assert pq.Signer(tmp_path).public() == pub, 'keys persist'


def test_exhausted_key_refuses_then_rotates(tmp_path):
    s = pq.Signer(tmp_path)
    old = s.public()['ohx']
    d = json.loads((tmp_path / 'proof_pq.json').read_text())
    d['next'] = pq.CAPACITY
    (tmp_path / 'proof_pq.json').write_text(json.dumps(d))
    s = pq.Signer(tmp_path)
    with pytest.raises(pq.PQError, match='exhausted'):
        s.sign('0x' + '02' * 32)
    rot = s.rotate()
    assert rot['retired']['id'] == old['id'] and rot['current']['id'] != old['id']
    assert s.retired()[0]['used'] == pq.CAPACITY
    sig = s.sign('0x' + '02' * 32)
    assert pq.sig_index(_b(sig['ohx'])) == 0


def test_mldsa_half():
    if not pq.mldsa_available():
        pytest.skip('orbit/postquant not on this box')
    m = pq._mldsa()
    pk, sk = m.keygen(pq.MLDSA, seed=bytes(32))
    sig = m.sign(sk, bytes(32), pq.MLDSA, ctx=pq.MLDSA_CTX)
    assert pq.mldsa_verify(pk, bytes(32), sig) is True
    assert pq.mldsa_verify(pk, b'\1' * 32, sig) is False


# ── receipts ────────────────────────────────────────────────────

@needs_scarb
def test_every_receipt_is_quantum_safe(oh):
    r = oh.proof_issue(tenant=CAST['maya'], amount=620_000, period=202610)
    assert 'error' not in r, r
    assert r['sig']['pq']['ohx'] and r['bank']['pq']['ohx']['id']
    v = oh.proof_verify(receipt=r)
    assert v['ok'] and v['policy'] == 'hybrid' and v['quantum']['safe'], v
    assert any(c.get('pq') and 'OHX-KECCAK' in c['claim'] and c['ok'] for c in v['checks'])
    assert oh.proof_verify(receipt=r, policy='pq')['ok']
    assert oh.proof_verify(receipt=r, policy='classical')['ok']
    assert 'error' in oh.proof_verify(receipt=r, policy='nope')


@needs_scarb
def test_after_q_day_only_pq_counts(oh):
    """Simulate a forged classical signature: under policy=pq the receipt
    still stands on its hash-based signature; a forged PQ signature sinks it."""
    r = oh.proof_issue(tenant=CAST['maya'], amount=620_000, period=202610)
    t = json.loads(json.dumps(r))
    t['sig']['near'] = '0x' + '00' * 64
    assert not oh.proof_verify(receipt=t, policy='hybrid')['ok']
    v = oh.proof_verify(receipt=t, policy='pq')
    assert v['ok'], v
    assert any(c.get('ignored') for c in v['checks'])
    bad = json.loads(json.dumps(r))
    sig = bytearray(_b(bad['sig']['pq']['ohx']))
    sig[900] ^= 1
    bad['sig']['pq']['ohx'] = '0x' + sig.hex()
    vb = oh.proof_verify(receipt=bad, policy='pq')
    assert not vb['ok'] and not vb['quantum']['safe']


@needs_scarb
def test_upgrade_countersigns_classical_receipts(oh):
    r = oh.proof_issue(tenant=CAST['maya'], amount=620_000, period=202610)
    p = oh._proofs()
    rows = p._load(p.receipts_path, {})
    for k in ('pq',):
        rows[r['nullifier']]['sig'].pop(k)
        rows[r['nullifier']]['bank'].pop(k)
    p._save(p.receipts_path, rows)
    v = oh.proof_verify(nullifier=r['nullifier'])
    assert v['ok'] and v['policy'] == 'classical' and not v['quantum']['safe']
    assert oh.proof_pq()['receipts']['classical_only'] == [r['nullifier']]
    up = oh.proof_pq_upgrade()
    assert up['upgraded'] == [r['nullifier']]
    v = oh.proof_verify(nullifier=r['nullifier'], policy='pq')
    assert v['ok'] and v['quantum']['safe']
    assert oh.proof(r['nullifier'])['fact'] == r['fact'], 'the fact never changes'
    assert oh.proof_pq_upgrade()['skipped'] == [r['nullifier']], 'idempotent'


def test_pq_status_and_gates(oh):
    st = oh.proof_pq()
    assert st['schemes']['ohx']['sig_bytes'] == 4804
    assert st['leaves']['capacity'] == 65536
    assert {t['quantum'] for t in st['threats']} == {'safe', 'breakable'}
    oh.network = 'mainnet'
    try:
        assert 'error' in oh.proof_pq_rotate() and 'error' in oh.proof_pq_upgrade()
    finally:
        oh.network = 'testnet'
    rot = oh.proof_pq_rotate()
    assert rot['current']['id'] != rot['retired']['id']
    assert len(oh.proof_pq()['retired']) == 1


def test_mcp_and_rest_surface(oh, monkeypatch):
    from mcp_server import TOOLS
    for t in ('openhouse_proof_pq', 'openhouse_proof_pq_upgrade', 'openhouse_proof_pq_rotate'):
        assert t in TOOLS
    assert TOOLS['openhouse_proof_pq']['handler']({}, oh)['leaves']['capacity'] == 65536
    assert 'door' in TOOLS['openhouse_proof_submit']['inputSchema']['properties']
    assert 'policy' in TOOLS['openhouse_proof_verify']['inputSchema']['properties']
    api = _load('openhouse_api_under_test', MODULE_DIR / 'api' / 'api.py')
    monkeypatch.setattr(api, '_openhouse', oh)
    from fastapi.testclient import TestClient
    c = TestClient(api.app)
    assert c.get('/proofs/pq').json()['schemes']['ohx']['name'] == 'OHX-KECCAK'
    assert c.post('/proofs/pq/upgrade', json={}).status_code == 200
    assert c.post('/proofs/submit', json={'nullifier': '0x1', 'door': 'sideways'}).status_code == 400
