"""The prediction game: the rules that make a leaderboard worth reading.

All offline — time is passed in, the ticks are synthetic, and the index is fed
offers directly, so nothing here waits an hour or reads a market.
"""

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
MODULE = os.path.dirname(HERE)
if MODULE not in sys.path:
    sys.path.insert(0, MODULE)

import auth                                             # noqa: E402
import forecast as F                                    # noqa: E402
import mcp                                              # noqa: E402
import oracle                                           # noqa: E402
from providers.base import ProviderError, offer as mk_offer  # noqa: E402

T0 = 1_800_000_000.0
STEP = 1200


@pytest.fixture
def board(tmp_path):
    return F.Board(str(tmp_path / 'g.db'), tick_every=STEP)


def feed(b, values, n, start=0):
    """n ticks every STEP seconds; values is a fn of tick index → {series: v}."""
    for i in range(start, start + n):
        b.tick(values(i), t=T0 + i * STEP)
    return T0 + (start + n - 1) * STEP


def test_points_halve_every_five_percent():
    assert F.points(2.0, 2.0)[0] == 100
    assert F.points(2.1, 2.0)[0] == 50
    assert F.points(2.2, 2.0)[0] == 25
    assert F.points(1.9, 2.0)[0] == F.points(2.1, 2.0)[0]   # symmetric


def test_a_call_is_scored_against_the_tick_nearest_its_target(board):
    now = feed(board, lambda i: {'gpu:h100': 2.0}, 10)
    key = board.join('alice')['key']
    c = board.predict('alice', key, 'gpu:h100', '1h', 2.1, now=now)
    assert c['base'] == 2.0 and c['state'] == 'open'
    feed(board, lambda i: {'gpu:h100': 2.0}, 4, start=10)    # +80 min of reality
    got = board.call(c['id'])
    assert got['state'] == 'scored' and got['actual'] == 2.0 and got['points'] == 50
    assert got['beat_naive'] == 0                           # flat market: naive wins


def test_beating_persistence_is_recorded(board):
    now = feed(board, lambda i: {'gpu:h100': 2.0}, 10)
    key = board.join('bob')['key']
    c = board.predict('bob', key, 'gpu:h100', '1h', 2.2, now=now)
    feed(board, lambda i: {'gpu:h100': 2.2}, 4, start=10)
    got = board.call(c['id'])
    assert got['points'] == 100 and got['beat_naive'] == 1 and got['direction_ok'] == 1


def test_no_tick_near_the_target_voids_the_call_instead_of_guessing(board):
    now = feed(board, lambda i: {'gpu:h100': 2.0}, 10)
    key = board.join('carol')['key']
    c = board.predict('carol', key, 'gpu:h100', '1h', 2.0, now=now)
    board.tick({'gpu:h100': 9.0}, t=now + 6 * 3600)           # the ticker was down
    assert board.call(c['id'])['state'] == 'void'


def test_one_open_call_per_player_series_and_horizon(board):
    now = feed(board, lambda i: {'gpu:h100': 2.0}, 3)
    key = board.join('dave')['key']
    board.predict('dave', key, 'gpu:h100', '1h', 2.0, now=now)
    with pytest.raises(F.ForecastError, match='already has an open'):
        board.predict('dave', key, 'gpu:h100', '1h', 2.5, now=now)
    board.predict('dave', key, 'gpu:h100', '6h', 2.5, now=now)   # other horizon is fine


def test_a_name_is_owned_by_its_key(board):
    now = feed(board, lambda i: {'gpu:h100': 2.0}, 3)
    board.join('erin')
    with pytest.raises(F.ForecastError, match='taken'):
        board.join('erin')
    with pytest.raises(F.ForecastError, match='wrong key'):
        board.predict('erin', 'guess', 'gpu:h100', '1h', 2.0, now=now)
    with pytest.raises(F.ForecastError, match='baselines'):
        board.join('bot.naive')


@pytest.mark.parametrize('value', [0, -1, 'abc', float('inf'), 1000])
def test_nonsense_values_are_refused(board, value):
    now = feed(board, lambda i: {'gpu:h100': 2.0}, 3)
    key = board.join('frank')['key']
    with pytest.raises(F.ForecastError):
        board.predict('frank', key, 'gpu:h100', '1h', value, now=now)


def test_calls_close_when_the_series_goes_stale(board):
    now = feed(board, lambda i: {'gpu:h100': 2.0}, 3)
    key = board.join('gina')['key']
    with pytest.raises(F.ForecastError, match='not ticked'):
        board.predict('gina', key, 'gpu:h100', '1h', 2.0, now=now + 86400)


