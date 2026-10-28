"""
gemfinder — scan the internet for crypto tokens and stocks that a hot
narrative is starting to carry, ranked by an attention score.

Keyless and local-first: CoinGecko (trending + category momentum), DexScreener
(boosted + new tokens), reddit mentions via ApeWisdom, Yahoo Finance (trending +
small-cap/day-gainer/most-active screens) and Hacker News (narrative buzz).
Stdlib only, one SQLite cache. Attention, not a forecast — see `flags`.

CLI:
    m gemfinder/scan                            # top 20 across crypto + stocks
    m gemfinder/scan asset=crypto k=10
    m gemfinder/scan asset=stock narrative=quantum
    m gemfinder/find "nuclear drones"           # type anything dope: it becomes a live theme (words + meaning)
    m gemfinder/top                             # one line per gem, most likely to go up first
    m gemfinder/review                          # did past picks go up? price then vs now
    m gemfinder/scan fresh=1                    # skip the 10-min scan cache
    m gemfinder/narratives                      # which themes are hot right now
    m gemfinder/history                         # past scans (price at scan time)
    m gemfinder/docs                            # docs for agents: MCP tools, REST routes, how to read a gem
    m gemfinder/serve                           # REST + MCP (POST /mcp) on :51320 (pm2 `gemfinder`)
    m gemfinder/status | m gemfinder/kill | m gemfinder/test
"""
import json
import subprocess
import sys
from pathlib import Path
from typing import Optional

MODULE_DIR = Path(__file__).resolve().parent
# Appended, never prepended: `mod` itself must keep winning.
if str(MODULE_DIR) not in sys.path:
    sys.path.append(str(MODULE_DIR))

from gemscan import engine, http, narratives as nar, semantic as sem  # noqa: E402
from gemscan.server import source_list  # noqa: E402

PM2_NAME = 'gemfinder'


def _list(v):
    if v is None or isinstance(v, list):
        return v
    return [s for s in str(v).split(',') if s]


def _truthy(v) -> bool:
    return str(v).lower() not in ('0', 'false', 'no', 'off', '', 'none')


