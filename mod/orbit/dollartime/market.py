"""dollartime engine — up/down price prediction markets, play money.

Anyone joins with a name and gets a token plus a starting balance. Anyone
opens a market on any USD-quoted asset ("BTC up or down in the next N
hours?"), backing a side with a stake. Anyone else takes UP or DOWN while
betting is open. When the window ends the market resolves against the price
at close time, and the pot is split parimutuel: winners share the whole pool
in proportion to their stakes.

    open price  — spot at creation (the strike)
    lock_at     — betting closes (default: first quarter of the window)
    close_at    — open_at + hours; close price = 1-minute candle at that time
    outcome     — up (close > open), down (close < open), flat (equal)
    flat, void, or nobody on the winning side → every stake is refunded

Prices come from Coinbase's keyless public API. Pass ``price_fn`` /
``price_at_fn`` to swap the source (the tests do).
"""

import hashlib
import json
import os
import secrets
import sqlite3
import threading
import time
import urllib.request

START_BALANCE = 1000.0
CLAIM_AMOUNT = 100.0
CLAIM_EVERY = 24 * 3600
MIN_HOURS, MAX_HOURS = 0.25, 24 * 30
MIN_STAKE = 1.0
VOID_AFTER = 24 * 3600   # give up fetching a close price after this long
SIDES = ('up', 'down')

UA = {'User-Agent': 'dollartime/0.1'}


# ── prices ──────────────────────────────────────────────────────────

def _get(url, timeout=8):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def coinbase_spot(asset):
    d = _get(f'https://api.coinbase.com/v2/prices/{asset}-USD/spot')
    return float(d['data']['amount'])


def coinbase_price_at(asset, ts):
    """Close of the 1-minute candle that contains ``ts`` (None if not out yet)."""
    start = int(ts // 60 * 60)
    fmt = lambda t: time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(t))  # noqa: E731
    rows = _get('https://api.exchange.coinbase.com/products/'
                f'{asset}-USD/candles?granularity=60'
                f'&start={fmt(start)}&end={fmt(start + 60)}')
    # rows: [time, low, high, open, close, volume], newest first
    for row in rows:
        if int(row[0]) == start:
            return float(row[4])
    return None


