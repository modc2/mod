"""The class every generated subnet mod (subnets/sn<N>/mod.py) inherits.

A subnet mod is a thin, data-first view of ONE Bittensor subnet:

  - data.json   the daily snapshot written by `subnets sync` (identity, price,
                pool, 24h/7d change, 24h trade summary, latest news)
  - live calls  go to the local orbit/bt index (BT_URL, default :50280) and
                fall back to the snapshot when bt is unreachable, flagged
                `stale: true` — so a subnet mod always answers, offline included.

Nothing here talks to a third-party API; bt owns the chain + scraping.
"""

import json
import os
import urllib.request

BT_URL = os.environ.get('BT_URL', 'http://localhost:50280').rstrip('/')
TIMEOUT = float(os.environ.get('SUBNETS_BT_TIMEOUT', '30'))


def bt_call(tool, timeout=TIMEOUT, **args):
    """POST one tool call to the local bt module; returns its `result` or raises."""
    body = json.dumps({'tool': tool, 'args': args}).encode()
    req = urllib.request.Request(f'{BT_URL}/api/call', data=body,
                                 headers={'content-type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        out = json.load(r)
    if not out.get('ok'):
        raise RuntimeError(f"{tool}: {out.get('error')}")
    return out['result']


class Subnet:
    netuid = None                      # set by the generated subclass
    dirpath = None                     # set by the generated subclass

    # -- snapshot ---------------------------------------------------------
    def snapshot(self):
        """The daily snapshot exactly as `subnets sync` last wrote it."""
        try:
            with open(os.path.join(self.dirpath, 'data.json')) as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return {'netuid': self.netuid, 'error': 'no snapshot yet — run `subnets sync`'}

    def config(self):
        with open(os.path.join(self.dirpath, 'config.json')) as f:
            return json.load(f)

    def _live(self, tool, fallback_key=None, **args):
        try:
            return bt_call(tool, **args)
        except Exception as e:
            snap = self.snapshot()
            out = snap.get(fallback_key) if fallback_key else snap
            return {'stale': True, 'error': str(e), 'updated': snap.get('updated'),
                    'snapshot': out}

    # -- public fns -------------------------------------------------------
    def info(self):
        """Identity + market numbers: live from bt, snapshot if bt is down."""
        try:
            rows = bt_call('bt_screener', limit=1000)['rows']
            row = next((r for r in rows if r['netuid'] == self.netuid), None)
            if row is None:
                return {**self.snapshot(), 'active': False,
                        'note': f'netuid {self.netuid} not in the live screener'}
            row.pop('spark', None)
            return {**row, 'active': True, 'stale': False}
        except Exception as e:
            return {**self.snapshot(), 'stale': True, 'error': str(e)}

    def price(self):
        i = self.info()
        return {k: i.get(k) for k in ('netuid', 'name', 'symbol', 'price', 'change_1h',
                                      'change_24h', 'change_7d', 'market_cap', 'stale')}

    def news(self, limit=20, days=30, kind=None):
        """News from bt's local scraper (GitHub, site feed, Google News, Reddit, HN, outlets)."""
        args = dict(netuid=self.netuid, limit=limit, days=days)
        if kind:
            args['kind'] = kind
        return self._live('bt_news', 'news', **args)

    def trades(self, hours=24, limit=50, side=None, min_tao=0):
        """Alpha buy/sell tape for this subnet from bt's chain-event index."""
        args = dict(netuid=self.netuid, hours=hours, limit=limit, min_tao=min_tao)
        if side:
            args['side'] = side
        return self._live('bt_trades', 'trades', **args)

    def daily(self, days_back=90):
        """Daily OHLC candles (price, mcap, pool TAO, volume, emission)."""
        return self._live('bt_daily', 'candles', netuid=self.netuid, days_back=days_back)

    def history(self, hours=168):
        """Intraday price history from bt's snapshot indexer."""
        return self._live('bt_history', None, netuid=self.netuid, hours=hours)

    def validators(self, limit=20):
        """Top validators by stake (a chain read — can take a while)."""
        return self._live('bt_validators', None, netuid=self.netuid, limit=limit)

    def neurons(self, limit=64):
        return self._live('bt_neurons', None, netuid=self.netuid, limit=limit)

    def links(self):
        c = self.config()
        return {k: c.get(k) for k in ('github', 'url', 'discord', 'logo', 'related', 'urls')}

    def readme(self):
        with open(os.path.join(self.dirpath, 'README.md')) as f:
            return f.read()

    def refresh(self):
        """Regenerate just this subnet's mod now (same code path as the daily cron)."""
        import importlib.util
        here = os.path.dirname(os.path.abspath(__file__))
        spec = importlib.util.spec_from_file_location('_subnets_mod', os.path.join(here, 'mod.py'))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m.Mod().sync(netuids=[self.netuid])
