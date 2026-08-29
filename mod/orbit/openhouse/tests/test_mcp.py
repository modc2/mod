"""
Tests for the openhouse MCP endpoint.

Every test drives a router built over a Mod whose store lives in a tmp HOME,
so the write tools (pay_rent, set_terms, purchase) can be exercised for real
without touching ~/.openhouse.
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

MODULE_DIR = Path(__file__).resolve().parent.parent
MOD_ROOT = MODULE_DIR.parent.parent.parent          # /root/mod — for `import mod`
sys.path.insert(0, str(MOD_ROOT))
sys.path.insert(0, str(MODULE_DIR / 'api'))

from mcp_server import TOOLS, build_router  # noqa: E402


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


openhouse_mod = _load('openhouse_under_test', MODULE_DIR / 'mod.py')


@pytest.fixture
def oh(tmp_path, monkeypatch):
    """A Mod whose ~/.openhouse store is a throwaway directory."""
    monkeypatch.setenv('HOME', str(tmp_path))
    return openhouse_mod.Mod()


@pytest.fixture
def client(oh):
    app = FastAPI()
    app.include_router(build_router(lambda: oh, '9.9.9'))
    return TestClient(app)


def rpc(client, method, params=None, id_=1):
    body = {'jsonrpc': '2.0', 'id': id_, 'method': method}
    if params is not None:
        body['params'] = params
    return client.post('/mcp', json=body)


def call(client, tool, **arguments):
    """tools/call → the tool result object (content + isError)."""
    r = rpc(client, 'tools/call', {'name': tool, 'arguments': arguments})
    assert r.status_code == 200, r.text
    payload = r.json()
    assert 'error' not in payload, payload['error']
    return payload['result']


def data(result):
    """The tool's JSON payload, decoded from its text content block."""
    assert result['isError'] is False, result['content'][0]['text']
    return json.loads(result['content'][0]['text'])


def errtext(result):
    assert result['isError'] is True, result
    return result['content'][0]['text']


# ── handshake ───────────────────────────────────────────────────

def test_initialize_echoes_a_supported_version(client):
    body = rpc(client, 'initialize', {'protocolVersion': '2025-06-18'}).json()
    assert body['result']['protocolVersion'] == '2025-06-18'
    assert body['result']['serverInfo'] == {'name': 'openhouse', 'version': '9.9.9'}
    assert 'rent-to-own' in body['result']['instructions']
    assert body['result']['capabilities']['tools'] == {}


def test_initialize_falls_back_on_an_unknown_version(client):
    body = rpc(client, 'initialize', {'protocolVersion': '1999-01-01'}).json()
    assert body['result']['protocolVersion'] == '2025-03-26'


def test_ping(client):
    assert rpc(client, 'ping').json()['result'] == {}


def test_get_is_not_a_transport(client):
    assert client.get('/mcp').status_code == 405


def test_notifications_get_an_empty_202(client):
    assert client.post('/mcp', json={'jsonrpc': '2.0',
                                     'method': 'notifications/initialized'}).status_code == 202
    # no id = a notification too, whatever the method
    assert client.post('/mcp', json={'jsonrpc': '2.0', 'method': 'ping'}).status_code == 202


# ── protocol errors ─────────────────────────────────────────────

def test_malformed_json_is_a_parse_error(client):
    r = client.post('/mcp', content=b'{not json')
    assert r.status_code == 400
    assert r.json()['error']['code'] == -32700


def test_a_body_without_a_method_is_invalid(client):
    r = client.post('/mcp', json={'jsonrpc': '2.0', 'id': 7})
    assert r.status_code == 400
    assert r.json()['error'] == {'code': -32600, 'message': r.json()['error']['message']}
    assert r.json()['id'] == 7


def test_unknown_method(client):
    assert rpc(client, 'tools/enumerate').json()['error']['code'] == -32601


def test_unknown_tool(client):
    r = rpc(client, 'tools/call', {'name': 'openhouse_teleport', 'arguments': {}})
    assert r.json()['error']['code'] == -32602


def test_non_object_arguments_are_rejected(client):
    r = rpc(client, 'tools/call', {'name': 'openhouse_terms', 'arguments': [1, 2]})
    assert r.json()['error']['code'] == -32602


# ── the catalogue ───────────────────────────────────────────────

def test_tools_list_is_well_formed(client):
    tools = rpc(client, 'tools/list').json()['result']['tools']
    assert {t['name'] for t in tools} == set(TOOLS)
    for t in tools:
        assert t['name'].startswith('openhouse_')
        assert t['description']
        schema = t['inputSchema']
        assert schema['type'] == 'object'
        # every required field must actually be described
        for req in schema.get('required', []):
            assert req in schema['properties'], f"{t['name']}: {req} undocumented"


def test_write_tools_announce_themselves(client):
    tools = {t['name']: t['description'] for t in rpc(client, 'tools/list').json()['result']['tools']}
    for name in ('openhouse_pay_rent', 'openhouse_purchase',
                 'openhouse_set_terms', 'openhouse_claim_owner'):
        assert tools[name].startswith('WRITES.'), name
    assert not tools['openhouse_terms'].startswith('WRITES.')


# ── reads ───────────────────────────────────────────────────────

def test_terms_returns_the_live_deal(client):
    t = data(call(client, 'openhouse_terms'))
    assert 1 <= t['fee_pct'] <= 5
    assert t['fee_band'] == {'min_pct': 1.0, 'max_pct': 5.0}
    # the three legs of a payment account for the whole of it
    assert round(t['fee_pct'] + t['equity_pct_of_rent'] + t['owner_pct_of_rent'], 6) == 100.0