class Mod:
    description = 'Narrative gem scanner for crypto + stocks — CoinGecko, DexScreener, reddit, Yahoo, HN; keyless'

    def __init__(self):
        self.module_dir = MODULE_DIR
        self.config = json.loads((MODULE_DIR / 'config.json').read_text())
        self.port = int(self.config.get('port', 51320))

    def forward(self, asset: str = 'all', **kwargs):
        return self.scan(asset, **kwargs)

    def scan(self, asset: str = 'all', k: int = 20, narrative: Optional[str] = None, min_score: float = 0,
             sources=None, hn: bool = True, q: Optional[str] = None, fresh: bool = False,
             sort: str = 'upside') -> dict:
        """Ranked gems with score breakdown, upside, reasons and risk flags, plus `pick` (the one most
        likely to go up). asset: all|crypto|stock. q: a typed theme ('quantum', 'frog', 'nuclear drones'),
        matched by words and by meaning. sort: upside|score. fresh: skip the scan cache."""
        return engine.scan(asset=asset, k=int(k), narrative=narrative, min_score=float(min_score),
                           sources=_list(sources), hn=_truthy(hn), q=q, fresh=_truthy(fresh), sort=sort)

    def find(self, q: str, asset: str = 'all', k: int = 15, fresh: bool = False, sort: str = 'upside') -> dict:
        """Type something dope. It becomes a live theme: its words AND its meaning (semantic search) are
        matched against every candidate, plus CoinGecko search + categories, DexScreener search and Yahoo
        search. Returns the theme, the pick most likely to go up, which feeds answered, and the gems that fit."""
        res = self.scan(asset, k=k, q=q, fresh=fresh, sort=sort)
        return {'theme': res['theme'], 'pick': res['pick'], 'cached': res['cached'], 'age_s': res['age_s'],
                'sources': {n: s for n, s in res['sources'].items()
                            if n in ('cg_search', 'cg_category', 'dex_search', 'yahoo_search', 'semantic')},
                'gems': self._lines(res['gems'])}

    @staticmethod
    def _lines(gems: list) -> list:
        return [f"{g['upside']:5.1f}/{g['score']:<5.1f} {g['asset'][:6]:6} {g['symbol']:10} {g['name'][:28]:28} "
                f"{'fit ' + format(g['fit'], '.0%') + ' ' if g.get('fit', 1) < 1 else ''}— "
                f"{'; '.join(g['why'][1:])}{'  [' + ', '.join(g['flags']) + ']' if g['flags'] else ''}"
                for g in gems]

    def top(self, asset: str = 'all', k: int = 15, narrative: Optional[str] = None, q: Optional[str] = None,
            fresh: bool = False, sort: str = 'upside') -> list:
        """Just the lines: 'upside/score  ASSET SYMBOL  name [fit] — why  [flags]'. Most likely to go up first."""
        return self._lines(self.scan(asset, k=k, narrative=narrative, q=q, fresh=fresh, sort=sort)['gems'])

    def review(self, n: int = 12, fresh: bool = False) -> dict:
        """Did past picks go up? Each gem listed in the last n scans, priced now vs when first listed,
        with hit rate and median return. Hours of data, not a track record."""
        return engine.review(int(n), fresh=_truthy(fresh))

    def narratives(self, asset: str = 'all', hn: bool = True) -> dict:
        """Heat 0-100 per narrative for crypto and stocks, with the raw drivers."""
        return engine.narratives(asset=asset, hn=hn)

    def narrative_list(self) -> dict:
        """The themes and how each is detected (edit gemscan/narratives.py to change the thesis)."""
        return nar.NARRATIVES

    def history(self, n: int = 10) -> list:
        return engine.history(int(n))

    def sources(self) -> dict:
        return source_list()

    def docs(self) -> dict:
        """Docs for agents: how to read a gem, every MCP tool, every REST route, connect commands."""
        from gemscan.server import agent_docs
        return agent_docs()

    def clear_cache(self) -> dict:
        """Drop both the scan cache (in-process, 10 min) and the raw HTTP body cache (SQLite)."""
        return {'scans': engine.clear_scan_cache(), 'http': http.clear()}

    def cache(self) -> dict:
        """What is hot right now: scan-cache keys with ages, plus HTTP cache size."""
        return {'scans': engine.cache_stats(), 'http': http.stats(), 'semantic': sem.status()}

    def serve(self, port: Optional[int] = None, host: str = '127.0.0.1') -> dict:
        port = int(port or self.port)
        subprocess.run(['pm2', 'delete', PM2_NAME], capture_output=True, text=True)
        # cwd = module dir so `gemscan` imports; the server never imports `mod`.
        cmd = ['pm2', 'start', sys.executable, '--name', PM2_NAME, '--cwd', str(MODULE_DIR),
               '--', '-m', 'gemscan.server', '--port', str(port), '--host', host]
        r = subprocess.run(cmd, capture_output=True, text=True)
        return {'ok': r.returncode == 0, 'pm2': PM2_NAME, 'url': f'http://{host}:{port}/health',
                **({'error': r.stderr[-800:]} if r.returncode else {})}

    def kill(self) -> dict:
        r = subprocess.run(['pm2', 'delete', PM2_NAME], capture_output=True, text=True)
        return {'killed': r.returncode == 0, 'pm2': PM2_NAME}

    def status(self) -> dict:
        import urllib.request
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{self.port}/health', timeout=2) as r:
                return {'running': True, **json.loads(r.read())}
        except Exception as e:
            return {'running': False, 'error': str(e), 'hint': 'm gemfinder/serve', 'cache': http.stats()}

    def test(self) -> dict:
        r = subprocess.run([sys.executable, '-m', 'pytest', '-q', 'test'], cwd=MODULE_DIR,
                           capture_output=True, text=True)
        return {'ok': r.returncode == 0, 'output': (r.stdout + r.stderr)[-4000:]}
