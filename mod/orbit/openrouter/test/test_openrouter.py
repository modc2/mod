"""openrouter tests — the parts that must be right before any money moves.

Everything here runs offline except the two tests marked `live`, which hit the
public catalog (no key, no spend) and skip themselves if the network is not
there. Nothing in this file can make a paid call: `chat` is only ever exercised
through `_chat_payload`, which builds the request without sending it.
"""

import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import client as C      # noqa: E402
import mcp              # noqa: E402
from client import Client, Filters, ORError, card, per_m  # noqa: E402


def live(fn):
    """Runs against openrouter.ai; skips rather than fails when offline."""
    return pytest.mark.live(fn)


MODEL = {
    'id': 'acme/fast', 'name': 'Acme Fast', 'context_length': 128000,
    'created': 1700000000,
    'architecture': {'modality': 'text+image->text', 'input_modalities': ['text', 'image'],
                     'output_modalities': ['text']},
    'pricing': {'prompt': '0.0000005', 'completion': '0.0000015', 'request': '0',
                'image': '0.001'},
    'top_provider': {'context_length': 128000, 'max_completion_tokens': 8192,
                     'is_moderated': False},
    'supported_parameters': ['tools', 'temperature', 'structured_outputs'],
}
FREE = {**MODEL, 'id': 'acme/free', 'pricing': {'prompt': '0', 'completion': '0'},
        'supported_parameters': []}
# A $0 prompt with a priced completion: a loss leader, not a free model.
BAIT = {**MODEL, 'id': 'acme/bait', 'pricing': {'prompt': '0', 'completion': '0.000002'}}
# What OpenRouter really returns for openrouter/auto: -1 is not a price, it is
# "depends on the model this lands on".
ROUTER = {**MODEL, 'id': 'acme/auto', 'pricing': {'prompt': '-1', 'completion': '-1'}}


# ── prices ───────────────────────────────────────────────────────────────

def test_prices_are_per_million_tokens():
    p = per_m(MODEL['pricing'])
    assert p['prompt_usd_m'] == 0.5 and p['completion_usd_m'] == 1.5
    # per-call fields keep their own units and never get an _m suffix
    assert p['per_image_usd'] == 0.001 and 'per_request_usd' not in p


def test_free_means_free_both_ways():
    assert card(FREE)['free'] is True
    assert card(BAIT)['free'] is False
    assert card(MODEL)['free'] is False


def test_card_reads_capabilities_from_supported_parameters():
    c = card(MODEL)
    assert c['tools'] is True and c['structured'] is True and c['reasoning'] is False
    assert c['context'] == 128000 and c['max_output'] == 8192
    assert c['input'] == ['text', 'image']


def test_missing_pricing_does_not_crash_or_lie():
    c = card({'id': 'x/y'})
    assert c['prompt_usd_m'] is None and c['free'] is False


def test_router_sentinel_is_unknown_not_minus_a_million():
    """-1 read as a price makes the routers the cheapest models in the catalog."""
    c = card(ROUTER)
    assert c['prompt_usd_m'] is None and c['completion_usd_m'] is None
    assert c['free'] is False, 'unknown is not free'
    assert c['variable_price'] is True, 'and it must say why it is unknown'
    assert card(MODEL)['variable_price'] is False


def test_router_does_not_win_a_price_ceiling_it_cannot_be_checked_against():
    assert not Filters(max_prompt_usd_m=0.01).match(card(ROUTER))
    assert not Filters(max_completion_usd_m=0.01).match(card(ROUTER))


def test_router_sorts_last_not_first():
    rows = [card(m) for m in (ROUTER, MODEL, FREE)]
    assert [r['id'] for r in Filters(sort='price').order(rows)][-1] == 'acme/auto'


# ── filters ──────────────────────────────────────────────────────────────

def test_filter_matches_all_query_words_across_fields():
    c = card({**MODEL, 'description': 'a very quick model'})
    assert Filters(q='acme quick').match(c)
    assert not Filters(q='acme slow').match(c)


