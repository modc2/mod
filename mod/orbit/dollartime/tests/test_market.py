"""Offline tests: a throwaway DB, a fake clock and a fake price feed."""

import os
import sys

import pytest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import market as mk  # noqa: E402


class Feed:
    def __init__(self):
        self.t = 1_800_000_000.0
        self.px = {'BTC': 100.0}
        self.close = {}   # asset -> price at close (None = not available yet)

    def now(self):
        return self.t

    def spot(self, a):
        if a not in self.px:
            raise KeyError(a)
        return self.px[a]

    def at(self, a, ts):
        return self.close.get(a)


@pytest.fixture
def env(tmp_path):
    f = Feed()
    m = mk.Market(str(tmp_path / 'dt.db'), price_fn=f.spot, price_at_fn=f.at, now_fn=f.now)
    return m, f


def bal(m, tok):
    return m.me(tok)['balance']


def test_up_wins_pot(env):
    m, f = env
    a, b, c = (m.join(n)['token'] for n in ('alice', 'bob', 'carol'))
    mid = m.create(a, 'btc', 4, 'up', 100)['id']
    m.bet(b, mid, 'down', 300)
    m.bet(c, mid, 'up', 100)
    mk_ = m.market(mid)
    assert mk_['pools'] == {'up': 200, 'down': 300} and mk_['pays']['up'] == 2.5
    f.t += 4 * 3600
    f.close['BTC'] = 101.0
    m.resolve_due()
    assert m.market(mid)['outcome'] == 'up'
    assert bal(m, a) == 1000 - 100 + 250
    assert bal(m, b) == 700
    assert bal(m, c) == 1150


def test_one_sided_and_flat_refund(env):
    m, f = env
    a, b = m.join('a')['token'], m.join('b')['token']
    one = m.create(a, 'BTC', 1, 'up', 50)['id']
    flat = m.create(b, 'BTC', 1, 'down', 20)['id']
    m.bet(a, flat, 'up', 20)
    f.t += 3600
    f.close['BTC'] = 99.0     # down: nobody on down in `one`
    m.resolve_due()
    assert m.market(one)['outcome'] == 'down' and bal(m, a) == 1000 - 20
    assert bal(m, b) == 1020
    f.close['BTC'] = 100.0
    c = m.create(a, 'BTC', 1, 'up', 10)['id']
    m.bet(b, c, 'down', 10)
    f.t += 3600
    m.resolve_due()
    assert m.market(c)['outcome'] == 'flat' and bal(m, a) == 980 and bal(m, b) == 1020


def test_betting_closes_and_validation(env):
    m, f = env
    a = m.join('a')['token']
    with pytest.raises(ValueError):
        m.join('A')                       # case-insensitive names
    with pytest.raises(ValueError):
        m.create(a, 'NOPE', 1, 'up', 10)  # unknown asset
    with pytest.raises(ValueError):
        m.create(a, 'BTC', 1, 'sideways', 10)
    with pytest.raises(ValueError):
        m.create(a, 'BTC', 1, 'up', 5000)  # more than balance
    with pytest.raises(ValueError):
        m.bet('bad-token', 1, 'up', 10)
    mid = m.create(a, 'BTC', 1, 'up', 10)['id']
    f.t += 15 * 60 + 1                   # past the default quarter-window lock
    with pytest.raises(ValueError, match='closed'):
        m.bet(a, mid, 'down', 10)
    assert m.markets('locked')['markets'][0]['id'] == mid


def test_waits_for_close_price_then_voids(env):
    m, f = env
    a, b = m.join('a')['token'], m.join('b')['token']
    mid = m.create(a, 'BTC', 1, 'up', 10)['id']
    m.bet(b, mid, 'down', 10)
    f.px = {}                             # feed down
    f.t += 3600 + 600
    m.resolve_due()
    assert m.market(mid)['outcome'] is None
    f.t += mk.VOID_AFTER
    m.resolve_due()
    assert m.market(mid)['outcome'] == 'void' and bal(m, a) == 1000 and bal(m, b) == 1000


def test_claim_daily(env):
    m, f = env
    a = m.join('a')['token']
    assert m.claim(a)['balance'] == 1100
    with pytest.raises(ValueError):
        m.claim(a)
    f.t += mk.CLAIM_EVERY
    assert m.claim(a)['balance'] == 1200
