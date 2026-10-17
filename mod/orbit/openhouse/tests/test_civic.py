"""
Tests for the civic seat — both halves.

The property-node half (mod.py): charter, override, resign, and the pause
gate on money. The government's half (civic/server.py): identity, watching,
the ledger audit that recomputes every split from scratch, and overrides
pushed from the city's own server. Every store lives in a tmp HOME.
"""
import importlib.util
import json
import sys
import urllib.error
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

MODULE_DIR = Path(__file__).resolve().parent.parent
MOD_ROOT = MODULE_DIR.parent.parent.parent
sys.path.insert(0, str(MOD_ROOT))
sys.path.insert(0, str(MODULE_DIR / 'api'))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


openhouse_mod = _load('openhouse_civic_under_test', MODULE_DIR / 'mod.py')
civic_server = _load('civic_server_under_test', MODULE_DIR / 'civic' / 'server.py')

OWNER = '0x00000000000000000000000000000000000000AA'
CITY_KEY = '0x00000000000000000000000000000000000000C1'
RENTER = '0x00000000000000000000000000000000000000FE'


@pytest.fixture
def oh(tmp_path, monkeypatch):
    """A Mod whose ~/.openhouse store is a throwaway directory."""
    monkeypatch.setenv('HOME', str(tmp_path))
    return openhouse_mod.Mod()


def _charter(oh):
    oh.claim_owner(OWNER)
    return oh.civic_charter(CITY_KEY, name='Cleveland Housing Authority',
                            region='US-OH', uri='https://housing.cleveland.gov',
                            owner=OWNER)


# ═══════════════════════════════════ The property node ═════════

def test_unchartered_seat_is_empty(oh):
    c = oh.civic()
    assert c['chartered'] is False
    assert c['authority'] is None
    assert c['civic_paused'] is False
    assert c['civic_hold'] is False
    assert c['overrides'] == []


def test_owner_charters_once(oh):
    res = _charter(oh)
    assert res['success'] and res['authority']['name'] == 'Cleveland Housing Authority'
    # The seat is taken — even the owner cannot re-charter over the city.
    again = oh.civic_charter('0xE1', owner=OWNER)
    assert 'error' in again and 'taken' in again['error']


def test_charter_is_owner_gated(oh):
    oh.claim_owner(OWNER)
    res = oh.civic_charter(CITY_KEY, owner='0xNOTOWNER')
    assert 'error' in res and 'owner' in res['error']
    assert oh.civic()['chartered'] is False


def test_override_requires_the_chartered_key(oh):
    assert 'error' in oh.civic_override('pause', CITY_KEY)  # nobody chartered yet
    _charter(oh)
    assert 'error' in oh.civic_override('pause', '0xIMPOSTOR')
    assert 'error' in oh.civic_override('confiscate', CITY_KEY)  # no such power
    ok = oh.civic_override('pause', CITY_KEY, reason='servicer stopped reporting')
    assert ok['success'] and ok['civic_paused'] is True


def test_civic_pause_freezes_money_and_lifts_clean(oh):
    _charter(oh)
    oh.set_terms(home_price=100.0, monthly_rent=1.0, owner=OWNER)
    assert oh.pay_rent(RENTER, 1.0)['success']

    oh.civic_override('pause', CITY_KEY, reason='audit running')
    blocked = oh.pay_rent(RENTER, 1.0)
    assert 'Civic pause' in blocked['error']
    assert 'Cleveland' in blocked['error']
    assert 'Civic pause' in oh.purchase('0xB0', 1).get('error', '')

    oh.civic_override('unpause', CITY_KEY)
    assert oh.pay_rent(RENTER, 1.0)['success']


def test_hold_resign_and_recharter(oh):
    _charter(oh)
    oh.civic_override('pause', CITY_KEY)
    oh.civic_override('hold', CITY_KEY, reason='vacancy complaint')
    c = oh.civic()
    assert c['civic_paused'] and c['civic_hold']

    res = oh.civic_resign(CITY_KEY)
    assert res['success']
    c = oh.civic()
    # A departed city holds nothing: seat empty, flags lifted.
    assert c['chartered'] is False and not c['civic_paused'] and not c['civic_hold']
    # And the record shows the whole history, resign included.
    assert [o['action'] for o in c['overrides']][0] == 'resign'

    # The seat is genuinely open again.
    assert oh.civic_charter('0xE2', name='State of Ohio', owner=OWNER)['success']


def test_mcp_civic_tool(oh):
    from mcp_server import build_router
    app = FastAPI()
    app.include_router(build_router(lambda: oh, '9.9.9'))
    client = TestClient(app)
    _charter(oh)
    r = client.post('/mcp', json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                                  'params': {'name': 'openhouse_civic', 'arguments': {}}})
    result = r.json()['result']
    assert result['isError'] is False
    payload = json.loads(result['content'][0]['text'])
    assert payload['chartered'] is True
    assert payload['authority']['region'] == 'US-OH'


# ═══════════════════════════════════ The city's server ═════════

UPSTREAM = 'http://fake:1'


@pytest.fixture
def city(tmp_path, monkeypatch):
    """The civic server, its store in the same throwaway HOME."""
    monkeypatch.setenv('HOME', str(tmp_path))
    return TestClient(civic_server.app)


