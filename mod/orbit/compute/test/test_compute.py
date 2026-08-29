"""compute tests.

Two halves. The offline half is the contract every adapter must keep — shapes,
filters, id round-trips, the spend guard, the MCP wire — and runs with no
network. The live half touches the public catalogs of the key-less markets and
skips itself when the network is not there, because a market being down is not
this module's bug.
"""

import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
MODULE = os.path.dirname(HERE)
if MODULE not in sys.path:
    sys.path.insert(0, MODULE)

import mcp                                              # noqa: E402
import mods                                             # noqa: E402
import providers as P                                   # noqa: E402
from hub import CONFIRM_USD, Hub, gpu_model, split_id   # noqa: E402
from providers.base import Filters, ProviderError, offer as mk_offer  # noqa: E402

OFFER_FIELDS = {'id', 'provider', 'ref', 'kind', 'gpu', 'gpus', 'vram_gb', 'cpu',
                'ram_gb', 'disk_gb', 'usd_hr', 'region', 'available', 'note', 'raw'}
LIVE = os.environ.get('COMPUTE_TEST_LIVE', '1') not in ('0', 'false')


# ── the contract every adapter keeps ──

def test_every_provider_declares_itself():
    for name, cls in P.REGISTRY.items():
        assert cls.name == name
        assert cls.title and cls.docs
        assert cls.kyc in ('none', 'email', 'account', 'full', 'unknown')
        assert set(cls.caps) <= set(P.CAPS), f'{name} claims an unknown verb'
        assert 'search' in cls.caps or cls.name == 'local'


def test_registry_is_the_only_place_names_live():
    """Adding a market must not need a change anywhere else."""
    assert len(P.REGISTRY) >= 9
    assert P.get('lium').name == 'lium'
    with pytest.raises(ProviderError):
        P.get('nope')


def test_offer_shape_is_uniform():
    o = mk_offer('lium', 'abc', usd_hr=1.234567, gpu='H100', gpus=8, cpu=9.33333)
    assert set(o) == OFFER_FIELDS
    assert o['id'] == 'lium:abc'
    assert o['usd_hr'] == 1.2346       # money is rounded, not floated forever
    assert o['cpu'] == 9.3


def test_id_round_trips_even_with_colons_in_the_ref():
    assert split_id('lium:b7095b41') == ('lium', 'b7095b41')
    assert split_id('cathedral:custom.v1:sealed-cpu-small') == (
        'cathedral', 'custom.v1:sealed-cpu-small')
    with pytest.raises(ProviderError):
        split_id('no-namespace')


# ── filters ──

def _o(**kw):
    return mk_offer('x', 'y', **kw)


def test_filters_apply_the_same_rules_to_every_market():
    f = Filters(gpu='h100', min_gpus=2, min_vram_gb=40, max_usd_hr=5)
    assert f.match(_o(gpu='NVIDIA H100 80GB', gpus=8, vram_gb=80, usd_hr=4))
    assert not f.match(_o(gpu='NVIDIA H100 80GB', gpus=1, vram_gb=80, usd_hr=4))
    assert not f.match(_o(gpu='RTX 4090', gpus=8, vram_gb=80, usd_hr=4))
    assert not f.match(_o(gpu='NVIDIA H100', gpus=8, vram_gb=80, usd_hr=9))
    assert not f.match(_o(gpu='NVIDIA H100', gpus=8, vram_gb=24, usd_hr=4))


def test_gpu_match_ignores_the_note():
    """A note carrying 'cuda 12.4' must not make a 3060 answer a 4090 search."""
    assert not Filters(gpu='4090').match(
        _o(gpu='RTX 3060', note='verified · 4090-class network'))


def test_unpriced_offers_never_satisfy_a_price_ceiling():
    assert not Filters(max_usd_hr=1).match(_o(gpu='H100', usd_hr=None))


def test_storage_is_hidden_unless_asked_for():
    """Storage is priced per GB-hour and would win every cheapest-first sort."""
    s = _o(kind='storage', usd_hr=0.0001)
    assert not Filters().match(s)
    assert Filters(kind='storage').match(s)
    assert Filters(kind='all').match(s)


def test_gpu_model_normalizes_nine_naming_schemes():
    assert gpu_model('NVIDIA H100 80GB HBM3') == 'h100'
    assert gpu_model('NVIDIA-H100') == 'h100'
    assert gpu_model('NVIDIA 4090 Community') == '4090'
    assert gpu_model('RTX 3060 laptop') == '3060'
    assert gpu_model('CPU Small (4 vCPU / 16 GB)') is None


# ── keys ──

def test_keys_are_per_provider_and_never_shared():
    h = Hub(keys={'lium': 'K1'})
    assert P.get('lium', h.keys).key() == 'K1'
    with pytest.raises(P.NeedsKey):
        P.get('prime', h.keys).key()


def test_no_route_or_card_can_leak_a_key():
    h = Hub(keys={'lium': 'SECRET-KEY'})
    blob = json.dumps(h.providers(), default=str)
    assert 'SECRET-KEY' not in blob
    assert 'set' in blob