def test_bots_play_by_the_same_rules_and_rank_like_anyone(board):
    rising = lambda i: {'gpu:h100': 2.0 + 0.01 * i}            # noqa: E731
    now = feed(board, rising, 80)
    made = board.bots_play(now=now)
    assert made == len(F.BOTS) * len(F.HORIZONS)
    assert board.bots_play(now=now) == 0                         # all still open
    for k in range(6):                                           # 6 rounds of 1h calls
        now = feed(board, rising, 3, start=80 + 3 * k)
        board.bots_play(now=now)
    lb = board.leaderboard(horizon='1h')['board']
    names = [r['player'] for r in lb]
    assert set(names) == {F.BOT_PREFIX + b for b in F.BOTS}
    assert lb[0]['player'] == 'bot.drift'                      # a trend rewards the trend
    assert lb[0]['rank'] == 1 and lb[0]['ranked']


def test_provisional_players_sit_below_ranked_ones(board):
    now = feed(board, lambda i: {'gpu:h100': 2.0}, 3)
    key = board.join('hank')['key']
    board.predict('hank', key, 'gpu:h100', '1h', 2.0, now=now)
    feed(board, lambda i: {'gpu:h100': 2.0}, 4, start=3)
    r = board.leaderboard()['board'][0]
    assert r['player'] == 'hank' and r['score'] == 100 and r['rank'] is None


# ── the compute half ──

def test_the_index_is_the_median_per_gpu_price_of_available_offers():
    rows = [mk_offer('vast', str(i), gpu='H100 SXM', gpus=8, usd_hr=8 * p)
            for i, p in enumerate([1.0, 2.0, 2.5, 3.0, 50.0])]
    rows.append(mk_offer('lium', 'x', gpu='NVIDIA H100 80GB', gpus=1, usd_hr=2.5))
    rows.append(mk_offer('lium', 'y', gpu='H100', gpus=1, usd_hr=0.01, available=False))
    rows += [mk_offer('clore', str(i), gpu='RTX 4090', usd_hr=0.4) for i in range(3)]
    values, meta = oracle.index(rows)
    assert values == {'gpu:h100': 2.5}                          # 4090 has too few offers
    assert '"cheapest": 1.0' in meta['gpu:h100']


def test_the_clore_price_is_per_day_not_per_hour():
    from providers.clore import _usd_hr
    assert _usd_hr({'price': {'usd': {'on_demand_usd': 24}}}) == 1.0


def test_tick_feeds_the_board_and_the_bots(tmp_path, monkeypatch):
    monkeypatch.setattr(oracle, 'DB', str(tmp_path / 'o.db'))
    rows = [mk_offer('vast', str(i), gpu='RTX 4090', usd_hr=0.4 + i / 100) for i in range(6)]
    got = oracle.tick(force=True, offers=rows)
    assert got['indexed'] == {'gpu:4090': 0.425}
    assert got['bot_calls'] == len(F.BOTS) * len(F.HORIZONS)
    assert oracle.tick(offers=rows)['skipped']                  # inside the interval
    st = oracle.state()
    assert st['series'][0]['model'] == '4090' and st['series'][0]['spark']


def test_the_tools_play_the_game_and_mint_a_key_once(tmp_path, monkeypatch):
    monkeypatch.setattr(oracle, 'DB', str(tmp_path / 'o.db'))
    oracle.tick(force=True, offers=[mk_offer('vast', str(i), gpu='RTX 4090', usd_hr=0.4)
                                    for i in range(6)])
    got = mcp.call_tool('compute_predict', {'player': 'agent7', 'series': 'gpu:4090',
                                            'horizon': '6h', 'value': 0.41})
    assert got['player_key'] and got['call']['state'] == 'open'
    again = mcp.call_tool('compute_predict', {'player': 'agent7', 'key': got['player_key'],
                                              'series': 'gpu:4090', 'horizon': '24h',
                                              'value': 0.42})
    assert again['horizon'] == '24h'
    with pytest.raises(ProviderError):
        mcp.call_tool('compute_predict', {'player': 'agent7', 'series': 'gpu:4090',
                                          'horizon': '1h', 'value': 0.4})
    st = mcp.call_tool('compute_oracle', {})
    assert 'spark' not in st['series'][0]


def test_the_game_is_open_but_forcing_a_tick_is_not():
    for p in ('/oracle', '/oracle/board', '/oracle/predict', '/oracle/join'):
        assert auth.guard(p)
    assert {'compute_oracle', 'compute_predict'} <= auth.OPEN_TOOLS
    with pytest.raises(auth.Denied):
        auth.guard('/oracle/tick')
