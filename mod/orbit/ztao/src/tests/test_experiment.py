"""Epoch-length x miners x validators sweep (core/experiment.py), offline:
fixed per-action prices and loads, no chain, RPC or price feed."""
import pytest

from ztao_src.core import experiment as ex, forecast as fc

# score: $0.5 + 0.25 x ring, load 100 + 50 x ring; 1000 units per 1 s block
ROW = {'ring': 4, 'max_ring': 32, 'block_seconds': 1.0, 'block_capacity': 1000,
       'capacity_unit': 'gas',
       'by_ring': [{'ring': r, 'usd': 0.5 + 0.25 * r, 'load': 100 + 50 * r}
                   for r in (4, 8, 16, 32)],
       'actions': {k: {'usd': 1.5, 'load': 300} for k in
                   ('score', 'submit', 'claim')} |
       {'settle': {'usd': 0.4, 'load': 80}, 'register': {'usd': 0.2, 'load': 40}}}


def go(**kw):
    u, lu = fc.units(ROW), ex.load_units(ROW)
    return ex.run(ROW, u, lu, trials=40, **kw)


def test_load_units_fit_the_chain_units():
    lu = ex.load_units(ROW)
    assert lu['a'] == pytest.approx(100) and lu['b'] == pytest.approx(50)
    assert lu['settle'] == 80


def test_dollars_per_epoch_do_not_depend_on_its_length():
    r = go(hours=(1, 24))
    one, day = r['epochs']
    c1 = {(c['miners'], c['validators']): c for c in one['cells']}
    for c in day['cells']:
        k = (c['miners'], c['validators'])
        assert c['usd'] == pytest.approx(c1[k]['usd'])
        assert c['usd_day'] == pytest.approx(c1[k]['usd_day'] / 24)


def test_longer_epochs_hold_bigger_subnets():
    r = go(hours=(1, 4, 24))
    sq = [e['max_square'] for e in r['epochs']]
    assert sq == sorted(sq) and sq[0] < sq[-1]


def test_max_is_the_largest_that_fits():
    r = go(hours=(1,), share=0.1)
    e = r['epochs'][0]
    u, lu = fc.units(ROW), ex.load_units(ROW)
    room = 1000 * 3600 * 0.1
    for m in e['max_miners']:
        V, M = m['validators'], m['miners']
        if M:
            assert ex.epoch_load(lu, M, V, r['ring'], 'baseline', 24) <= room
        assert ex.epoch_load(lu, M + 1, V, r['ring'], 'baseline', 24) > room
    assert u


def test_budget_caps_the_subnet_too():
    free = go(hours=(1,))['epochs'][0]['max_square']
    tight = go(hours=(1,), budget_day=1000)['epochs'][0]['max_square']
    assert 0 < tight < free


def test_batching_raises_the_ceiling():
    base = go(hours=(1,))['epochs'][0]['max_square']
    batch = go(hours=(1,), strategy='batch')['epochs'][0]['max_square']
    assert batch > base


def test_simulation_brackets_the_deterministic_cost():
    for c in go(hours=(1,), vol=0.3)['epochs'][0]['cells']:
        assert c['p5'] <= c['p50'] <= c['p95']
        assert c['p95'] <= c['usd'] * 3


def test_no_capacity_means_only_the_budget_binds():
    row = {**ROW, 'block_capacity': None}
    r = ex.run(row, fc.units(row), ex.load_units(row), hours=(1,), trials=20)
    assert not r['blockspace_known']
    assert r['epochs'][0]['max_square'] == ex.CEIL
