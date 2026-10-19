"""
The simulation must play the deal through the real code paths — the same
splits, the same refusals — in a throwaway store, deterministically, and
never touch the live store.
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

MODULE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(MODULE_DIR.parent.parent.parent))
sys.path.insert(0, str(MODULE_DIR / 'api'))

from mcp_server import TOOLS, build_router  # noqa: E402


def _load(name, path):
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


openhouse_mod = _load('openhouse_under_test', MODULE_DIR / 'mod.py')


@pytest.fixture
def oh(tmp_path):
    return openhouse_mod.Mod(store=tmp_path / 'store')


def test_default_run_plays_the_real_split(oh):
    r = oh.simulate(years=2)
    assert r['ok'] and r['params']['years'] == 2 and len(r['months']) == 24
    m1 = r['months'][0]
    # 0.62 Ξ at 2.5% fee, full credit: the same numbers quote() gives
    assert m1['paid'] == 0.62 and m1['fees'] == 0.0155 and m1['credit'] == 0.6045
    assert r['months'][-1]['equity'] == pytest.approx(0.6045 * 24, abs=1e-6)
    assert r['end']['to_property_pct'] == 97.5
    # the bank path: every month went through the sandbox and the reconciler
    assert r['bank']['booked'] == 24 and r['params']['bank'] is True
    assert r['months'][0]['paid_by'] == ['maya']
    # quarters close every ~90 days and split the fee by bloctime
    assert r['end']['quarters_closed'] >= 7
    q = r['quarters'][0]
    assert q['pool'] > 0 and {a['who'] for a in q['allocations']} == {'owner', 'maya'}
    assert sum(a['share_pct'] for a in q['allocations']) == pytest.approx(100, abs=1e-3)


def test_pays_off_and_stops(oh):
    r = oh.simulate(years=20, bank=False)
    e = r['end']
    assert e['owned'] and e['owned_month'] == 199 and e['equity'] == 120.0
    assert len(r['months']) == 199, 'the run stops the month the ledger refuses'
    assert r['events'][-1]['kind'] == 'owned'
    assert e['owner_income'] > 0, 'the last partial month spills to owner income, not past the price'


def test_zero_fee_has_no_quarters(oh):
    r = oh.simulate(years=3, fee_pct=0)
    assert r['end']['fees'] == 0 and r['end']['quarters_closed'] == 0
    assert all(m['owner_income'] == 0 for m in r['months'])


def test_housemates_split_pro_rata(oh):
    r = oh.simulate(years=1, renters=3)
    eq = {x['who']: x['principal'] for x in r['end']['renters']}
    assert set(eq) == {'maya', 'jonah', 'ada'}
    assert max(eq.values()) == pytest.approx(min(eq.values()), abs=1e-6)
    assert len(r['cast']) == 4 and all(c['reference'] for c in r['cast'][:3])


def test_lease_option_and_skips_are_deterministic(oh):
    a = oh.simulate(years=2, model='classic', skip_every=6, seed='x')
    b = oh.simulate(years=2, model='classic', skip_every=6, seed='x')
    assert a['months'] == b['months'] and a['events'] == b['events']
    assert a['events'][0]['kind'] == 'option'
    assert any(e['kind'] == 'skipped' for e in a['events'])
    assert a['params']['credit_pct'] == 25.0 and a['params']['option_fee_pct'] == 3.0
    skipped = [m for m in a['months'] if m['skipped']]
    assert skipped and all(m['credit'] == 0 for m in skipped)


def test_clamps_not_refuses(oh):
    r = oh.simulate(years=999, fee_pct=50, renters=9, model='nonsense')
    p = r['params']
    assert p['years'] == 40 and p['fee_pct'] == 5.0 and p['renters'] == 4 and p['model'] == 'full_credit'


def test_live_store_untouched(oh):
    oh.simulate(years=1)
    assert oh.rent_ledger() == [] and oh.terms()['owner'] == ''
    assert not (oh.store_dir / 'rent.json').exists()


def test_presets_run(oh):
    ids = [p['id'] for p in oh.simulate_presets()]
    assert 'first_home' in ids and 'housemates' in ids
    p = next(x for x in oh.simulate_presets() if x['id'] == 'plain_lease')
    r = oh.simulate(**{k: v for k, v in p.items() if k not in ('id', 'title', 'shows')})
    assert r['end']['equity'] == 0 and r['end']['owner_income'] > 0


def test_mcp_and_rest(oh, monkeypatch):
    assert 'openhouse_simulate' in TOOLS and 'openhouse_simulate_presets' in TOOLS
    out = TOOLS['openhouse_simulate']['handler']({'preset': 'zero_fee', 'years': 1}, oh)
    assert out['end']['fees'] == 0 and isinstance(out['months'], str)
    out = TOOLS['openhouse_simulate']['handler']({'years': 1, 'months': True}, oh)
    assert len(out['months']) == 12
    app = FastAPI()
    app.include_router(build_router(lambda: oh, '9.9.9'))
    c = TestClient(app)
    r = c.post('/mcp', json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                             'params': {'name': 'openhouse_simulate_presets', 'arguments': {}}})
    assert len(json.loads(r.json()['result']['content'][0]['text'])['presets']) >= 7

    api = _load('openhouse_api_under_test', MODULE_DIR / 'api' / 'api.py')
    monkeypatch.setattr(api, '_openhouse', oh)
    c = TestClient(api.app)
    assert c.get('/simulate/presets').status_code == 200
    g = c.get('/simulate', params={'years': 1, 'bank': 'false'}).json()
    assert len(g['months']) == 12 and g['bank'] is None
    p = c.post('/simulate', json={'years': 1, 'renters': 2}).json()
    assert p['bank']['booked'] == 24