def _wire(monkeypatch, oh):
    """Stand the Mod in as the property node the city server talks to."""
    def fetch(url, payload=None, timeout=10.0):
        path = url.split('fake:1', 1)[1]
        if path == '/terms':
            return oh.terms()
        if path == '/rent_ledger':
            return oh.rent_ledger()
        if path == '/rent_stats':
            return oh.rent_stats()
        if path == '/civic/override':
            res = oh.civic_override(payload['action'], payload['key'],
                                    payload.get('reason', ''))
            if 'error' in res:
                raise urllib.error.URLError(res['error'])
            return res
        raise AssertionError(f'unexpected fetch: {url}')
    monkeypatch.setattr(civic_server, '_fetch', fetch)


def test_city_identity_and_key(city):
    c = city.get('/city').json()
    assert c['key'].startswith('0x') and len(c['key']) == 42
    r = city.post('/city', json={'name': 'Cleveland Housing Authority',
                                 'region': 'US-OH',
                                 'uri': 'https://housing.cleveland.gov'}).json()
    assert r['name'] == 'Cleveland Housing Authority'
    # The key survives an identity update — it is the server's standing.
    assert r['key'] == c['key']


def test_watching(city):
    assert city.post('/watch', json={'upstream': UPSTREAM, 'note': 'pilot'}).json()['watches'] == 1
    assert city.post('/watch', json={'upstream': UPSTREAM}).json()['already'] is True
    assert len(city.get('/watches').json()) == 1


def test_verify_clean_ledger(city, oh, monkeypatch):
    _wire(monkeypatch, oh)
    oh.claim_owner(OWNER)
    # A small house so the equity clamp path runs: the second payment is
    # clamped at the price, and the audit must agree with the clamp.
    oh.set_terms(home_price=100.0, monthly_rent=60.0, fee_pct=2.0, owner=OWNER)
    oh.pay_rent(RENTER, 60.0)
    oh.pay_rent(RENTER, 60.0, kind='option')

    report = city.get('/verify', params={'upstream': UPSTREAM}).json()
    assert report['ok'] is True, report['findings']
    assert report['payments'] == 2
    assert len(city.get('/verifications').json()) == 1


def test_verify_catches_a_cooked_ledger(city, oh, monkeypatch):
    _wire(monkeypatch, oh)
    oh.claim_owner(OWNER)
    oh.set_terms(home_price=1000.0, monthly_rent=10.0, owner=OWNER)
    oh.pay_rent(RENTER, 10.0)
    oh.pay_rent(RENTER, 10.0)

    # The node quietly moves a renter's equity into the owner's pocket.
    ledger = oh._load_rent()
    ledger[1]['credit'] -= 5.0
    ledger[1]['owner_income'] += 5.0
    oh._save_rent(ledger)

    report = city.get('/verify', params={'upstream': UPSTREAM}).json()
    assert report['ok'] is False
    fields = {f['field'] for f in report['findings']}
    assert 'credit' in fields and 'owner_income' in fields
    assert all(f['entry'] in (1, None) for f in report['findings'])


def test_verify_catches_a_fee_outside_the_band(city, oh, monkeypatch):
    _wire(monkeypatch, oh)
    oh.claim_owner(OWNER)
    oh.set_terms(home_price=1000.0, owner=OWNER)
    oh.pay_rent(RENTER, 10.0)
    # A node that recorded a 12% take — impossible under the contract's band.
    ledger = oh._load_rent()
    ledger[0]['fee_pct'] = 12.0
    ledger[0]['fee'] = 1.2
    ledger[0]['credit'] = 8.8
    ledger[0]['owner_income'] = 0.0
    oh._save_rent(ledger)

    report = city.get('/verify', params={'upstream': UPSTREAM}).json()
    assert report['ok'] is False
    assert any(f['field'] == 'fee_pct' for f in report['findings'])


def test_override_from_the_citys_own_server(city, oh, monkeypatch):
    _wire(monkeypatch, oh)
    # The property owner charters the SERVER's key — the city acts as its box.
    server_key = city.get('/city').json()['key']
    oh.claim_owner(OWNER)
    oh.set_terms(home_price=100.0, owner=OWNER)
    oh.civic_charter(server_key, name='Cleveland Housing Authority', owner=OWNER)

    r = city.post('/override', json={'upstream': UPSTREAM, 'action': 'pause',
                                     'reason': 'ledger audit failed'}).json()
    assert r['success'] is True
    # The freeze landed on the property node, from the city's server.
    assert oh.civic()['civic_paused'] is True
    assert 'Civic pause' in oh.pay_rent(RENTER, 1.0)['error']
    assert len(city.get('/overrides').json()) == 1

    city.post('/override', json={'upstream': UPSTREAM, 'action': 'unpause'})
    assert oh.civic()['civic_paused'] is False


def test_override_without_standing_is_refused(city, oh, monkeypatch):
    _wire(monkeypatch, oh)
    # Nobody chartered this server's key: the node refuses, the server reports it.
    r = city.post('/override', json={'upstream': UPSTREAM, 'action': 'pause'})
    assert r.status_code == 400
    assert city.get('/overrides').json() == []


def test_unknown_action_is_refused_locally(city):
    r = city.post('/override', json={'upstream': UPSTREAM, 'action': 'seize'})
    assert r.status_code == 400
