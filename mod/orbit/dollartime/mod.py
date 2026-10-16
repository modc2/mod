"""dollartime — will the price be up or down in N hours? Bet on it.

Anyone joins, anyone opens a market on any USD-quoted asset for any window
(15 minutes to 30 days), anyone takes a side. The strike is the price when
the market opens; the market settles on the price when the window ends and
winners split the pot. Play money only — no wallets, no keys.

    m dollartime                                     # null call → info()
    m dollartime/join name=alice                     # → token + 1000 points
    m dollartime/create token=<t> asset=BTC hours=4 side=up amount=50
    m dollartime/bet token=<t> id=1 side=down amount=20
    m dollartime/markets status=live                 # open | locked | live | resolved | all
    m dollartime/market id=1                         # pools, payout multiples, bets
    m dollartime/me token=<t>                        # balance + your bets
    m dollartime/claim token=<t>                     # +100 once a day
    m dollartime/leaderboard
    m dollartime/serve                               # console + API on :51410
    m dollartime/test
    m dollartime/kill

This is the anchor file: the orbit loader imports it by path and
instantiates ``Mod``.
"""

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# Appended, never prepended: this directory holds mod.py, which would shadow
# the protocol's own `mod` package for anything that imports it after us.
if HERE not in sys.path:
    sys.path.append(HERE)


class Mod:
    description = """
    dollartime — up/down price prediction markets. Anyone opens a market on
    any USD-quoted asset ("BTC up or down in 4h?"), anyone takes a side, and
    it settles on the real price when the window ends. Parimutuel play money.
    """

    def __init__(self, port=None, path=None, **kwargs):
        cfg = self.config()
        self.port = int(port or os.environ.get('PORT') or cfg.get('port', 51410))
        self.base = cfg.get('base_path', '/dollartime')
        self._path = path
        self._mkt = None

    @property
    def mkt(self):
        if self._mkt is None:
            import market
            self._mkt = market.Market(self._path)
        return self._mkt

    def config(self):
        try:
            with open(os.path.join(HERE, 'config.json')) as f:
                return json.load(f)
        except Exception:
            return {}

    def info(self):
        """What this module is, and every route it serves."""
        cfg = self.config()
        return {'name': 'dollartime', 'description': self.description.strip(),
                'version': cfg.get('version'), 'port': self.port,
                'app': f'http://localhost:{self.port}{self.base}/',
                'stats': self.mkt.stats(), 'endpoints': cfg.get('endpoints', {})}

    forward = info

    def health(self):
        return {'ok': True, 'port': self.port, **self.mkt.stats()}

    def readme(self):
        p = os.path.join(HERE, 'README.md')
        return open(p).read() if os.path.exists(p) else None

    # ── players ──────────────────────────────────────────────────

    def join(self, name):
        """Pick a name; get a token (keep it) and 1000 play points."""
        return self.mkt.join(name)

    def me(self, token):
        """Your balance and your bets."""
        return self.mkt.me(token)

    def claim(self, token):
        """+100 play points, once every 24h."""
        return self.mkt.claim(token)

    def leaderboard(self, limit=25):
        return self.mkt.leaderboard(limit)

    # ── markets ──────────────────────────────────────────────────

    def price(self, asset='BTC'):
        """Spot USD price for any Coinbase-listed asset."""
        return self.mkt.price(asset)

    def create(self, token, asset, hours, side, amount, note='', bet_minutes=None):
        """Open "<asset> up or down in <hours>h?" at today's price and back a side."""
        return self.mkt.create(token, asset, hours, side, amount, note, bet_minutes)

    def bet(self, token, id, side, amount):
        """Take up or down on an open market."""
        return self.mkt.bet(token, id, side, amount)

    def markets(self, status='live', asset='', limit=50, offset=0):
        return self.mkt.markets(status, asset, limit, offset)

    def market(self, id):
        return self.mkt.market(id)

    def resolve(self):
        """Settle every market whose window has ended."""
        return self.mkt.resolve_due()

    # ── surfaces ─────────────────────────────────────────────────

    def serve(self, port=None, background=False):
        """Run the console and the API on one port."""
        port = int(port or self.port)
        if not background:
            import serve as srv
            return srv.serve(port)
        proc = subprocess.Popen([sys.executable, os.path.join(HERE, 'serve.py'),
                                 '--port', str(port)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                cwd=HERE)
        return {'pid': proc.pid, 'port': port,
                'app': f'http://localhost:{port}{self.base}/'}

    def kill(self, port=None):
        """Stop whatever holds the port. By port, never by name — a pattern
        kill on this box takes the fleet down."""
        port = int(port or self.port)
        out = subprocess.run(['bash', '-c', f'lsof -ti tcp:{port} || true'],
                             capture_output=True, text=True).stdout.split()
        for pid in out:
            subprocess.run(['kill', pid], capture_output=True)
        return {'port': port, 'killed': out}

    def test(self):
        """Offline tests in a throwaway database with a fake price feed."""
        r = subprocess.run([sys.executable, '-m', 'pytest', '-q', 'tests'],
                           cwd=HERE, capture_output=True, text=True)
        return {'ok': r.returncode == 0, 'output': (r.stdout + r.stderr)[-4000:]}
