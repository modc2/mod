"""Offline tests: scoring, merging and narrative matching on fixture signals (no network)."""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault('GEMFINDER_HOME', tempfile.mkdtemp())
sys.path.append(str(Path(__file__).resolve().parents[1]))

from gemscan import engine, narratives as nar  # noqa: E402
from gemscan.server import route  # noqa: E402


def sig(asset, symbol, source, strength=1.0, text='', metrics=None, ids=None):
    return {'asset': asset, 'symbol': symbol, 'name': symbol, 'source': source, 'strength': strength,
            'text': text, 'metrics': metrics or {}, 'url': '', 'ids': ids or {}}


def heat_map(crypto=80, stock=80):
    return {n: {'label': v['label'], 'crypto': crypto, 'stock': stock} for n, v in nar.NARRATIVES.items()}


def test_match_keywords_and_seeds():
    assert 'quantum' in nar.match('Quantum Computing Inc', 'QUBT', 'stock')
    assert 'quantum' in nar.match('', 'IONQ', 'stock')          # seed ticker, no text
    assert nar.match('', 'IONQ', 'crypto') == []                # seeds are stock-only
    assert nar.match('Alamos Gold', 'AGI', 'stock') == []       # ticker never keyword-matched
    assert 'ai' in nar.match('the first AI agent on solana')


def test_merge_skips_majors_and_splits_dex_collisions():
    m = engine.merge([sig('crypto', 'BTC', 'coingecko_trending'),
                      sig('crypto', 'PEPE', 'dexscreener_boost', ids={'address': 'aaa111', 'chain': 'solana'}),
                      sig('crypto', 'PEPE', 'dexscreener_profile', ids={'address': 'bbb222', 'chain': 'base'}),
                      sig('crypto', 'PEPE', 'reddit_apewisdom')])
    assert ('crypto', 'BTC') not in m
    assert ('crypto', 'PEPE') in m and ('crypto', 'PEPE@bbb222') in m
    assert len(m[('crypto', 'PEPE')]['signals']) == 2


def _cand(asset, symbol, signals, metrics, narratives=(), ids=None):
    c = engine.merge(signals)[(asset, symbol)]
    c['metrics'].update(metrics)
    c['ids'].update(ids or {})
    c['narratives'] = list(narratives)
    return c


def test_cross_source_hot_narrative_beats_lone_signal():
    hm = heat_map()
    strong = _cand('stock', 'QUBT', [sig('stock', 'QUBT', 'reddit_apewisdom'), sig('stock', 'QUBT', 'yahoo_trending')],
                   {'mentions': 40, 'mentions_24h_ago': 8, 'chg24': 18, 'mcap': 1.2e9, 'relvol': 3}, ['quantum'])
    weak = _cand('stock', 'XYZ', [sig('stock', 'XYZ', 'yahoo_most_actives', 0.2)], {'chg24': -3, 'mcap': 5e11})
    a, b = engine.score(strong, hm), engine.score(weak, hm)
    assert a['score'] > b['score'] + 30
    assert any('reddit mentions' in w for w in a['why'])


def test_flags_penalise_rugs_and_vertical_moves():
    hm = heat_map()
    base = {'chg24': 20, 'mcap': 2e6, 'liq': 300_000, 'fdv': 2e6, 'buys24': 500, 'sells24': 400}
    ok = engine.score(_cand('crypto', 'GEM', [sig('crypto', 'GEM', 'dexscreener_boost')], base,
                            ids={'address': 'x'}), hm)
    rug = engine.score(_cand('crypto', 'RUG', [sig('crypto', 'RUG', 'dexscreener_boost')],
                             {**base, 'liq': 5_000, 'chg24': 900, 'age_h': 2, 'sells24': 2000},
                             ids={'address': 'y'}), hm)
    assert rug['score'] < ok['score'] - 20
    assert any('thin liquidity' in f for f in rug['flags']) and any('already' in f for f in rug['flags'])


def test_routes_accept_gateway_prefixes():
    assert route('/gemfinder/api/scan') == '/scan'
    assert route('/gemfinder/scan') == '/scan'
    assert route('/scan') == '/scan'
