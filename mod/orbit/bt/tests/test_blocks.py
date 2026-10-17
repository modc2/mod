"""Offline tests for bt.blocks — the daily block ledger + daily candles."""
import os
import sqlite3
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ['BT_NO_SNAPSHOT'] = '1'

import pytest  # noqa: E402

from bt import blocks, history  # noqa: E402

DAY0 = 1790726400       # 2026-09-30 00:00 UTC


class FakeChain:
    """Block n stamped 12s apart with a 30s stall at block 5000."""
    def __init__(self, head=20000, t0=DAY0 - 100000):
        self._head, self.t0, self.reads = head, t0, 0

    def head(self):
        return self._head

    def ts(self, n):
        self.reads += 1
        return self.t0 + n * 12 + (30 if n >= 5000 else 0)

    def hash(self, n):
        return '0x%064x' % n


def test_block_at_finds_the_first_block_of_the_day():
    c = FakeChain()
    for target in (DAY0 - 50000, DAY0, DAY0 + 7, c.t0 + 5000 * 12 + 10):
        n = blocks.block_at(c, target)
        assert c.ts(n) >= target and c.ts(n - 1) < target
    with pytest.raises(ValueError):
        blocks.block_at(c, c.ts(c.head()) + 100)


def test_day_helpers():
    assert blocks.day_of(DAY0) == '2026-09-30' and blocks.day_start('2026-09-30') == DAY0
    assert blocks.days_between('2026-09-29', '2026-10-01') == ['2026-09-29', '2026-09-30', '2026-10-01']


@pytest.fixture
def store(monkeypatch):
    d = tempfile.mkdtemp(prefix='bt-blocks-')
    monkeypatch.setenv('BT_DATA_DIR', d)
    conn = history._db()
    rows = []
    # two days of 6-hourly snapshots for two subnets; volume is cumulative
    for i in range(8):
        ts = DAY0 - 86400 + i * 21600
        for uid, p0 in ((1, 0.01), (2, 0.5)):
            rows.append((ts, uid, p0 * (1 + i / 100), 100.0 * i, 10.0, 1000.0 + 50 * i, 0.1, 1000 + i * 1800))
    conn.executemany('INSERT INTO snaps (ts, netuid, price, mcap, tao_in, volume, emission, block) '
                     'VALUES (?,?,?,?,?,?,?,?)', rows)
    conn.commit(); conn.close()
    return d


def test_rollup_candles(store):
    assert blocks.rollup('2026-09-30') == 2
    c = {r['netuid']: r for r in blocks.daily(day='2026-09-30')['candles']}
    one = c[1]
    assert one['open'] == pytest.approx(0.01 * 1.04) and one['close'] == pytest.approx(0.01 * 1.07)
    assert one['high'] == one['close'] and one['low'] == one['open'] and one['snaps'] == 4
    # 24h volume = close cumulative - previous day's close cumulative
    assert one['vol_tao'] == pytest.approx(50 * 4)
    assert one['change_pct'] == pytest.approx((1.07 / 1.04 - 1) * 100)
    # first day has no previous close: falls back to its own first snapshot
    blocks.rollup('2026-09-29')
    assert blocks.daily(day='2026-09-29')['candles'][0]['vol_tao'] == pytest.approx(150)
    # idempotent
    assert blocks.rollup('2026-09-30') == 2
    assert len(blocks.daily(netuid=2, days_back=10000)['candles']) == 2


def test_anchor_days_once_each(store):
    c = FakeChain(head=200000, t0=DAY0 - 1_000_000)
    got = blocks.anchor_days(chain=c, days=['2026-09-29', '2026-09-30'])
    assert [g['day'] for g in got] == ['2026-09-30', '2026-09-29']
    assert got[0]['block_ts'] >= DAY0 and got[0]['block'] - got[1]['block'] in (7200, 7199, 7198)
    assert blocks.anchor_days(chain=c, days=['2026-09-29', '2026-09-30']) == []   # cached
    d = blocks.days()
    assert d['count'] == 2 and d['days'][1]['blocks'] == got[0]['block'] - got[1]['block']
