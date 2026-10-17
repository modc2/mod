"""Offline tests: the data core is coherent and the model's arithmetic holds."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import housing
import serve


def test_crisis_stats_sourced():
    assert len(housing.CRISIS['stats']) >= 6
    for s in housing.CRISIS['stats']:
        assert s['source'], f"unsourced stat: {s['id']}"
        assert isinstance(s['value'], (int, float))


def test_plan_pillars():
    ids = [p['id'] for p in housing.PLAN['pillars']]
    assert ids == ['freeze', 'build', 'preserve', 'nycha', 'zoning', 'tenants']
    delivered = [p for p in housing.PLAN['pillars'] if p['status'] == 'delivered']
    assert [p['id'] for p in delivered] == ['freeze']  # the Oct 2026 freeze is live


def test_simulate_defaults():
    r = housing.simulate()
    assert r['years'] == list(range(11))
    # plan: 20k new + 10k preserved-vs-attrition − 10k attrition = +20k/yr
    assert r['plan_stock'][-1] == 200000
    # status quo: 7k pace − 10k attrition = −3k/yr
    assert r['status_quo_stock'][-1] == -30000
    assert r['headline']['vs_status_quo'] == 230000
    # two frozen years, then both compound at the same rate: gap persists
    assert r['plan_rent'][2] == r['plan_rent'][0]
    assert r['status_quo_rent'][2] > r['status_quo_rent'][0]
    assert r['headline']['tenant_savings_total_usd'] > 0


def test_simulate_clamps_garbage():
    r = housing.simulate(new_per_year=-5, freeze_years=99, rgb_hike=-3,
                         attrition_per_year=-1, years=500)
    a = r['assumptions']
    assert a['new_per_year'] == 0 and a['attrition_per_year'] == 0
    assert a['rgb_hike_pct'] == 0.0 and a['years'] == 30
    assert a['freeze_years'] <= a['years']


def test_no_freeze_means_no_savings():
    r = housing.simulate(freeze_years=0)
    assert r['headline']['tenant_savings_total_usd'] == 0
    assert r['plan_rent'] == r['status_quo_rent']


def test_api_surface():
    for fn in serve.READ_FNS:
        out = serve.api(fn, {})
        assert out is not None, fn
    assert serve.api('nope', {}) is None
    assert serve.api('pillar', {'id': 'freeze'})['status'] == 'delivered'
    assert 'pillars' in serve.api('pillar', {'id': 'bogus'})
    # query args arrive as strings and must not crash the model
    out = serve.api('simulate', {'new_per_year': '25000', 'freeze_years': 'x'})
    assert out['assumptions']['new_per_year'] == 25000
    assert out['assumptions']['freeze_years'] == 2  # bad arg → default


def test_sources_have_urls():
    for s in housing.SOURCES:
        assert s['url'].startswith('https://')
