"""
Proof of rent paid, without the tenant — proofs.py, the Cairo statement, and
the REST/MCP surfaces over them.

The cross-language vector (PH, VECTOR, VECTOR_FACT and the two signatures)
is the same one pinned in near/src/lib.rs and contracts/test/Proofs.t.sol,
so the fact encoding and both signature schemes cannot drift between the
Python issuer and the two on-chain verifiers. Every Mod gets its own store
(Mod(store=tmp_path)), so nothing touches ~/.openhouse or ~/.mod/openhouse.
Tests that run the Cairo program skip where scarb is not installed.
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

MODULE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(MODULE_DIR.parent.parent.parent.parent))     # /root/mod — `import mod`
sys.path.insert(0, str(MODULE_DIR / 'api'))

from mcp_server import TOOLS  # noqa: E402


def _load(name, path):
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


openhouse_mod = _load('openhouse_under_test', MODULE_DIR / 'mod.py')
proofs = _load('openhouse_proofs', MODULE_DIR / 'proofs.py')
CAST = _load('openhouse_demo_cast', MODULE_DIR / 'demo.py').CAST
MAYA, JONAH, OWNER = CAST['maya'], CAST['jonah'], CAST['owner']

needs_scarb = pytest.mark.skipif(not proofs.scarb(), reason='scarb not installed')

PH = '0x' + '11' * 32
VECTOR = [1, 1, 202610, 1_500_000_000, 0xabc, 0xdef]
VECTOR_FACT = '0x3ed1a6f4a58595b7f7aa1d9aa1363211800b07c54f58c0267328cc38276362e3'
ED_PUB = 'ed25519:AKnL4NNf3DGWZJS6cPknBuEGnVsV4A4m5tgebLHaRSZ9'
ED_SIG = '0x2c1b40126713886cf2f378f83f1d6484dfd6ce1aa88f2dc164e2f96b0ff78056b89895c09eadad1c08354ae11e2bb49d7ca164431b26922c12229035b057d30a'
ETH_ADDR = '0x5050A4F4b3f9338C3472dcC01A87C76A144b3c9c'
ETH_SIG = '0x7f8c56164b5aa96cddd3b99ae0740024d6de7a1e8023578b44e4c4ee15e88e3c608a8e954f2e631b6fa2da0947212893e02e337e66519e32e63cf98e09ae3e841c'


@pytest.fixture
def oh(tmp_path):
    o = openhouse_mod.Mod(store=tmp_path)
    o.set_terms(model='full_credit', fee_pct=2.5, home_price=250, monthly_rent=0.62,
                owner=OWNER, treasury=OWNER)
    return o


# ── encoding ────────────────────────────────────────────────────

def test_fact_vector_is_shared_with_the_chains():
    assert proofs.fact(PH, VECTOR) == VECTOR_FACT
    assert proofs.fact(PH, [proofs.hex32(v) for v in VECTOR]) == VECTOR_FACT
    assert proofs.fact(PH, VECTOR[:5] + [0xdf0]) != VECTOR_FACT
    assert proofs.fact('0x' + '22' * 32, VECTOR) != VECTOR_FACT


def test_felts_and_units():
    assert proofs.felt('0xabc') == 0xabc and proofs.felt('2748') == 2748 and proofs.felt(7) == 7
    with pytest.raises(proofs.ProofError):
        proofs.felt(proofs.FIELD_P)
    assert proofs.tenant_felt('0xABC') == 0xabc
    near = proofs.tenant_felt('maya.near')
    assert 0 < near < proofs.FIELD_P and near == proofs.tenant_felt('MAYA.near')
    assert proofs.tenant_felt('maya.near') != proofs.tenant_felt('jonah.near')
    assert proofs.units(0.62) == 620_000 and proofs.units('1.5') == 1_500_000
    assert proofs.period_of(0) == 197001 and proofs.period_of(1_760_000_000) == 202510
    assert proofs.b58decode(proofs.b58encode(b'\0\0hello')) == b'\0\0hello'


def test_issuer_vectors(tmp_path):
    (tmp_path / 'proof_issuer.json').write_text(json.dumps(
        {'ed25519_seed': '01' * 32, 'eth_key': '02' * 32, 'name': 'vector bank'}))
    iss = proofs.Issuer(tmp_path)
    pub = iss.public()
    assert pub['near'] == ED_PUB and pub['eth'] == ETH_ADDR and pub['name'] == 'vector bank'
    sig = iss.sign(VECTOR_FACT)
    assert sig['near'] == ED_SIG and sig['eth'] == ETH_SIG
    assert all(c['ok'] for c in proofs.verify_signatures(VECTOR_FACT, pub, sig))
    other = proofs.fact(PH, VECTOR[:5] + [0xdf0])
    assert not any(c['ok'] for c in proofs.verify_signatures(other, pub, sig))
    assert not any(c['ok'] for c in proofs.verify_signatures(VECTOR_FACT, {'near': ED_PUB, 'eth': OWNER}, {'near': ED_SIG[:-2] + '00', 'eth': ETH_SIG}))


def test_fresh_issuer_keys_are_private(tmp_path):
    iss = proofs.Issuer(tmp_path, name='x')
    p = tmp_path / 'proof_issuer.json'
    assert oct(p.stat().st_mode)[-3:] == '600'
    assert 'ed25519_seed' not in json.dumps(iss.public())
    assert proofs.Issuer(tmp_path).public() == iss.public(), 'keys persist'


# ── the statement, end to end ───────────────────────────────────

@needs_scarb
def test_program_hash_pins_the_statement():
    info = proofs.program_info()
    assert info['built'] and info['program_hash'].startswith('0x') and len(info['program_hash']) == 66
    assert proofs.run([4]) == [proofs.VERSION]
    c = proofs.run([2, 0xabc, 0x5a17])[0]
    assert proofs.run([3, c, 1, 202610])[0] == proofs.run([1, 1, 202610, 100, 0xabc, 0x5a17, 100])[5]


@needs_scarb
def test_issue_verify_open_and_the_two_refusals(oh):
    r = oh.proof_issue(tenant=MAYA, amount=620_000, period=202610, note='October')
    assert 'error' not in r, r
    assert r['kind'] == 'attested' and r['version'] == 1 and r['property_id'] == 1
    assert r['period'] == 202610 and r['rent_due'] == 620_000 and len(r['outputs']) == 6
    assert r['program_hash'] == proofs.program_hash() and r['fact'] == proofs.fact(r['program_hash'], r['outputs'])
    assert not any(k in r for k in ('tenant', 'salt', 'amount')), 'the receipt names nobody'
    assert MAYA.lower() not in json.dumps(r).lower()

    v = oh.proof_verify(nullifier=r['nullifier'])
    assert v['ok'], v
    assert v['known_here'] and oh.proof_verify(receipt=r)['ok']
    assert {c['claim'] for c in v['checks']} >= {'6 public outputs', 'statement version 1',
                                                 'program hash is the one this node runs',
                                                 'nothing about the tenant in the receipt'}

    salt = oh.proof_salt(MAYA)['salt']
    assert oh.proof_open(MAYA, salt, nullifier=r['nullifier'])['matches']
    assert not oh.proof_open(MAYA, '0x1', nullifier=r['nullifier'])['matches']
    assert not oh.proof_open(JONAH, salt, receipt=r)['matches']
    assert oh.proof_salt(MAYA)['salt'] == salt, 'one salt per tenant per node'

    again = oh.proof_issue(tenant=MAYA, amount=620_000, period=202610)
    assert 'already issued' in again['error']
    short = oh.proof_issue(tenant=JONAH, amount=619_999, period=202610)
    assert 'paid less than rent due' in short['error']
    assert len(oh.proofs()) == 1 and oh.proofs(period=202611) == []
    got = oh.proof(r['nullifier'])
    assert got['nullifier'] == r['nullifier']
    assert got['chains']['near']['configured'] is False or 'paid' in got['chains']['near'] or 'error' in got['chains']['near']
    assert 'error' in oh.proof('0x1')


@needs_scarb
def test_next_month_is_another_nullifier_same_commitment(oh):
    a = oh.proof_issue(tenant=MAYA, amount=700_000, period=202610)
    b = oh.proof_issue(tenant=MAYA, amount=620_000, period=202611)
    assert a['commitment'] == b['commitment'] and a['nullifier'] != b['nullifier']
    assert a['rent_due'] == b['rent_due'] == 620_000, 'the rent due is the public term, not what was paid'


@needs_scarb
def test_tampered_receipt_fails_offline(oh):
    r = oh.proof_issue(tenant=MAYA, amount=620_000, period=202610)
    t = json.loads(json.dumps(r))
    t['outputs'][3] = proofs.hex32(1)
    t['rent_due'] = 1
    v = oh.proof_verify(receipt=t)
    assert not v['ok']
    bad = {c['claim'] for c in v['checks'] if not c['ok']}
    assert any('fact' in c for c in bad) and any('signature' in c for c in bad)
    leaky = dict(r, tenant=MAYA)
    assert not oh.proof_verify(receipt=leaky)['ok']


@needs_scarb
def test_reconcile_issues_a_receipt_per_booking(oh):
    oh.bank_connect(kind='sandbox', name='landlord', config={'currency': 'USD', 'holder': 'DEMO Landlord'})
    ref = oh.bank_reference(MAYA)['reference']
    oh.bank_link(MAYA, payer_name='Maya Ortiz')
    oh.bank_receive(1550, reference=f'Rent October {ref}', from_name='Maya Ortiz',
                    from_iban='XT279876543210987654')
    rec = oh.bank_reconcile(rate=2500, issue_proofs=True)
    assert len(rec['booked']) == 1, rec
    receipts = oh.proofs()
    assert len(receipts) == 1
    r = receipts[0]
    assert r['rent_due'] == 620_000 and r['source']['txn'] and 'fiat' not in r['source']
    assert MAYA.lower() not in json.dumps(r).lower()
    assert oh.bank_reconcile(rate=2500, issue_proofs=True)['booked'] == [], 'idempotent'
    assert len(oh.proofs()) == 1


def test_short_bank_payment_books_but_has_no_proof(oh):
    if not proofs.scarb():
        pytest.skip('scarb not installed')
    oh.bank_connect(kind='sandbox', name='landlord', config={'currency': 'USD', 'holder': 'L'})
    ref = oh.bank_reference(MAYA)['reference']
    oh.bank_link(MAYA, payer_name='Maya Ortiz')
    oh.bank_receive(1000, reference=f'partial {ref}', from_name='Maya Ortiz', from_iban='XT279876543210987654')
    rec = oh.bank_reconcile(rate=2500, issue_proofs=True)
    assert len(rec['booked']) == 1 and len(oh.rent_ledger(MAYA)) == 1, 'the money is still booked'
    assert oh.proofs() == [], 'but a short month has no proof'


def test_gates_outside_simulation(oh):
    oh.network = 'mainnet'
    assert 'key=' in oh.proof_salt(MAYA)['error']
    assert 'key=' in oh.proof_issue(tenant=MAYA, amount=1)['error']
    assert 'tenant' in oh.proof_open('', '', nullifier='0x1')['error']
    assert 'no such receipt' in oh.proof_submit('0x1', chain='near')['error']


def test_status_and_program_surface(oh):
    st = oh.proof_status()
    assert st['property_id'] == 1 and st['rent_due'] == 620_000
    assert st['issuer']['near'].startswith('ed25519:') and st['issuer']['eth'].startswith('0x')
    assert set(st['chains']) == {'near', 'eth'} and st['receipts'] == 0
    prog = oh.proof_program()
    assert 'fn main(args: Array<felt252>)' in prog['source']
    assert prog['public_outputs'] == ['version', 'property_id', 'period', 'rent_due', 'commitment', 'nullifier']


# ── MCP + REST ──────────────────────────────────────────────────

@needs_scarb
def test_mcp_tools(oh):
    names = {'openhouse_proof_status', 'openhouse_proof_program', 'openhouse_proofs', 'openhouse_proof',
             'openhouse_proof_issue', 'openhouse_proof_verify', 'openhouse_proof_open',
             'openhouse_proof_salt', 'openhouse_proof_submit'}
    assert names <= set(TOOLS)
    assert 'issue_proofs' in TOOLS['openhouse_bank_reconcile']['inputSchema']['properties']
    assert TOOLS['openhouse_proof_issue']['description'].startswith('WRITES')
    call = lambda t, **a: TOOLS[t]['handler'](a, oh)
    assert 'program' in call('openhouse_proof_status')
    r = call('openhouse_proof_issue', tenant=MAYA, amount=620000, period=202610)
    assert call('openhouse_proof_verify', nullifier=r['nullifier'])['ok']
    assert call('openhouse_proof_verify', receipt=json.dumps(r))['ok']
    salt = call('openhouse_proof_salt', tenant=MAYA)['salt']
    assert call('openhouse_proof_open', tenant=MAYA, salt=salt, nullifier=r['nullifier'])['matches']
    assert len(call('openhouse_proofs')['receipts']) == 1
    assert call('openhouse_proof', nullifier=r['nullifier'])['period'] == 202610
    from mcp_server import ToolError
    with pytest.raises(ToolError):
        call('openhouse_proof_issue', tenant=MAYA, amount=1, period=202611)
    with pytest.raises(ToolError):
        call('openhouse_proof_verify')


@needs_scarb
def test_rest_routes(oh, monkeypatch):
    api = _load('openhouse_api_under_test', MODULE_DIR / 'api' / 'api.py')
    monkeypatch.setattr(api, '_openhouse', oh)
    c = TestClient(api.app)
    assert c.get('/proofs/status').json()['property_id'] == 1
    assert 'source' in c.get('/proofs/program').json()
    assert c.get('/proofs').json() == []
    r = c.post('/proofs/issue', json={'tenant': MAYA, 'amount': 620000, 'period': 202610})
    assert r.status_code == 200, r.text
    n = r.json()['nullifier']
    assert c.post('/proofs/issue', json={'tenant': MAYA, 'amount': 1, 'period': 202611}).status_code == 400
    assert len(c.get('/proofs').json()) == 1 and c.get('/proofs?period=202611').json() == []
    assert c.get(f'/proofs/{n}').status_code == 200
    assert c.get('/proofs/0x1').status_code == 404
    assert c.post('/proofs/verify', json={'nullifier': n}).json()['ok']
    salt = c.post('/proofs/salt', json={'tenant': MAYA}).json()['salt']
    assert c.post('/proofs/open', json={'tenant': MAYA, 'salt': salt, 'nullifier': n}).json()['matches']
    assert c.post('/proofs/submit', json={'nullifier': '0x1'}).status_code == 400
    oh.network = 'mainnet'
    assert c.post('/proofs/salt', json={'tenant': MAYA}).status_code == 403
