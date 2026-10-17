"""Offline tests for the nyc dependency seam and the reality layer.

nyc is pointed at a dead port and the disk cache at a temp dir, so every
read falls through to snapshot.json — the baked fixture. The shapes these
tests assert are exactly what the console consumes."""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ['NYC_API'] = 'http://127.0.0.1:9'      # discard port: refused instantly
os.environ['NYC_TIMEOUT'] = '1'

import nycbridge  # noqa: E402
import reality  # noqa: E402
import serve  # noqa: E402

nycbridge.CACHE_DIR = tempfile.mkdtemp(prefix='mandamistan-test-')
nycbridge._mem.clear()


def test_bridge_falls_back_to_snapshot():
    r = nycbridge.fetch('stats', {'geography': 'borough'})
    assert r['source'] == 'snapshot' and r['error']
    assert r['data']['city']['renter_households'] > 2_000_000
    assert nycbridge.status()['reachable'] is False


def test_bridge_live_path_with_fake_fetcher():
    nycbridge._mem.clear()
    calls = []
    fake = lambda fn, a: calls.append((fn, a)) or {'ok': True, 'fn': fn}  # noqa: E731
    r1 = nycbridge.fetch('health', {}, fetcher=fake)
    r2 = nycbridge.fetch('health', {}, fetcher=fake)
    assert r1['source'] == 'live' and r2['source'] == 'cache'
    assert len(calls) == 1                                  # TTL cache held
    assert nycbridge.fetch('health', {}, max_age=0, fetcher=fake)['source'] == 'live'


def test_bridge_never_raises():
    r = nycbridge.fetch('no_such_endpoint', {'x': 1})
    assert r['data'] is None and r['source'] == 'none' and r['error']


def test_scorecard_rows_match_pillars():
    sc = reality.scorecard()
    assert [r['id'] for r in sc['rows']] == ['freeze', 'build', 'preserve', 'nycha', 'zoning', 'tenants']
    assert sc['source'] == 'snapshot'
    by = {r['id']: r for r in sc['rows']}
    assert 40 < by['freeze']['now'] < 70                   # rent burden %
    assert by['build']['target_per_year'] == 20000
    assert by['preserve']['now'] > 0                        # class C per 1k renters
    assert by['nycha']['now'] > 150_000
    assert by['tenants']['unit'] == 'lotteries'


def test_live_crisis_keeps_static_tiles():
    lc = reality.live_crisis()
    ids = [t['id'] for t in lc['tiles']]
    assert 'rent_burden' in ids and 'vacancy' in ids and 'shelter' in ids
    assert all(t['value'] is not None for t in lc['tiles'])


def test_borough_scales_the_plan():
    b = reality.borough('bronx')
    assert b['borough'] == 'Bronx' and 0.1 < b['share_of_renters'] < 0.3
    a = b['simulation']['assumptions']
    assert a['new_per_year'] == int(20000 * b['share_of_renters'])
    assert a['stabilized_units'] == int(1_000_000 * b['share_of_renters'])
    assert a['median_stabilized_rent'] == b['profile']['median_rent']
    assert 'boroughs' in reality.borough('Hoboken')


def test_find_ami_bands():
    assert reality.ami_for(4) == 169_600 and reality.ami_for(5) == round(169_600 * 1.08)
    f = reality.find(45000, 2)
    assert f['pct_ami'] == 33.2 and f['band']['key'] == 'very_low'
    assert f['affordable_rent'] == 1125
    for L in f['lotteries']['items']:
        assert any('very low' in b.lower() for b in L['income_bands'])
    rich = reality.find(400000, 1)
    assert rich['band']['key'] == 'above'
    assert 'error' in reality.find('lots', 1)


def test_neighborhoods_rank_the_rich_who_built_nothing():
    n = reality.neighborhoods()
    rows = n['least_building']
    assert rows and rows[0]['median_income'] >= 120_000
    assert rows == sorted(rows, key=lambda r: r['new_units_per_1k'])


def test_api_reality_routes():
    for fn in ('live', 'scorecard', 'borough', 'find', 'neighborhoods', 'nyc'):
        out = serve.api(fn, {})
        assert out is not None and not out.get('error'), fn
    out = serve.api('find', {'income': '60000', 'household': '3', 'bedrooms': '2'})
    assert out['household'] == 3 and out['affordable_rent'] == 1500
    out = serve.api('borough', {'name': 'Queens', 'freeze_years': '4'})
    assert out['simulation']['assumptions']['freeze_years'] == 4
    sim = serve.api('simulate', {'base_pace': '0', 'stabilized': '0'})
    assert sim['headline']['tenant_savings_total_usd'] == 0


def test_find_lists_one_row_per_building():
    f = reality.find(45000, 2)
    keys = [h['name'] for h in f['homes']['items']]
    assert len(keys) == len(set(keys))
    assert f['homes']['buildings'] <= (f['homes']['matched'] or 0)