def test_filter_price_ceiling_excludes_unpriced_models():
    assert not Filters(max_prompt_usd_m=1).match(card({'id': 'x/y'}))
    assert Filters(max_prompt_usd_m=1).match(card(MODEL))
    assert not Filters(max_prompt_usd_m=0.1).match(card(MODEL))


def test_filter_booleans_accept_strings_from_query_strings():
    c = card(MODEL)
    assert Filters(tools='1').match(c) and Filters(tools='true').match(c)
    assert not Filters(tools='0').match(c)
    assert Filters(tools='').match(c), 'an empty filter must not filter'


def test_sort_puts_unpriced_models_last():
    rows = [card(m) for m in (MODEL, FREE, {'id': 'x/y'})]
    assert [r['id'] for r in Filters(sort='price').order(rows)] == \
        ['acme/free', 'acme/fast', 'x/y']


# ── chat payload (built, never sent) ─────────────────────────────────────

def test_prompt_and_system_become_a_message_array():
    p = Client(key='k')._chat_payload(model='a/b', prompt='hi', system='be brief')
    assert p['messages'] == [{'role': 'system', 'content': 'be brief'},
                             {'role': 'user', 'content': 'hi'}]
    assert p['usage'] == {'include': True}, 'cost must come back on the response'


def test_provider_string_is_sugar_for_order():
    p = Client(key='k')._chat_payload(model='a/b', prompt='hi', provider='groq, cerebras')
    assert p['provider'] == {'order': ['groq', 'cerebras']}


def test_provider_object_survives_and_typos_do_not():
    c = Client(key='k')
    p = c._chat_payload(model='a/b', prompt='hi',
                        provider={'only': 'anthropic', 'allow_fallbacks': False})
    assert p['provider'] == {'only': ['anthropic'], 'allow_fallbacks': False}
    with pytest.raises(ORError) as e:
        c._chat_payload(model='a/b', prompt='hi', provider={'oder': ['groq']})
    assert 'oder' in str(e.value)


def test_a_model_is_required_but_a_fallback_list_counts():
    c = Client(key='k')
    with pytest.raises(ORError):
        c._chat_payload(prompt='hi')
    assert c._chat_payload(prompt='hi', models='a/b,c/d')['models'] == ['a/b', 'c/d']


def test_empty_options_are_dropped_not_forwarded_as_null():
    p = Client(key='k')._chat_payload(model='a/b', prompt='hi', temperature=None,
                                      max_tokens='', seed=0)
    assert 'temperature' not in p and 'max_tokens' not in p
    assert p['seed'] == 0, 'a real zero is not the same as absent'


# ── spend guard ──────────────────────────────────────────────────────────

def test_guard_prices_the_worst_case_and_stops_it(monkeypatch):
    c = Client(key='k')
    monkeypatch.setattr(c, 'models', lambda refresh=False: [MODEL])
    payload = {'model': 'acme/fast', 'messages': [{'role': 'user', 'content': 'hi'}],
               'max_tokens': 1_000_000}
    guard = c._guard(payload, confirm=False)
    assert guard['needs_confirm'] and guard['estimate']['total_usd'] > C.SPEND_USD
    assert c._guard(payload, confirm=True) is None, 'confirm=true means go'
    assert c._guard({**payload, 'max_tokens': 100}, confirm=False) is None


def test_estimate_is_arithmetic_anyone_can_check(monkeypatch):
    c = Client(key='k')
    monkeypatch.setattr(c, 'models', lambda refresh=False: [MODEL])
    e = c.estimate('acme/fast', prompt_tokens=1_000_000, completion_tokens=1_000_000)
    assert e['prompt_usd'] == 0.5 and e['completion_usd'] == 1.5 and e['total_usd'] == 2.0


