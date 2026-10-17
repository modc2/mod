"""The rules, pinned. The EVM and NEAR tests assert the same numbers."""

import os
import sys

import pytest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import engine as E  # noqa: E402

T0 = 1_800_000_000
USD = E.USD
DTT = 10**18


class Clock:
    def __init__(self):
        self.t = T0

    def __call__(self):
        return self.t


@pytest.fixture
def led():
    c = Clock()
    L = E.Ledger(None, clock=c, genesis=T0)
    L.clock_obj = c
    return L


def tok(L, name):
    return L.join(name)['token']


def test_schedule_sums_to_cap():
    assert E.FULL_DAYS == 2916
    assert E.LAST_DAY_EMISSION == 4800 * DTT
    assert sum(E.emission(d) for d in range(0, 2920)) == E.CAP
    assert E.cumulative(2916) + E.emission(2916) == E.CAP
    assert E.cumulative(5000) == E.CAP
    assert E.emission(2917) == 0


def test_weight_is_dollars_times_time():
    assert E.weight_of(100 * USD, 0) == 100 * USD * 365
    assert E.weight_of(100 * USD, 365) == 100 * USD * 730   # 1y lock = 2x
    assert E.weight_of(1 * USD, 1460) == USD * 1825          # 4y lock = 5x


def test_day_split_by_weight(led):
    a, b = tok(led, 'a'), tok(led, 'b')
    led.contribute(a, 100 * USD, 0)      # w = 100*365
    led.contribute(b, 100 * USD, 365)    # w = 100*730  -> 2/3 of the day
    assert led.stats()['weight_today'] == 100 * USD * 365 + 100 * USD * 730
    assert led.claimable(1) == 0          # day still open
    led.clock_obj.t += E.DAY
    assert led.claimable(1) == E.DAILY * 365 // 1095
    assert led.claimable(2) == E.DAILY * 730 // 1095
    led.claim(a)
    led.claim(b)
    s = led.stats()
    assert s['total_supply'] == E.DAILY - 1 or s['total_supply'] == E.DAILY  # dust
    assert s['burned'] == 0
    assert s['treasury'] == 200 * USD


def test_sole_buyer_gets_all_7200(led):
    a = tok(led, 'a')
    led.contribute(a, 1 * USD, 0)        # one dollar, no lock
    led.clock_obj.t += E.DAY
    led.claim(a)
    assert led.me(a)['balance'] == 7200 * DTT


def test_empty_day_burns(led):
    a = tok(led, 'a')
    led.clock_obj.t += 3 * E.DAY         # days 0,1,2 pass untouched
    s = led.stats()
    assert s['day'] == 3
    assert s['emitted'] == 3 * E.DAILY
    assert s['burned'] == 3 * E.DAILY
    led.contribute(a, 5 * USD, 10)       # day 3 sold
    assert led.stats()['burned'] == 3 * E.DAILY
    led.clock_obj.t += 2 * E.DAY          # day 4 burned, day 5 open
    assert led.stats()['burned'] == 4 * E.DAILY
    assert led.stats()['emitted'] == 5 * E.DAILY


def test_cap_is_hard(led):
    a = tok(led, 'a')
    led.clock_obj.t += 2916 * E.DAY       # last emitting day
    assert led.stats()['emission_today'] == 4800 * DTT
    led.contribute(a, 1 * USD, 0)
    led.clock_obj.t += E.DAY
    led.claim(a)
    assert led.me(a)['balance'] == 4800 * DTT
    assert led.stats()['emitted'] == E.CAP
    assert led.stats()['burned'] == E.CAP - 4800 * DTT
    with pytest.raises(ValueError):
        led.contribute(a, 1 * USD, 0)     # emission over


def test_lock_blocks_transfer_until_unlock(led):
    a, b = tok(led, 'a'), tok(led, 'b')
    led.contribute(a, 10 * USD, 30)
    led.clock_obj.t += E.DAY
    led.claim(a)
    with pytest.raises(ValueError):
        led.transfer(a, 'b', 1 * DTT)
    assert led.me(a)['locked'] == 7200 * DTT
    led.clock_obj.t += 30 * E.DAY
    led.transfer(a, 'b', 1000 * DTT)
    assert led.me(b)['balance'] == 1000 * DTT
    assert led.me(a)['locked'] == 0


def test_revenue_splits_by_holding(led):
    a, b = tok(led, 'a'), tok(led, 'b')
    led.contribute(a, 300 * USD, 0)
    led.contribute(b, 100 * USD, 0)
    led.clock_obj.t += E.DAY
    led.claim(a)
    led.claim(b)
    led.deposit_revenue(b, 400 * USD, 'rent')
    assert led.me(a)['owed'] == pytest.approx(300 * USD, abs=2)
    assert led.me(b)['owed'] == pytest.approx(100 * USD, abs=2)
    r = led.withdraw_revenue(a)
    assert r['withdrawn'] == pytest.approx(300 * USD, abs=2)
    assert led.me(a)['owed'] == 0
    # a transfer carries no past revenue with it
    led.transfer(a, 'b', led.me(a)['balance'])
    led.deposit_revenue(a, 100 * USD)
    assert led.me(b)['owed'] == pytest.approx(200 * USD, abs=2)
    assert led.me(a)['owed'] == 0


def test_revenue_with_no_supply_pools(led):
    a = tok(led, 'a')
    r = led.deposit_revenue(a, 50 * USD)
    assert r['to'] == 'treasury'
    assert led.stats()['treasury'] == 50 * USD


def test_steward_spends_treasury_and_values_asset(led):
    a, b = tok(led, 'a'), tok(led, 'b')   # a joined first: steward
    led.contribute(a, 100 * USD, 0)
    led.contribute(b, 100 * USD, 0)
    with pytest.raises(PermissionError):
        led.spend(b, 'seller', 1 * USD)
    r = led.spend(a, 'seller', 150 * USD, 'buy the machine')
    assert r['treasury'] == 50 * USD
    with pytest.raises(ValueError):
        led.spend(a, 'seller', 51 * USD)
    led.set_asset(a, name='GPU box', valuation=1000 * USD)
    led.clock_obj.t += E.DAY
    led.claim(a)
    led.claim(b)
    assert led.me(a)['share_bps'] == 5000
    assert led.me(a)['value'] == 500 * USD


def test_persistence(tmp_path):
    c = Clock()
    p = str(tmp_path / 'l.json')
    L = E.Ledger(p, clock=c, genesis=T0)
    t = L.join('a')['token']
    L.contribute(t, 1 * USD, 0)
    L2 = E.Ledger(p, clock=c)
    assert L2.stats()['treasury'] == 1 * USD
    assert L2.me(t)['positions'][0]['id'] == 1