def test_structured_content_mirrors_the_text_block(client):
    result = call(client, 'openhouse_terms')
    assert result['structuredContent'] == json.loads(result['content'][0]['text'])


def test_quote_splits_a_payment_without_recording_it(client):
    q = data(call(client, 'openhouse_quote', amount=1))
    assert round(q['fee'] + q['credit'] + q['owner_income'], 8) == 1.0
    assert data(call(client, 'openhouse_rent_stats'))['payments'] == 0


def test_an_option_payment_is_all_equity(client):
    q = data(call(client, 'openhouse_quote', amount=2, kind='option'))
    assert q['credit_pct'] == 100.0
    assert round(q['credit'], 8) == round(2 - q['fee'], 8)


def test_quote_needs_an_amount(client):
    assert 'amount required' in errtext(call(client, 'openhouse_quote'))


def test_quote_rejects_a_non_number(client):
    assert 'must be a number' in errtext(call(client, 'openhouse_quote', amount='soon'))


def test_quote_rejects_zero(client):
    assert 'greater than 0' in errtext(call(client, 'openhouse_quote', amount=0))


def test_status_reports_honest_emptiness(client):
    s = data(call(client, 'openhouse_status'))
    assert s['deployed'] is False
    assert s['shareholders'] == 0 and s['shares_sold'] == 0
    assert data(call(client, 'openhouse_property'))['deployed'] is False


def test_models_carry_the_fee_band_and_benchmarks(client):
    m_ = data(call(client, 'openhouse_models'))
    assert {x['id'] for x in m_['models']} >= {'full_credit', 'lease'}
    assert m_['fee_band']['max_pct'] == 5.0
    assert m_['benchmarks']


def test_source_lists_the_manifest_then_one_file(client):
    manifest = data(call(client, 'openhouse_source'))['files']
    names = [f['name'] for f in manifest]
    assert 'contracts/OpenHouse.sol' in names
    # the manifest stays small — no file bodies until asked for by name
    assert all('content' not in f for f in manifest)

    f = data(call(client, 'openhouse_source', name='OpenHouse.sol'))
    assert f['language'] == 'solidity'
    assert 'contract OpenHouse' in f['content']


def test_source_says_what_it_has_when_asked_for_something_else(client):
    msg = errtext(call(client, 'openhouse_source', name='secrets.env'))
    assert 'no such source file' in msg and 'mod.py' in msg


# ── writes ──────────────────────────────────────────────────────

def test_pay_rent_records_the_split_and_moves_equity(client):
    call(client, 'openhouse_set_terms', home_price=100, monthly_rent=2, fee_pct=2.5)
    paid = data(call(client, 'openhouse_pay_rent', renter='0xrenter', amount=2))
    assert paid['success'] is True
    assert round(paid['fee'], 8) == 0.05          # 2.5% of 2

    eq = data(call(client, 'openhouse_equity', address='0xrenter'))
    assert eq['payments'] == 1
    assert round(eq['principal'], 8) == round(paid['credit'], 8)
    assert eq['fully_owned'] is False

    stats = data(call(client, 'openhouse_rent_stats'))
    assert stats['payments'] == 1 and stats['renters'] == 1
    assert round(stats['to_property_pct'], 4) == 97.5


def test_pay_rent_needs_a_renter(client):
    assert 'renter required' in errtext(call(client, 'openhouse_pay_rent', amount=1))


def test_rent_ledger_filters_and_limits(client):
    for i in range(3):
        call(client, 'openhouse_pay_rent', renter='0xa', amount=1)
    call(client, 'openhouse_pay_rent', renter='0xb', amount=1)

    assert data(call(client, 'openhouse_rent_ledger'))['payments'] == 4
    mine = data(call(client, 'openhouse_rent_ledger', renter='0xA'))
    assert mine['payments'] == 3                  # address match is case-insensitive
    assert len(data(call(client, 'openhouse_rent_ledger', limit=2))['ledger']) == 2


def test_set_terms_holds_the_fee_band(client):
    assert 'between 1.0% and 5.0%' in errtext(call(client, 'openhouse_set_terms', fee_pct=12))
    assert 'between 0% and 100%' in errtext(call(client, 'openhouse_set_terms', credit_pct=140))


def test_set_terms_needs_something_to_set(client):
    assert 'nothing to set' in errtext(call(client, 'openhouse_set_terms'))


def test_a_preset_moves_the_dials(client):
    t = data(call(client, 'openhouse_set_terms', model='lease'))['terms']
    assert t['model'] == 'lease' and t['credit_pct'] == 0
    assert t['equity_pct_of_rent'] == 0


def test_once_an_owner_is_recorded_only_they_can_set_terms(client):
    data(call(client, 'openhouse_claim_owner', address='0xowner'))
    assert 'Only the property owner' in errtext(
        call(client, 'openhouse_set_terms', fee_pct=3, owner='0xsomeone'))
    assert data(call(client, 'openhouse_set_terms', fee_pct=3, owner='0xowner'))['terms']['fee_pct'] == 3


def test_the_owner_seat_is_claimed_once(client):
    call(client, 'openhouse_claim_owner', address='0xowner')
    assert 'Owner already set' in errtext(call(client, 'openhouse_claim_owner', address='0xother'))


def test_purchase_needs_a_deployed_property(client):
    assert errtext(call(client, 'openhouse_purchase', buyer='0xb', share_count=10))
    assert data(call(client, 'openhouse_shareholders'))['count'] == 0
