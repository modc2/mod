"""core/yuma.py — the reference every chain's settle() is pinned to. The
numbers here are the same ones the native suites assert on chain."""
import itertools
import random

import pytest

from ztensor_src.core import yuma


def brute(scores, v):
    """Yuma straight from the definition, no histogram walk."""
    n = len(scores)
    k = yuma.need(max(v, n))
    full = list(scores) + [0] * max(v - n, 0)
    if n < k or k == 0:
        return 0, 0
    c = max(s for s in range(101) if sum(x >= s for x in full) >= k)
    return c, sum(min(x, c) for x in scores)


@pytest.mark.parametrize('scores,v,want', [
    ([90, 70], 4, (70, 140)),            # starknet full_epoch_anonymous_flow
    ([40], 4, (0, 0)),                   # 1 of 4: no consensus
    ([90, 90, 90, 100], 4, (90, 360)),   # outlier clipped
    ([100, 10, 10, 10], 4, (10, 40)),    # pump clipped to what 3 agree on
    ([90, 90, 100], 4, (90, 270)),       # silent validator = 0
    ([80, 80], 4, (80, 160)),            # exactly half is enough (kappa 1/2)
    ([80, 80], 7, (0, 0)),               # 2 of 7 is not
    ([0, 0, 0], 3, (0, 0)),
    ([], 0, (0, 0)),
    ([50, 60, 70], 2, (60, 170)),        # more votes than the snapshot: Vt = n
])
def test_settle_cases(scores, v, want):
    assert yuma.settle(yuma.hist(scores), v) == want
    assert brute(scores, v) == want


def test_settle_matches_definition_at_random():
    rnd = random.Random(7)
    for _ in range(2000):
        v = rnd.randint(1, 12)
        n = rnd.randint(0, v)
        scores = [rnd.choice([0, 10, 50, 77, 90, 100, rnd.randint(0, 100)])
                  for _ in range(n)]
        assert yuma.settle(yuma.hist(scores), v) == brute(scores, v), (scores, v)


def test_epoch_shares_and_outlier_resistance():
    r = yuma.epoch({'a': [90, 90, 90, 100], 'b': [100, 10, 10, 10]}, 4, 1000)
    assert r['rank_total'] == 400
    assert r['tags']['a']['share'] == 900 and r['tags']['b']['share'] == 100
    # raw sums would have paid b 1000 * 130 / 500 = 260
    assert r['tags']['b']['clipped'] == 90


def test_a_minority_cannot_raise_consensus():
    # any minority (< half) raising its scores never moves C up
    honest = [40, 40, 40, 40]
    for k in range(1, 2):  # 1 of 4 is a minority
        for pumped in itertools.combinations(range(4), k):
            s = [100 if i in pumped else x for i, x in enumerate(honest)]
            assert yuma.settle(yuma.hist(s), 4)[0] == 40


def test_scores_out_of_range():
    with pytest.raises(ValueError):
        yuma.hist([101])