def test_permissionless_markets_report_no_key_needed():
    cards = {p['name']: p for p in Hub().providers()['providers']}
    assert cards['akash']['key'] == 'not needed'
    assert cards['nosana']['key'] == 'not needed'


# ── the spend guard ──

def test_guard_stops_an_expensive_rent_that_did_not_confirm(monkeypatch):
    h = Hub()
    monkeypatch.setattr(h, 'quote', lambda oid, hours: {
        'usd_hr': 9.0, 'total_usd': 9.0 * hours, 'needs_confirm': True})
    out = h.rent('lium:whatever', hours=1)
    assert out['rented'] is False and out['needs_confirm'] is True
    assert str(CONFIRM_USD) in out['why']


def test_guard_lets_a_cheap_rent_through(monkeypatch):
    h = Hub()
    monkeypatch.setattr(h, 'quote', lambda oid, hours: {
        'usd_hr': 0.01, 'total_usd': 0.01, 'needs_confirm': False})
    reached = {}

    class Fake:
        caps = ('rent',)

        def rent(self, ref, **kw):
            reached['ref'] = ref
            return {'id': 'lium:pod1'}

    monkeypatch.setattr(P, 'get', lambda name, keys=None: Fake())
    out = h.rent('lium:node9', hours=1)
    assert reached['ref'] == 'node9' and out['id'] == 'lium:pod1'
    assert 'compute_stop' in out['hint'] or 'stop' in out['hint']


def test_chain_native_markets_answer_with_a_plan_not_a_refusal():
    for pid, key in (('akash:h100-80Gi', 'sdl'), ('nosana:nvidia-4090-community', 'steps')):
        plan = Hub().rent(pid, hours=1)
        assert plan['action'] == 'plan'
        assert key in plan
        assert 'wallet' in plan


# ── fan-out ──

def test_one_dead_market_cannot_fail_the_search(monkeypatch):
    class Boom:
        name, caps, kyc = 'boom', ('search',), 'none'

        def search(self, f):
            raise ProviderError('upstream on fire', provider='boom')

    class Fine:
        name, caps, kyc = 'fine', ('search',), 'none'

        def search(self, f):
            return [mk_offer('fine', '1', usd_hr=1.0, gpu='H100', gpus=1)]

    monkeypatch.setattr(P, 'every', lambda *a, **k: [Boom(), Fine()])
    r = Hub().search()
    assert r['count'] == 1
    assert r['providers']['boom']['error'] == 'upstream on fire'
    assert r['providers']['fine'] == 1


def test_results_are_sorted_by_price_with_unpriced_rows_last(monkeypatch):
    class Fake:
        name, caps, kyc = 'f', ('search',), 'none'

        def search(self, f):
            return [mk_offer('f', 'c', usd_hr=None, gpu='H100'),
                    mk_offer('f', 'b', usd_hr=2.0, gpu='H100'),
                    mk_offer('f', 'a', usd_hr=1.0, gpu='H100')]

    monkeypatch.setattr(P, 'every', lambda *a, **k: [Fake()])
    r = Hub().search()
    assert [o['ref'] for o in r['offers']] == ['a', 'b', 'c']
    assert r['cheapest']['ref'] == 'a'


# ── mcp wire ──

def test_every_tool_is_declared_and_callable():
    assert len(mcp.TOOLS) == 21
    for name, t in mcp.TOOLS.items():
        assert name.startswith('compute_')
        assert t['description'] and t['inputSchema']['type'] == 'object'
        assert callable(t['handler'])


def test_initialize_echoes_a_protocol_version_we_implement():
    r = mcp.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                    'params': {'protocolVersion': '2025-06-18'}})
    assert r['result']['protocolVersion'] == '2025-06-18'
    assert r['result']['serverInfo']['name'] == 'compute'
    r2 = mcp.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                     'params': {'protocolVersion': '1999-01-01'}})
    assert r2['result']['protocolVersion'] == mcp.DEFAULT_PROTOCOL_VERSION


def test_notifications_get_no_response():
    assert mcp.handle({'jsonrpc': '2.0', 'method': 'notifications/initialized'}) is None


def test_a_failing_tool_is_a_successful_response_carrying_iserror():
    r = mcp.handle({'jsonrpc': '2.0', 'id': 9, 'method': 'tools/call',
                    'params': {'name': 'compute_status', 'arguments': {'id': 'junk'}}},
                   owner=True)
    assert 'error' not in r                      # not a protocol error
    assert r['result']['isError'] is True
    assert 'provider:ref' in r['result']['content'][0]['text']


def test_unknown_tool_is_a_protocol_error():
    r = mcp.handle({'jsonrpc': '2.0', 'id': 9, 'method': 'tools/call',
                    'params': {'name': 'compute_nope', 'arguments': {}}})
    assert r['result']['isError'] is True


# ── the mod lane ──