def test_a_router_quote_says_it_cannot_be_priced(monkeypatch):
    c = Client(key='k')
    monkeypatch.setattr(c, 'models', lambda refresh=False: [ROUTER])
    e = c.estimate('acme/auto', prompt_tokens=1000, completion_tokens=1000)
    assert e['total_usd'] is None and e['variable_price'] is True
    assert 'router' in e['note']
    # and it must not be silently ranked as the cheapest call money can buy
    assert c.cost(prompt_tokens=1000, completion_tokens=1000)['quotes'] == []


def test_cost_ranks_by_the_whole_call_not_the_prompt_price(monkeypatch):
    """The point of the ranking: cheap in, expensive out loses on a long answer."""
    cheap_in = {**MODEL, 'id': 'a/cheap-in',
                'pricing': {'prompt': '0.0000001', 'completion': '0.00001'}}
    even = {**MODEL, 'id': 'a/even',
            'pricing': {'prompt': '0.000001', 'completion': '0.000001'}}
    c = Client(key='k')
    monkeypatch.setattr(c, 'models', lambda refresh=False: [cheap_in, even])
    by_prompt = c.search(sort='price')['models'][0]['id']
    by_call = c.cost(prompt_tokens=100, completion_tokens=10_000)['quotes'][0]['model']
    assert by_prompt == 'a/cheap-in' and by_call == 'a/even'


# ── keys ─────────────────────────────────────────────────────────────────

def test_missing_key_raises_with_a_way_out(monkeypatch):
    monkeypatch.delenv('OPENROUTER_API_KEY', raising=False)
    monkeypatch.setattr(C, '_keystore', dict)
    with pytest.raises(C.NeedsKey) as e:
        Client().key()
    assert 'openrouter.ai/keys' in (e.value.hint or '')


def test_inference_key_is_not_a_provisioning_key(monkeypatch):
    monkeypatch.delenv('OPENROUTER_PROVISIONING_KEY', raising=False)
    monkeypatch.setattr(C, '_keystore', dict)
    with pytest.raises(C.NeedsKey):
        Client(key='sk-or-inference').provisioning_key()


def test_set_key_writes_0600_and_never_returns_the_secret(tmp_path, monkeypatch):
    path = tmp_path / 'key.json'
    monkeypatch.setattr(C, 'KEY_FILE', str(path))
    out = C.set_key(key='sk-or-v1-secret')
    assert 'sk-or-v1-secret' not in json.dumps(out)
    assert out['key'] == 'set'
    assert oct(path.stat().st_mode)[-3:] == '600'
    assert json.loads(path.read_text())['key'] == 'sk-or-v1-secret'
    C.set_key(provisioning_key='prov')                 # must not clobber the other
    assert json.loads(path.read_text())['key'] == 'sk-or-v1-secret'


def test_server_only_accepts_an_authorization_header_that_is_an_openrouter_key():
    import api
    assert api._keys_from({'authorization': 'Bearer sk-or-v1-abc'})['key'] == 'sk-or-v1-abc'
    # the gateway's own session bearer must not be forwarded upstream as a key
    assert api._keys_from({'authorization': 'Bearer eyJhbGciOi'})['key'] is None
    assert api._keys_from({'x-openrouter-key': 'sk-or-x'})['key'] == 'sk-or-x'


# ── mcp ──────────────────────────────────────────────────────────────────

def test_initialize_echoes_a_supported_protocol_version():
    r = mcp.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                    'params': {'protocolVersion': '2025-06-18'}})
    assert r['result']['protocolVersion'] == '2025-06-18'
    assert r['result']['serverInfo']['name'] == 'openrouter'
    r2 = mcp.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                     'params': {'protocolVersion': 'from-the-future'}})
    assert r2['result']['protocolVersion'] in mcp.SUPPORTED_PROTOCOL_VERSIONS


