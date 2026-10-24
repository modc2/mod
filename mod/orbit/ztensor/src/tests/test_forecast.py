"""Cost over time and the compression ladder (core/forecast.py), offline:
fixed per-action prices, no chain, RPC or price feed."""
import pytest

from ztensor_src.core import forecast as fc

# an economics() row: score $ = 0.5 + 0.25 x ring
ROW = {'ring': 4, 'max_ring': 32, 'actions_per_hour': 10_000,
       'by_ring': [{'ring': r, 'usd': 0.5 + 0.25 * r} for r in (4, 8, 16, 32)],
       'actions': {'score': {'usd': 1.5}, 'submit': {'usd': 1.5},
                   'claim': {'usd': 1.5}, 'settle': {'usd': 0.4},
                   'register': {'usd': 0.2}}}


def test_fit_recovers_the_line():
    a, b = fc.fit(ROW['by_ring'])
    assert a == pytest.approx(0.5) and b == pytest.approx(0.25)


def test_baseline_is_the_contract_formula():
    u = fc.units(ROW, proof_usd=2.0)
    e = fc.epoch_cost(u, M=10, V=5, ring=4)
    s = 1.5                                   # sig at ring 4
    assert e['score'] == pytest.approx(5 * 10 * s)
    assert e['submit'] == pytest.approx(10 * s)
    assert e['settle'] == pytest.approx(10 * 0.4)
    assert e['total'] == pytest.approx(50 * s + 10 * s + 4 + 10 * s)


def test_ring_is_capped_by_the_set():
    u = fc.units(ROW)
    assert fc.epoch_cost(u, 3, 3, ring=16)['score'] == \
        pytest.approx(9 * (0.5 + 0.25 * 3))


def test_every_step_of_the_ladder_is_cheaper_at_scale():
    u = fc.units(ROW, proof_usd=2.0)
    tot = [fc.epoch_cost(u, 256, 64, 16, st)['total']
           for st in fc.LADDERS['linking']]
    assert tot == sorted(tot, reverse=True)
    # batching removes the V x M ring verifications
    assert tot[0] / tot[1] > 10


def test_curve_hits_both_ends():
    for shape in ('exp', 'linear', 'logistic'):
        c = fc.curve(16, 256, 12, shape)
        assert (c[0], c[-1], len(c)) == (16, 256, 13)
        assert c == sorted(c)


def test_project_accumulates_and_floors_the_epoch():
    p = fc.project(fc.units(ROW, 2.0), (16, 64), (4, 16), months=6,
                   epoch_hours=0.25)
    assert p['epoch_hours'] == 1.0 and p['K'] == 24
    rows = p['rows']
    assert len(rows) == 7
    for st in fc.STRATEGIES:
        months = [r[st]['month'] for r in rows]
        assert rows[-1][st]['cum'] == pytest.approx(sum(months))
    assert p['savings']['baseline'] == 1.0
    assert p['savings']['proof'] > p['savings']['batch'] > 1
    # blockspace: baseline epoch of 64x16 scores vs 10k scores/hour
    assert rows[-1]['baseline']['blockspace'] > 0.1


def test_private_ladder_is_cheaper_and_keeps_privacy():
    u = fc.units(ROW, proof_usd=2.0)
    tot = [fc.epoch_cost(u, 256, 64, 16, st)['total']
           for st in fc.LADDERS['private']]
    assert tot == sorted(tot, reverse=True)
    assert tot[0] / tot[-1] > 10
    assert all(fc.PRIVACY[st][0] for st in fc.LADDERS['private'])
    assert not any(fc.PRIVACY[st][0] for st in ('batch', 'lazy', 'proof'))


def test_committee_scores_c_per_tag_not_v():
    u = fc.units(ROW)
    s = 0.5 + 0.25 * 16
    assert fc.epoch_cost(u, 100, 64, 16, 'committee', C=8)['score'] == \
        pytest.approx(8 * 100 * s)
    # a committee bigger than the set is everyone: today's contract
    assert fc.epoch_cost(u, 10, 4, 4, 'committee', C=16)['score'] == \
        pytest.approx(fc.epoch_cost(u, 10, 4, 4)['score'])


def test_capture_shrinks_with_the_committee_and_vanishes_when_all_score():
    assert fc.capture(64, 64, 0.25) == 0.0
    assert fc.capture(64, 64, 0.5) == 1.0
    a, b, c = (fc.capture(256, C, 0.25) for C in (8, 16, 32))
    assert a > b > c > 0
    assert c < 0.01
    # a coalition of 1/3 needs a larger committee for the same risk
    assert fc.capture(256, 32, 1 / 3) > c


def test_starve_is_the_liveness_price_and_falls_with_c():
    assert fc.starve(64, 64, 0.25) == 0.0
    a, b = fc.starve(64, 16, 0.25), fc.starve(64, 32, 0.25)
    assert 0 < b < a < 0.2
