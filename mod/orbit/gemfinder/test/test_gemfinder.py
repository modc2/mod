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


def test_server_serves_app_and_health():
    import json
    import threading
    import urllib.error
    import urllib.request
    from http.server import ThreadingHTTPServer
    from gemscan.server import Handler
    srv = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{srv.server_address[1]}'
    try:
        for p in ('/', '/gemfinder/', '/gemfinder'):
            with urllib.request.urlopen(base + p) as r:
                assert r.headers['Content-Type'].startswith('text/html') and b'gemfinder' in r.read()
        with urllib.request.urlopen(base + '/gemfinder/api/health') as r:
            assert json.loads(r.read())['ok'] is True
        try:
            urllib.request.urlopen(base + '/nope')
            assert False
        except urllib.error.HTTPError as e:
            assert e.code == 404
    finally:
        srv.shutdown()


# ── typed themes + scan cache (offline: feeds are monkeypatched) ─────────

def test_custom_theme_keywords_and_boundaries():
    assert nar.keywords('the next quantum computing plays') == ['quantum', 'computing']
    assert nar.keywords('AI') == ['ai']
    t = nar.custom('ai agents', [{'id': 'ai-agents', 'name': 'AI Agents', 'market_cap': 1},
                                 {'id': 'binance-hodler-airdrops', 'name': 'Airdrops', 'market_cap': 9}])
    assert t['cg'] == ['ai-agents']                              # "ai" is not "airdrops"
    assert not nar.hit(t, 'American Airlines', 'AAL')
    assert nar.hit(t, 'Virtuals ai agent launchpad', 'VIRTUAL')
    assert nar.hit(nar.custom('robot'), 'Figure humanoid robotics', 'FIG')   # prefix + alias
    try:
        nar.custom('the of')
        assert False
    except ValueError:
        pass


def test_merge_into_existing_keeps_dex_split():
    m = engine.merge([sig('crypto', 'PEPE', 'dexscreener_boost', ids={'address': 'aaa111'})])
    engine.merge([sig('crypto', 'PEPE', 'dex_search', ids={'address': 'bbb222'}),
                  sig('crypto', 'PEPE', 'cg_search')], into=m)
    assert set(m) == {('crypto', 'PEPE'), ('crypto', 'PEPE@bbb222')}
    assert len(m[('crypto', 'PEPE')]['signals']) == 2


def test_scan_cache_and_typed_theme(monkeypatch):
    calls = {'gather': 0, 'theme': 0}

    def fake_gather(asset='all', only=None):
        calls['gather'] += 1
        return [sig('crypto', 'QRL', 'coingecko_trending', text='Quantum Resistant Ledger',
                    metrics={'chg24': 12, 'mcap': 3e7, 'price': 1}),
                sig('stock', 'IONQ', 'yahoo_trending', text='IonQ', metrics={'chg24': 5, 'mcap': 9e9, 'price': 40}),
                sig('crypto', 'PEPE', 'reddit_apewisdom', text='Pepe frog', metrics={'chg24': 2, 'mcap': 4e9, 'price': 0.00001})], \
               {'coingecko_trending': {'ok': True, 'n': 3, 's': 0}}

    def fake_theme(theme, asset='all'):
        calls['theme'] += 1
        return [sig('crypto', 'FROGE', 'dex_search', text='Froge frog', metrics={'chg24': 30, 'mcap': 2e6, 'liq': 1e5, 'price': 0.1},
                    ids={'address': 'ccc333', 'chain': 'base'})], {'dex_search': {'ok': True, 'n': 1, 's': 0}}

    def fake_raw(themes, cands, asset='all', hn=True):
        raw = {n: {'crypto_move': i, 'trending_cats': 0, 'stock_move': i, 'hn_7d': i, 'crowd_crypto': 0.0, 'crowd_stock': 0.0}
               for i, n in enumerate(themes)}
        engine.crowd(raw, cands)
        return raw, {}

    monkeypatch.setattr(engine, 'gather', fake_gather)
    monkeypatch.setattr(engine, 'theme_signals', fake_theme)
    monkeypatch.setattr(engine, 'heat_raw', fake_raw)
    monkeypatch.setattr(engine, 'enrich_crypto', lambda c: None)
    monkeypatch.setattr(engine, 'enrich_stocks', lambda c, limit=120: None)
    monkeypatch.setattr(engine.src, 'coingecko_categories', lambda: [{'id': 'frog-themed-coins', 'name': 'Frog', 'market_cap': 1}])
    engine.clear_scan_cache()

    a = engine.scan('all', k=10, save=False)
    assert a['cached'] is False and calls['gather'] == 1
    b = engine.scan('all', k=2, save=False)                    # k is a post-filter: same cache entry
    assert b['cached'] is True and calls['gather'] == 1 and len(b['gems']) == 2
    c = engine.scan('all', k=10, save=False, fresh=True)
    assert c['cached'] is False and calls['gather'] == 2

    f = engine.scan('all', k=10, save=False, q='frog')
    assert calls['gather'] == 2 and calls['theme'] == 1          # theme rides on the cached base
    assert f['theme']['keywords'] == ['frog'] and f['theme']['categories'] == ['frog-themed-coins']
    assert {g['symbol'] for g in f['gems']} == {'PEPE', 'FROGE'}  # matched by word, and pulled in by search
    assert f['narratives'][0]['id'] == 'q' and f['theme']['matches'] == 2
    assert all('"frog"' in g['narratives'] for g in f['gems'])
    assert engine.scan('all', k=10, save=False, q='Frog ')['cached'] is True
    assert engine.scan('all', k=10, save=False, q='quantum')['gems'][0]['symbol'] in ('QRL', 'IONQ')
    assert any(x['key'][2] == 'frog' for x in engine.cache_stats()['results'])
    engine.clear_scan_cache()