def test_every_tool_is_declared_and_callable():
    tools = mcp.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})['result']['tools']
    assert {t['name'] for t in tools} == set(mcp.TOOLS)
    for t in tools:
        assert t['inputSchema']['type'] == 'object' and len(t['description']) > 40
    with open(os.path.join(HERE, 'config.json')) as f:
        assert set(json.load(f)['tools']) == set(mcp.TOOLS), 'config.json drifted'


def test_notifications_get_no_response():
    assert mcp.handle({'jsonrpc': '2.0', 'method': 'notifications/initialized'}) is None


def test_a_tool_failure_is_a_result_with_isError_not_a_transport_error(monkeypatch):
    monkeypatch.delenv('OPENROUTER_API_KEY', raising=False)
    monkeypatch.setattr(C, '_keystore', dict)
    r = mcp.handle({'jsonrpc': '2.0', 'id': 7, 'method': 'tools/call',
                    'params': {'name': 'openrouter_key', 'arguments': {}}})
    assert 'error' not in r and r['result']['isError'] is True
    assert 'key' in r['result']['structuredContent']['error']


def test_unknown_method_and_unknown_tool_are_told_apart():
    assert mcp.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'nope'})['error']['code'] == -32601
    r = mcp.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                    'params': {'name': 'openrouter_nope'}})
    assert r['result']['isError'] and 'unknown tool' in r['result']['content'][0]['text']


def test_per_call_key_is_consumed_not_forwarded_as_a_filter():
    """`key` in tool args authenticates the call; it must never reach a filter."""
    args = {'key': 'sk-or-v1-x', 'q': 'claude'}
    c = mcp._client(args)
    assert c._key == 'sk-or-v1-x' and args == {'q': 'claude'}


# ── the module's own surface ─────────────────────────────────────────────

def test_config_fns_all_exist_on_the_mod():
    from mod import Mod
    with open(os.path.join(HERE, 'config.json')) as f:
        cfg = json.load(f)
    m = Mod()
    missing = [fn for fn in cfg['fns'] if not callable(getattr(m, fn, None))]
    assert not missing, f'config.json lists fns that do not exist: {missing}'


def test_info_lists_every_route_the_server_answers():
    import api
    d = api.info()
    assert d['name'] == 'openrouter' and d['mcp']['tools'] == len(mcp.TOOLS)
    assert 'no house key' in d['byok']['rule']
    # A route the server answers but nothing advertises is a route nobody finds.
    described = {r.split()[-1] for r in d['endpoints']}
    for path in ('/models', '/model', '/endpoints', '/providers', '/chat', '/complete',
                 '/cost', '/generation', '/key', '/credits', '/state', '/keys',
                 '/set_key', '/raw', '/tools', '/mcp'):
        assert path in described, f'{path} is served but not in info()'


def test_config_endpoints_match_the_api_self_description():
    import api
    with open(os.path.join(HERE, 'config.json')) as f:
        cfg = json.load(f)
    described = {r.split()[-1] for r in api.info()['endpoints']}
    for name, spec in cfg['endpoints'].items():
        path = spec.split()[1].split('?')[0] if ' ' in spec else spec
        if path.startswith('/') and name not in ('console', 'info'):
            assert path in described, f'config.json advertises {path}, info() does not'


# ── live (public catalog only — no key, no spend) ────────────────────────

@live
def test_catalog_is_real_and_normalizes():
    try:
        d = Client().search(q='claude', limit=5)
    except ORError as e:
        pytest.skip(f'offline: {e}')
    assert d['total_catalog'] > 100
    assert all(m['id'].count('/') == 1 for m in d['models'])


@live
def test_endpoints_come_back_priced_and_sorted():
    try:
        d = Client().endpoints('openai/gpt-4o-mini')
    except ORError as e:
        pytest.skip(f'offline: {e}')
    prices = [e['prompt_usd_m'] for e in d['endpoints'] if e['prompt_usd_m'] is not None]
    assert d['count'] >= 1 and prices == sorted(prices)
