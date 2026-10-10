"""bt.trades — chain-event trade index. Offline: fixtures are real finney
event shapes (block 9190438/9190439), a fake chain stands in for the node."""
import os
import sys
import time

import pytest

os.environ['BT_NO_SNAPSHOT'] = '1'
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bt import trades  # noqa: E402

A, B, C = '5CLkUqWqDygmZucQCojG5J4Tm9UiHw6VrXUZ7Avj9JUwnbKS', '5Gp5B45q', '5Ewy6rrw'
HK1, HK2 = '5HTauas8', '5G1Qj93F'


def ev(name, attrs, ext, module='SubtensorModule', phase='ApplyExtrinsic'):
    return {'phase': phase, 'extrinsic_idx': ext, 'module_id': module,
            'event_id': name, 'attributes': attrs}


BLOCK = [
    ev('ExtrinsicSuccess', {'dispatch_info': {}}, 0, module='System'),
    # hotkey move on SN75: Removed+Added same pool, same amounts -> not a trade
    ev('StakeRemoved', (A, HK1, 2344749539, 146140999954, 75, 0), 10),
    ev('StakeAdded', (A, HK2, 2344749539, 146140999954, 75, 0), 10),
    ev('StakeMoved', (A, HK1, 75, HK2, 75, 2344749539), 10),
    # transfer out of SN60 into root: the SN60 leg is a real sell, root is skipped
    ev('StakeRemoved', (B, HK1, 15451177823, 3828990137449, 60, 1929050632), 19),
    ev('StakeAdded', (B, HK1, 15451177823, 15451177823, 0, 0), 19),
    ev('StakeTransferred', (B, B, HK1, 60, 0, 15451177823), 19),
    # swap SN44 -> SN53: a sell and a buy, both kind=swap
    ev('StakeRemoved', (C, HK2, 9053831604, 279107534902, 44, 140614769), 21),
    ev('StakeAdded', (C, HK2, 9053831604, 247738200677, 53, 0), 21),
    ev('StakeSwapped', (C, HK2, 44, 53, 9053831604), 21),
    # plain buy
    ev('StakeAdded', (A, HK1, 5_000_000_000, 1_000_000_000_000, 21, 2_000_000), 30),
    # not in an extrinsic (emission/auto-stake) -> ignored
    ev('StakeAdded', (A, HK1, 1, 1, 21, 0), None, phase='Finalization'),
]


def test_classify_drops_moves_root_and_non_extrinsic():
    rows = trades.classify(BLOCK, 100, 1_700_000_000)
    got = [(r['netuid'], r['side'], r['kind']) for r in rows]
    assert got == [(60, 'sell', 'stake'), (44, 'sell', 'swap'),
                   (53, 'buy', 'swap'), (21, 'buy', 'stake')]
    buy = rows[-1]
    assert buy['tao'] == 5.0 and buy['alpha'] == 1000.0
    assert buy['price'] == pytest.approx(0.005)
    assert buy['coldkey'] == A and buy['block'] == 100


def test_classify_accepts_dict_attributes_and_nested_event():
    e = {'phase': 'ApplyExtrinsic', 'extrinsic_idx': 3,
         'event': {'module_id': 'SubtensorModule', 'event_id': 'StakeRemoved',
                   'attributes': {'coldkey': A, 'hotkey': HK1, 'tao': 10 ** 9,
                                  'alpha': 4 * 10 ** 9, 'netuid': 7, 'fee': 0}}}
    [r] = trades.classify([e], 1, 1)
    assert r['side'] == 'sell' and r['netuid'] == 7 and r['price'] == 0.25


class FakeChain:
    def __init__(self, head, per_block):
        self._head, self.per_block, self.calls = head, per_block, []

    def head(self):
        return self._head

    def block(self, n, head):
        self.calls.append(n)
        return int(time.time()) - (head - n) * 12, self.per_block


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv('BT_DATA_DIR', str(tmp_path))
    monkeypatch.setattr(trades, 'BACKFILL_DAYS', 50 * 12 / 86400)  # 50 blocks
    monkeypatch.setattr(trades, 'YIELD_SEC', 0)
    return tmp_path


def test_run_once_backfills_newest_first_and_heals_gaps(store):
    ch = FakeChain(1000, BLOCK)
    r = trades.run_once(ch, budget=60)
    assert r['indexed'] == 51 and r['backlog'] == 0
    assert ch.calls[0] == 1000 and ch.calls[-1] == 950
    ch2 = FakeChain(1003, BLOCK)                 # 3 new blocks; floor moves up too
    r = trades.run_once(ch2, budget=60)
    assert sorted(ch2.calls) == [1001, 1002, 1003]
    assert trades.status()['blocks'] == 54


def test_pruned_node_is_refused_not_recorded(store):
    with pytest.raises(RuntimeError):
        trades.run_once(FakeChain(10, []), budget=60)
    assert trades.status()['blocks'] == 0


def test_trades_query_filters_summary_and_paging(store):
    trades.run_once(FakeChain(1000, BLOCK), budget=60)
    one = trades.trades(netuid=21, hours=24, limit=10)
    assert one['summary']['trades'] == 51 and one['summary']['buys'] == 51
    assert one['summary']['buy_tao'] == pytest.approx(255.0)
    assert one['top'][0]['coldkey'] == A and one['top'][0]['net_tao'] == pytest.approx(255.0)
    assert one['count'] == 10 and one['more'] and one['trades'][0]['block'] == 1000
    page2 = trades.trades(netuid=21, hours=24, limit=10, before_block=one['next_before_block'])
    assert page2['trades'][0]['block'] == one['next_before_block'] - 1
    sells = trades.trades(hours=24, side='sell', limit=500)
    assert {t['side'] for t in sells['trades']} == {'sell'}
    assert sells['summary']['trades'] == 51 * 4          # summary ignores the side filter
    assert trades.trades(hours=24, min_tao=10)['summary']['trades'] == 51   # only the 15.45 τ sell
    cov = one['coverage']
    assert cov['complete'] and cov['to_block'] == 1000 and cov['gaps'] == 0


def test_flows_board_nets_per_subnet(store):
    trades.run_once(FakeChain(1000, BLOCK), budget=60)
    f = trades.flows(hours=24)
    rows = {r['netuid']: r for r in f['rows']}
    assert set(rows) == {21, 44, 53, 60}
    # sorted by net inflow: the swap-in subnet leads, the big sell trails
    assert [r['netuid'] for r in f['rows']] == [53, 21, 44, 60]
    sn21 = rows[21]
    assert sn21['buys'] == 51 and sn21['sells'] == 0
    assert sn21['buy_tao'] == pytest.approx(255.0)
    assert sn21['net_tao'] == pytest.approx(255.0)
    assert sn21['traders'] == 1 and sn21['buyers'] == 1 and sn21['sellers'] == 0
    assert sn21['biggest_tao'] == pytest.approx(5.0)
    sn60 = rows[60]
    assert sn60['net_tao'] == pytest.approx(-51 * 15.451177823)
    # swap legs flow on both sides they touch
    assert rows[44]['sell_tao'] == pytest.approx(rows[53]['buy_tao'])
    assert f['total_net_tao'] == pytest.approx(
        sum(r['net_tao'] for r in f['rows']))
    assert f['coverage']['complete']