# ── store ───────────────────────────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
    name TEXT PRIMARY KEY,
    token_hash TEXT NOT NULL,
    balance REAL NOT NULL,
    created_at REAL NOT NULL,
    claimed_at REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS markets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset TEXT NOT NULL,
    hours REAL NOT NULL,
    creator TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    open_price REAL NOT NULL,
    open_at REAL NOT NULL,
    lock_at REAL NOT NULL,
    close_at REAL NOT NULL,
    close_price REAL,
    outcome TEXT,
    resolved_at REAL
);
CREATE TABLE IF NOT EXISTS bets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id INTEGER NOT NULL REFERENCES markets(id),
    player TEXT NOT NULL REFERENCES players(name),
    side TEXT NOT NULL,
    amount REAL NOT NULL,
    at REAL NOT NULL,
    payout REAL
);
CREATE INDEX IF NOT EXISTS bets_market ON bets(market_id);
CREATE INDEX IF NOT EXISTS bets_player ON bets(player);
CREATE INDEX IF NOT EXISTS markets_open ON markets(outcome, close_at);
"""


def default_path():
    d = os.path.expanduser(os.environ.get('DOLLARTIME_DIR', '~/.mod/dollartime'))
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, 'dollartime.db')


def _hash(token):
    return hashlib.sha256((token or '').encode()).hexdigest()


def _norm_asset(asset):
    a = (asset or '').strip().upper()
    if a.endswith('-USD'):
        a = a[:-4]
    if not a or len(a) > 12 or not a.replace('.', '').isalnum():
        raise ValueError('asset must be a ticker like BTC, ETH, SOL')
    return a


class Market:
    def __init__(self, path=None, price_fn=None, price_at_fn=None, now_fn=None):
        self.path = path or default_path()
        self.price_fn = price_fn or coinbase_spot
        self.price_at_fn = price_at_fn or coinbase_price_at
        self.now = now_fn or time.time
        self.lock = threading.RLock()
        self._spot = {}   # asset -> (ts, price), 10s cache
        with self._db() as db:
            db.executescript(SCHEMA)

    def _db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    # ── players ─────────────────────────────────────────────────────

    def join(self, name):
        """New player: returns a token (shown once) and the starting balance."""
        name = (name or '').strip()
        if not (1 <= len(name) <= 32) or not all(c.isalnum() or c in '-_.' for c in name):
            raise ValueError('name: 1-32 chars of letters, digits, - _ .')
        token = secrets.token_urlsafe(24)
        with self.lock, self._db() as db:
            if db.execute('SELECT 1 FROM players WHERE lower(name)=lower(?)', (name,)).fetchone():
                raise ValueError(f'name {name!r} is taken')
            db.execute('INSERT INTO players VALUES (?,?,?,?,0)',
                       (name, _hash(token), START_BALANCE, self.now()))
        return {'name': name, 'token': token, 'balance': START_BALANCE,
                'note': 'keep the token — it is the only way to bet as this name'}

    def _player(self, db, token):
        p = db.execute('SELECT * FROM players WHERE token_hash=?', (_hash(token),)).fetchone()
        if not p:
            raise ValueError('unknown token — join first')
        return p

    def me(self, token):
        """Your balance and your bets."""
        self.resolve_due()
        with self._db() as db:
            p = self._player(db, token)
            bets = [dict(r) for r in db.execute(
                'SELECT b.*, m.asset, m.hours, m.outcome, m.close_at FROM bets b '
                'JOIN markets m ON m.id=b.market_id WHERE b.player=? '
                'ORDER BY b.id DESC LIMIT 200', (p['name'],))]
        now = self.now()
        return {'name': p['name'], 'balance': round(p['balance'], 2),
                'can_claim': now - p['claimed_at'] >= CLAIM_EVERY,
                'bets': bets}

    def claim(self, token):
        """Daily top-up of play money."""
        with self.lock, self._db() as db:
            p = self._player(db, token)
            wait = CLAIM_EVERY - (self.now() - p['claimed_at'])
            if wait > 0:
                raise ValueError(f'next claim in {int(wait // 3600)}h {int(wait % 3600 // 60)}m')
            db.execute('UPDATE players SET balance=balance+?, claimed_at=? WHERE name=?',
                       (CLAIM_AMOUNT, self.now(), p['name']))
            bal = db.execute('SELECT balance FROM players WHERE name=?',
                             (p['name'],)).fetchone()[0]
        return {'name': p['name'], 'balance': round(bal, 2)}

    def leaderboard(self, limit=25):
        with self._db() as db:
            rows = db.execute(
                'SELECT p.name, p.balance, '
                '  (SELECT count(*) FROM bets b WHERE b.player=p.name) AS bets, '
                '  (SELECT count(*) FROM bets b WHERE b.player=p.name AND b.payout>b.amount) AS wins '
                'FROM players p ORDER BY p.balance DESC LIMIT ?', (int(limit),)).fetchall()
        return {'players': [dict(r, balance=round(r['balance'], 2)) for r in rows]}

    # ── prices ──────────────────────────────────────────────────────

    def price(self, asset):
        """Spot USD price (10s cache)."""
        a = _norm_asset(asset)
        hit = self._spot.get(a)
        if hit and self.now() - hit[0] < 10:
            return {'asset': a, 'price': hit[1]}
        try:
            p = float(self.price_fn(a))
        except Exception as e:  # noqa: BLE001
            raise ValueError(f'no USD price for {a} ({type(e).__name__})')
        if p <= 0:
            raise ValueError(f'no USD price for {a}')
        self._spot[a] = (self.now(), p)
        return {'asset': a, 'price': p}

    # ── markets ─────────────────────────────────────────────────────

    def create(self, token, asset, hours, side, amount, note='', bet_minutes=None):
        """Open a market — "will <asset> be up or down in <hours>h?" — and
        back your side with a stake. The strike is the price right now."""
        a = _norm_asset(asset)
        hours = float(hours)
        if not (MIN_HOURS <= hours <= MAX_HOURS):
            raise ValueError(f'hours must be {MIN_HOURS}..{MAX_HOURS}')
        window = hours * 3600
        bet_window = (float(bet_minutes) * 60 if bet_minutes not in (None, '')
                      else window / 4)
        if not (60 <= bet_window <= window):
            raise ValueError('bet_minutes must be at least 1 and within the window')
        side, amount = self._check_bet(side, amount)
        open_price = self.price(a)['price']
        now = self.now()
        with self.lock, self._db() as db:
            p = self._player(db, token)
            self._debit(db, p, amount)
            cur = db.execute(
                'INSERT INTO markets (asset, hours, creator, note, open_price, '
                'open_at, lock_at, close_at) VALUES (?,?,?,?,?,?,?,?)',
                (a, hours, p['name'], (note or '')[:280], open_price,
                 now, now + bet_window, now + window))
            mid = cur.lastrowid
            db.execute('INSERT INTO bets (market_id, player, side, amount, at) '
                       'VALUES (?,?,?,?,?)', (mid, p['name'], side, amount, now))
        return self.market(mid)

    def bet(self, token, id, side, amount):
        """Take UP or DOWN on an open market."""
        side, amount = self._check_bet(side, amount)
        with self.lock, self._db() as db:
            m = db.execute('SELECT * FROM markets WHERE id=?', (int(id),)).fetchone()
            if not m:
                raise ValueError(f'no market {id}')
            if m['outcome'] or self.now() >= m['lock_at']:
                raise ValueError('betting is closed on this market')
            p = self._player(db, token)
            self._debit(db, p, amount)
            db.execute('INSERT INTO bets (market_id, player, side, amount, at) '
                       'VALUES (?,?,?,?,?)', (m['id'], p['name'], side, amount, self.now()))
        return self.market(m['id'])

    def _check_bet(self, side, amount):
        side = (side or '').strip().lower()
        if side not in SIDES:
            raise ValueError('side must be up or down')
        amount = round(float(amount), 2)
        if amount < MIN_STAKE:
            raise ValueError(f'stake must be at least {MIN_STAKE:g}')
        return side, amount

    def _debit(self, db, p, amount):
        if p['balance'] + 1e-9 < amount:
            raise ValueError(f"not enough balance ({p['balance']:.2f})")
        db.execute('UPDATE players SET balance=balance-? WHERE name=?', (amount, p['name']))

    def _status(self, m, now):
        if m['outcome']:
            return 'resolved'
        if now < m['lock_at']:
            return 'open'
        if now < m['close_at']:
            return 'locked'
        return 'resolving'

    def _shape(self, db, m, now, with_bets=False):
        pools = {s: 0.0 for s in SIDES}
        n = 0
        for r in db.execute('SELECT side, sum(amount) s, count(*) c FROM bets '
                            'WHERE market_id=? GROUP BY side', (m['id'],)):
            pools[r['side']] = round(r['s'], 2)
            n += r['c']
        total = pools['up'] + pools['down']
        out = dict(m)
        out.update(status=self._status(m, now), pools=pools, total=round(total, 2),
                   bettors=n,
                   # parimutuel multiple if you win on that side, as of now
                   pays={s: (round(total / pools[s], 3) if pools[s] else None) for s in SIDES},
                   question=f"{m['asset']} up or down {m['hours']:g}h after "
                            f"${m['open_price']:,.6g}?")
        if with_bets:
            out['bets'] = [dict(r) for r in db.execute(
                'SELECT player, side, amount, at, payout FROM bets '
                'WHERE market_id=? ORDER BY id', (m['id'],))]
        return out

    def market(self, id):
        """One market with its pools, payout multiples and every bet."""
        self.resolve_due()
        with self._db() as db:
            m = db.execute('SELECT * FROM markets WHERE id=?', (int(id),)).fetchone()
            if not m:
                raise ValueError(f'no market {id}')
            return self._shape(db, m, self.now(), with_bets=True)

    def markets(self, status='live', asset='', limit=50, offset=0):
        """List markets. status = open | locked | live (open+locked) | resolved | all."""
        self.resolve_due()
        now = self.now()
        where, args = [], []
        if status in ('open',):
            where.append('outcome IS NULL AND lock_at > ?'); args.append(now)
        elif status == 'locked':
            where.append('outcome IS NULL AND lock_at <= ?'); args.append(now)
        elif status == 'live':
            where.append('outcome IS NULL')
        elif status == 'resolved':
            where.append('outcome IS NOT NULL')
        elif status != 'all':
            raise ValueError('status: open | locked | live | resolved | all')
        if asset:
            where.append('asset=?'); args.append(_norm_asset(asset))
        sql = 'SELECT * FROM markets' + (' WHERE ' + ' AND '.join(where) if where else '')
        sql += (' ORDER BY close_at ASC' if status in ('open', 'locked', 'live')
                else ' ORDER BY id DESC') + ' LIMIT ? OFFSET ?'
        args += [int(limit), int(offset)]
        with self._db() as db:
            return {'markets': [self._shape(db, m, now) for m in db.execute(sql, args)]}

    # ── resolution ──────────────────────────────────────────────────

    def resolve_due(self):
        """Settle every market whose window has ended. Safe to call anytime."""
        now = self.now()
        with self._db() as db:
            due = [dict(r) for r in db.execute(
                'SELECT * FROM markets WHERE outcome IS NULL AND close_at <= ?', (now,))]
        done = []
        for m in due:
            price = None
            try:
                price = self.price_at_fn(m['asset'], m['close_at'])
            except Exception:  # noqa: BLE001 — retry on the next pass
                pass
            if price is None and now - m['close_at'] < 120:
                try:
                    price = float(self.price_fn(m['asset']))
                except Exception:  # noqa: BLE001
                    pass
            if price is None and now - m['close_at'] < VOID_AFTER:
                continue
            done.append(self._settle(m['id'], price))
        return {'resolved': [d for d in done if d]}

    def _settle(self, mid, close_price):
        with self.lock, self._db() as db:
            m = db.execute('SELECT * FROM markets WHERE id=?', (mid,)).fetchone()
            if not m or m['outcome']:
                return None   # someone else got there first
            if close_price is None:
                outcome = 'void'
            elif close_price > m['open_price']:
                outcome = 'up'
            elif close_price < m['open_price']:
                outcome = 'down'
            else:
                outcome = 'flat'
            bets = db.execute('SELECT * FROM bets WHERE market_id=?', (mid,)).fetchall()
            total = sum(b['amount'] for b in bets)
            win = sum(b['amount'] for b in bets if b['side'] == outcome)
            for b in bets:
                if win > 0:   # parimutuel: winners split the whole pot
                    pay = round(b['amount'] * total / win, 2) if b['side'] == outcome else 0.0
                else:         # flat, void, or nobody took the winning side
                    pay = b['amount']
                db.execute('UPDATE bets SET payout=? WHERE id=?', (pay, b['id']))
                if pay:
                    db.execute('UPDATE players SET balance=balance+? WHERE name=?',
                               (pay, b['player']))
            db.execute('UPDATE markets SET close_price=?, outcome=?, resolved_at=? '
                       'WHERE id=?', (close_price, outcome, self.now(), mid))
        return {'id': mid, 'outcome': outcome, 'close_price': close_price,
                'refunded': win == 0}

    def stats(self):
        with self._db() as db:
            r = db.execute(
                'SELECT (SELECT count(*) FROM players) players, '
                '(SELECT count(*) FROM markets) markets, '
                '(SELECT count(*) FROM markets WHERE outcome IS NULL) live, '
                '(SELECT coalesce(sum(amount),0) FROM bets) volume').fetchone()
        return dict(r)