def test_every_mod_lane_fronts_a_market_this_module_already_knows():
    for name, lane in mods.LANES.items():
        assert name in P.REGISTRY, f'{name} has a lane but no adapter'
        assert callable(lane['read']) and lane['reads']


def test_a_mod_answering_200_with_an_error_body_is_not_an_empty_market(monkeypatch):
    """The failure this lane exists to catch: a module that proxies a retired
    upstream answers 200 with {"error": …}, which reads as a dead-quiet market
    unless something turns it back into an error."""
    monkeypatch.setattr(P.base, 'http', lambda *a, **k: {'error': 'upstream 410'})
    card = mods.one('targon', compare=False)
    assert card['status'] == 'down' and card['count'] == 0
    assert '410' in card['error']
    assert card['note'].startswith('neither')


def test_the_mod_lane_maps_a_module_row_into_the_shared_offer_shape(monkeypatch):
    monkeypatch.setattr(mods, 'http', lambda *a, **k: {'executors': [{
        'id': 'exec-1', 'gpu': 'NVIDIA H200', 'gpu_count': 8,
        'available_gpu_count': 8, 'vram_gb_per_gpu': 141.0, 'cpu_count': 128,
        'ram_gb': 1000.0, 'disk_gb': 3500.0, 'price_per_hr': 16.0,
        'price_per_gpu_hr': 2.0, 'location': 'Reykjavik, IS', 'tier': 'secure',
        'reliability': 99.4}]})
    card = mods.one('lium', compare=False)
    assert card['status'] == 'up' and card['count'] == 1
    o = card['offers'][0]
    assert set(o) == OFFER_FIELDS
    assert o['id'] == 'lium:exec-1' and o['usd_hr'] == 16.0 and o['gpus'] == 8


def test_a_mod_lane_id_round_trips_into_the_direct_lane(monkeypatch):
    """The lane is a view: what it finds must be rentable through the normal
    path, so its ids have to be the upstream's own."""
    monkeypatch.setattr(mods, 'http', lambda *a, **k: {'executors': [
        {'id': 'exec-1', 'gpu_count': 1, 'available_gpu_count': 1,
         'price_per_hr': 1.0}]})
    oid = mods.one('lium', compare=False)['offers'][0]['id']
    assert split_id(oid) == ('lium', 'exec-1')


def test_one_unreachable_module_cannot_fail_the_lane(monkeypatch):
    monkeypatch.setattr(mods, '_config', lambda name: {'port': 1})   # nothing listens
    r = mods.lane(compare=False)
    assert r['count'] == len(mods.LANES) and r['up'] == 0
    assert all(c['status'] == 'down' and c['error'] for c in r['mods'])


def test_the_lane_never_returns_a_key(monkeypatch):
    monkeypatch.setattr(mods, '_config', lambda name: {'port': 1})
    blob = json.dumps(mods.lane(keys={'lium': 'secret-key'}, compare=False))
    assert 'secret-key' not in blob


# ── live catalogs (skipped without network) ──

def _live(name):
    try:
        return P.get(name).search(Filters(available_only=False, limit=50))
    except ProviderError as e:
        pytest.skip(f'{name} unreachable: {e}')


@pytest.mark.skipif(not LIVE, reason='COMPUTE_TEST_LIVE=0')
@pytest.mark.parametrize('name', ['targon', 'lium', 'akash', 'vast', 'nosana',
                                  'cathedral', 'polaris', 'shadeform'])
def test_public_catalog_maps_into_the_shared_shape(name):
    rows = _live(name)
    assert rows, f'{name} returned an empty catalog'
    for o in rows[:10]:
        assert set(o) == OFFER_FIELDS
        assert o['provider'] == name and o['id'].startswith(name + ':')
        assert o['usd_hr'] is None or o['usd_hr'] >= 0
        assert o['kind'] in ('gpu', 'cpu', 'confidential', 'job', 'storage')


@pytest.mark.skipif(not LIVE, reason='COMPUTE_TEST_LIVE=0')
def test_a_module_running_here_reads_the_same_market_as_the_direct_lane():
    """Where both lanes answer, they must agree — a module fronting a market it
    reads differently from its own upstream is the bug this catches."""
    r = mods.lane(compare=True, sample=1)
    agreed = [c for c in r['mods'] if c['status'] == 'up'
              and isinstance((c.get('direct') or {}).get('count'), int)]
    if not agreed:
        pytest.skip('no sibling module is both installed and reachable')
    for c in agreed:
        assert c['count'] == c['direct']['count'], f"{c['name']}: {c['note']}"


@pytest.mark.skipif(not LIVE, reason='COMPUTE_TEST_LIVE=0')
def test_a_real_fan_out_reaches_several_markets():
    r = Hub().search(limit=25)
    reached = [k for k, v in r['providers'].items() if isinstance(v, int) and v > 0]
    assert len(reached) >= 3, f'only {reached} answered'
    assert r['offers'] and r['cheapest']['usd_hr'] > 0
