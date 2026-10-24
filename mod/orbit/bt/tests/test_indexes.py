"""Offline tests for bt.indexes — trader baskets on synthetic snapshots.

No chain: snapshots are written straight through traders._record and live
marks are monkeypatched, same pattern as the trader tests in test_bt.py.
"""
import os
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ['BT_NO_SNAPSHOT'] = '1'
os.environ.setdefault('BT_DATA_DIR', '/tmp/bt-test-default')

from bt import history, indexes, tools, traders  # noqa: E402

WHALE = '5GsbTgfvgCH4xdqSkiPb7EaBBFLHjWH5vfEALhJaewSFpZX9'
DEPOSITOR = '5GcCZ2BPXBjgG88tXJCEtkbdg2hNrPbL4EFfbiVRvBZdSQDC'


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv('BT_DATA_DIR', str(tmp_path))
    monkeypatch.setattr(history, '_cache',
                        {'rows': None, 'ts': None, 'block': None})
    monkeypatch.setattr(traders, '_prices_now', lambda: {1: 1.0, 2: 1.0})
    monkeypatch.setattr(traders, 'snapshot', lambda a, bt=None: {
        'ts': 1, 'total_tao': 0.0, 'positions': [], 'flows': []})
    return tmp_path


def _pos(netuid, alpha, price):
    return {'netuid': netuid, 'hotkey': 'hk', 'alpha': alpha,
            'price': price, 'value_tao': alpha * price}


def _snap(ss58, ts, positions, free=1.0):
    staked = sum(p['value_tao'] for p in positions)
    traders._record(ss58, ts, {
        'free_tao': free, 'staked_tao': staked, 'total_tao': free + staked,
        'positions': positions}, None)


def _seed(now):
    """WHALE +100% on subnet 1, DEPOSITOR flat on subnet 2."""
    traders.track(WHALE, label='whale')
    traders.track(DEPOSITOR, label='flat')
    _snap(WHALE, now - 6 * 86400, [_pos(1, 100.0, 0.5)])
    _snap(WHALE, now, [_pos(1, 100.0, 1.0)])
    _snap(DEPOSITOR, now - 6 * 86400, [_pos(2, 50.0, 1.0)])
    _snap(DEPOSITOR, now, [_pos(2, 50.0, 1.0)])


# ---------------------------------------------------------------- parsing

def test_parse_members_weights_normalize(store):
    got = indexes.parse_members(f'{WHALE}:3, {DEPOSITOR}')
    assert [e['ss58'] for e in got] == [WHALE, DEPOSITOR]
    assert got[0]['weight'] == pytest.approx(0.75)
    assert got[1]['weight'] == pytest.approx(0.25)


def test_parse_members_rejects_garbage(store):
    with pytest.raises(ValueError, match='invalid ss58'):
        indexes.parse_members('not-an-address')
    with pytest.raises(ValueError, match='at least one'):
        indexes.parse_members('  ')
    with pytest.raises(ValueError, match='bad weight'):
        indexes.parse_members(f'{WHALE}:heavy')
    with pytest.raises(ValueError, match='positive'):
        indexes.parse_members(f'{WHALE}:0')


def test_parse_members_dedups(store):
    got = indexes.parse_members(f'{WHALE} {WHALE} {DEPOSITOR}')
    assert len(got) == 2


# ------------------------------------------------------------------- CRUD

def test_create_get_delete_roundtrip(store):
    out = indexes.create('Whales', f'{WHALE}, {DEPOSITOR}', note='top two')
    assert out['name'] == 'Whales' and out['members'][0]['ss58'] == WHALE
    # auto-tracked both members
    assert {t['ss58'] for t in traders.watchlist()} == {WHALE, DEPOSITOR}
    assert {r['ss58'] for r in out['tracking']} == {WHALE, DEPOSITOR}
    # unique name, case-insensitive
    with pytest.raises(ValueError, match='already exists'):
        indexes.create('whales', WHALE)
    # resolve by name or id
    assert indexes.get('whales')['id'] == out['id']
    assert indexes.get(out['id'])['name'] == 'Whales'
    gone = indexes.delete('Whales')
    assert gone['removed'] is True
    with pytest.raises(ValueError, match='no index'):
        indexes.get('Whales')
    # members stay tracked after delete
    assert len(traders.watchlist()) == 2


def test_update_members_and_name(store):
    idx = indexes.create('Mix', WHALE)
    out = indexes.update(idx['id'], name='Mix2',
                         members=f'{WHALE}:1, {DEPOSITOR}:1')
    assert out['name'] == 'Mix2' and len(out['members']) == 2
    assert {t['ss58'] for t in traders.watchlist()} == {WHALE, DEPOSITOR}


# ------------------------------------------------------------ performance

def test_blended_performance(store):
    now = int(time.time())
    _seed(now)
    out = indexes.create('Blend', f'{WHALE}, {DEPOSITOR}', track=False)
    got = indexes.get('Blend', days=7)
    assert got['priced'] == 2
    # whale +100% market, depositor 0% → equal-weight blend = +50%
    assert got['market_pct'] == pytest.approx(50.0, abs=0.5)
    rows = {r['ss58']: r for r in got['rows']}
    assert rows[WHALE]['market_pct'] == pytest.approx(100.0, abs=0.5)
    assert rows[DEPOSITOR]['market_pct'] == pytest.approx(0.0, abs=0.5)
    # curve rebased to 100, ends around the blended ratio
    curve = got['curve']
    assert curve[0]['v'] == pytest.approx(100.0)
    assert curve[-1]['v'] > 120.0
    assert out['id'] == got['id']


def test_indexes_listing_sparks(store):
    now = int(time.time())
    _seed(now)
    indexes.create('A', WHALE, track=False)
    indexes.create('B', f'{WHALE}:2 {DEPOSITOR}', track=False)
    got = indexes.indexes(days=7)
    assert got['count'] == 2
    by_name = {i['name']: i for i in got['indexes']}
    assert by_name['A']['market_pct'] == pytest.approx(100.0, abs=0.5)
    assert len(by_name['B']['spark']) == 32
    # weighted 2:1 → (2·100 + 1·0)/3
    assert by_name['B']['market_pct'] == pytest.approx(66.7, abs=0.7)


def test_curve_ignores_warming_member(store):
    now = int(time.time())
    traders.track(WHALE)
    _snap(WHALE, now - 6 * 86400, [_pos(1, 100.0, 0.5)])
    _snap(WHALE, now, [_pos(1, 100.0, 1.0)])
    # DEPOSITOR tracked but never snapshotted — must not flatten the blend
    indexes.create('Half', f'{WHALE}, {DEPOSITOR}', track=False)
    got = indexes.get('Half', days=7)
    assert got['curve'][-1]['v'] > 150.0        # whale alone drives it
    assert got['priced'] == 1


# ------------------------------------------------------------------ tools

def test_tools_registered(store):
    names = {t.name for t in tools.TOOLS}
    assert {'bt_index_create', 'bt_indexes', 'bt_index', 'bt_index_update',
            'bt_index_delete'} <= names
    out = tools.call_tool('bt_index_create',
                          {'name': 'ViaTool', 'members': WHALE})
    assert out['name'] == 'ViaTool'
    assert tools.call_tool('bt_indexes', {})['count'] == 1
    assert tools.call_tool('bt_index', {'index': 'ViaTool'})['name'] == 'ViaTool'
    assert tools.call_tool('bt_index_delete',
                           {'index': 'ViaTool'})['removed'] is True
