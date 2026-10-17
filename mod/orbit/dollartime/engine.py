"""dollartime engine — the reference ledger. Same rules as the EVM and NEAR
contracts, in plain Python, so the console can run paper and the tests can
pin the numbers the contracts must reproduce.

THE RULES (identical on paper, EVM and NEAR)

  cap        21,000,000 DTT, ever. Not one more.
  emission   at most 7,200 DTT per day. Day d (from genesis) emits
             7,200 for d < 2916, then 4,800 on day 2916, then nothing:
             2916 x 7200 + 4800 = 21,000,000 exactly.
  buying     anyone puts dollars into the treasury for a lock of 0..1460
             days. weight = dollars x (365 + lock_days). The day's
             emission is split by weight: tokens = emission x w / W.
  burning    a day nobody bought is burned: its emission is gone for good
             and still counts against the cap. Nothing rolls over.
  lock       tokens from a locked contribution cannot be transferred
             until the lock ends; they still earn revenue.
  revenue    dollars the asset earns (rent, compute fees) are deposited
             and split pro-rata across every DTT in existence at that
             moment (accumulator-per-share, 1e36 precision).
  treasury   contributed dollars pool here; the steward spends them on
             the asset (the house, the machine) and sets its valuation,
             so value_of(you) = valuation x balance / supply.

Units: dollars are integer cents x 10^4 (6 decimals, like USDC);
tokens are 18-decimal integers. All arithmetic is integer.
"""

import json
import os
import secrets
import threading
import time

DAY = 86_400
CAP = 21_000_000 * 10**18
DAILY = 7_200 * 10**18
FULL_DAYS = CAP // DAILY            # 2916 full days
LAST_DAY_EMISSION = CAP - FULL_DAYS * DAILY   # 4,800 DTT on day 2916
MAX_LOCK_DAYS = 1460
ACC = 10**36          # revenue accumulator precision (1e18 was too coarse vs 1e18-decimal supply)
USD = 10**6
FAUCET = 10_000 * USD


def emission(day):
    """DTT emitted on day `day` (0-based from genesis)."""
    if day < FULL_DAYS:
        return DAILY
    if day == FULL_DAYS:
        return LAST_DAY_EMISSION
    return 0


def cumulative(days):
    """Total emission for days 0..days-1 (the closed-form schedule)."""
    if days <= FULL_DAYS:
        return days * DAILY
    return CAP


def weight_of(dollars, lock_days):
    return dollars * (365 + lock_days)


class Ledger:
    """Paper DollarTime. One asset, one treasury, one token.

    `clock` is injectable so tests can move time; state persists as JSON
    under `path` (None = memory only).
    """

    def __init__(self, path=None, clock=time.time, genesis=None):
        self.path = path
        self.clock = clock
        self.lock = threading.RLock()
        self.s = {
            'genesis': int(genesis if genesis is not None else clock()),
            'asset': {'name': 'unnamed asset', 'description': '', 'valuation': 0},
            'steward': None,
            'treasury': 0,
            'allocated': 0,            # sum of emission(d) for days with weight
            'day_weight': {},          # day -> total weight
            'next_id': 1,
            'positions': {},           # id -> position
            'accounts': {},            # name -> {token, dollars, balance, owed, debt, positions:[]}
            'tokens': {},              # token -> name
            'total_supply': 0,
            'acc_per_share': 0,
            'revenue_total': 0,
            'spends': [],
            'revenues': [],
        }
        if path and os.path.exists(path):
            with open(path) as f:
                self.s = json.load(f)

    # ── persistence ────────────────────────────────────────────────

    def _save(self):
        if not self.path:
            return
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(self.s, f)
        os.replace(tmp, self.path)

    # ── time ───────────────────────────────────────────────────────

    def now(self):
        return int(self.clock())

    def day(self, ts=None):
        ts = self.now() if ts is None else ts
        return max(0, (ts - self.s['genesis']) // DAY)

    # ── accounts (paper identity: name + token) ────────────────────

    def join(self, name):
        name = (name or '').strip()[:32]
        if not name:
            raise ValueError('name required')
        with self.lock:
            if name in self.s['accounts']:
                raise ValueError('name taken')
            tok = secrets.token_urlsafe(18)
            self.s['accounts'][name] = {'token': tok, 'dollars': FAUCET, 'balance': 0,
                                        'owed': 0, 'debt': 0, 'positions': []}
            self.s['tokens'][tok] = name
            if self.s['steward'] is None:
                self.s['steward'] = name
            self._save()
            return {'name': name, 'token': tok, 'dollars': FAUCET,
                    'steward': self.s['steward'] == name}

    def _who(self, token):
        name = self.s['tokens'].get(token or '')
        if not name:
            raise PermissionError('bad token')
        return name

    def _acct(self, name):
        return self.s['accounts'][name]

    # ── dividend accounting ────────────────────────────────────────

    def _settle_owed(self, name):
        a = self._acct(name)
        a['owed'] += a['balance'] * self.s['acc_per_share'] // ACC - a['debt']
        a['debt'] = a['balance'] * self.s['acc_per_share'] // ACC

    def _mint(self, name, amount):
        self._settle_owed(name)
        a = self._acct(name)
        a['balance'] += amount
        a['debt'] = a['balance'] * self.s['acc_per_share'] // ACC
        self.s['total_supply'] += amount

    def locked_balance(self, name, ts=None):
        ts = self.now() if ts is None else ts
        a = self._acct(name)
        return sum(self.s['positions'][str(i)]['minted'] for i in a['positions']
                   if self.s['positions'][str(i)]['unlock_at'] > ts)

    # ── the schedule ───────────────────────────────────────────────

    def burned(self):
        d = self.day()
        allocated_closed = self.s['allocated']
        if self.s['day_weight'].get(str(d), 0) > 0:
            allocated_closed -= emission(d)
        return cumulative(d) - allocated_closed

    def stats(self):
        d = self.day()
        dw = self.s['day_weight'].get(str(d), 0)
        return {
            'now': self.now(), 'genesis': self.s['genesis'], 'day': d,
            'day_ends_in': self.s['genesis'] + (d + 1) * DAY - self.now(),
            'emission_today': emission(d), 'weight_today': dw,
            'emitted': cumulative(d), 'burned': self.burned(),
            'total_supply': self.s['total_supply'], 'cap': CAP, 'daily': DAILY,
            'days_left': max(0, FULL_DAYS + 1 - d),
            'treasury': self.s['treasury'], 'revenue_total': self.s['revenue_total'],
            'acc_per_share': self.s['acc_per_share'],
            'asset': self.s['asset'], 'steward': self.s['steward'],
            'positions': len(self.s['positions']), 'accounts': len(self.s['accounts']),
            'max_lock_days': MAX_LOCK_DAYS,
        }

    # ── contribute / claim ─────────────────────────────────────────

    def contribute(self, token, dollars, lock_days=0):
        dollars, lock_days = int(dollars), int(lock_days)
        if dollars <= 0:
            raise ValueError('dollars must be > 0')
        if not 0 <= lock_days <= MAX_LOCK_DAYS:
            raise ValueError(f'lock_days must be 0..{MAX_LOCK_DAYS}')
        with self.lock:
            name = self._who(token)
            a = self._acct(name)
            if a['dollars'] < dollars:
                raise ValueError('not enough dollars')
            d = self.day()
            if emission(d) == 0:
                raise ValueError('emission is over: the cap is reached')
            w = weight_of(dollars, lock_days)
            key = str(d)
            if self.s['day_weight'].get(key, 0) == 0:
                self.s['allocated'] += emission(d)
            self.s['day_weight'][key] = self.s['day_weight'].get(key, 0) + w
            a['dollars'] -= dollars
            self.s['treasury'] += dollars
            pid = self.s['next_id']
            self.s['next_id'] += 1
            pos = {'id': pid, 'owner': name, 'day': d, 'dollars': dollars,
                   'lock_days': lock_days, 'weight': w, 'at': self.now(),
                   'unlock_at': self.now() + lock_days * DAY,
                   'claimed': False, 'minted': 0}
            self.s['positions'][str(pid)] = pos
            a['positions'].append(pid)
            self._save()
            return dict(pos, share_today=w / self.s['day_weight'][key])

    def claimable(self, pid):
        pos = self.s['positions'].get(str(int(pid)))
        if not pos or pos['claimed'] or pos['day'] >= self.day():
            return 0
        return emission(pos['day']) * pos['weight'] // self.s['day_weight'][str(pos['day'])]

    def claim(self, token, pid=None):
        """Mint the tokens a closed day owes you. No id = claim all."""
        with self.lock:
            name = self._who(token)
            ids = [int(pid)] if pid is not None else list(self._acct(name)['positions'])
            out = []
            for i in ids:
                pos = self.s['positions'].get(str(i))
                if not pos or pos['owner'] != name:
                    if pid is not None:
                        raise ValueError('not your position')
                    continue
                if pos['claimed'] or pos['day'] >= self.day():
                    continue
                amt = self.claimable(i)
                pos['claimed'] = True
                pos['minted'] = amt
                self._mint(name, amt)
                out.append({'id': i, 'minted': amt})
            self._save()
            return {'claimed': out, 'balance': self._acct(name)['balance']}

    # ── token transfers (lock-aware) ───────────────────────────────

    def transfer(self, token, to, amount):
        amount = int(amount)
        if amount <= 0:
            raise ValueError('amount must be > 0')
        with self.lock:
            name = self._who(token)
            if to not in self.s['accounts']:
                raise ValueError('unknown recipient')
            a = self._acct(name)
            if a['balance'] - self.locked_balance(name) < amount:
                raise ValueError('amount exceeds unlocked balance')
            self._settle_owed(name)
            self._settle_owed(to)
            a['balance'] -= amount
            self._acct(to)['balance'] += amount
            for n in (name, to):
                x = self._acct(n)
                x['debt'] = x['balance'] * self.s['acc_per_share'] // ACC
            self._save()
            return {'from': name, 'to': to, 'amount': amount}

    # ── revenue ────────────────────────────────────────────────────

    def deposit_revenue(self, token, dollars, memo=''):
        dollars = int(dollars)
        if dollars <= 0:
            raise ValueError('dollars must be > 0')
        with self.lock:
            name = self._who(token)
            a = self._acct(name)
            if a['dollars'] < dollars:
                raise ValueError('not enough dollars')
            a['dollars'] -= dollars
            if self.s['total_supply'] == 0:
                self.s['treasury'] += dollars      # nobody to pay yet: pool it
                where = 'treasury'
            else:
                self.s['acc_per_share'] += dollars * ACC // self.s['total_supply']
                where = 'holders'
            self.s['revenue_total'] += dollars
            self.s['revenues'].append({'from': name, 'dollars': dollars, 'memo': memo,
                                       'at': self.now(), 'to': where})
            self._save()
            return {'dollars': dollars, 'to': where, 'acc_per_share': self.s['acc_per_share']}

    def owed(self, name):
        a = self._acct(name)
        return a['owed'] + a['balance'] * self.s['acc_per_share'] // ACC - a['debt']

    def withdraw_revenue(self, token):
        with self.lock:
            name = self._who(token)
            self._settle_owed(name)
            a = self._acct(name)
            amt, a['owed'] = a['owed'], 0
            a['dollars'] += amt
            self._save()
            return {'withdrawn': amt, 'dollars': a['dollars']}

    # ── steward: the treasury buys the asset ───────────────────────

    def _steward(self, token):
        name = self._who(token)
        if name != self.s['steward']:
            raise PermissionError('steward only')
        return name

    def set_asset(self, token, name=None, description=None, valuation=None):
        with self.lock:
            self._steward(token)
            if name is not None:
                self.s['asset']['name'] = str(name)[:80]
            if description is not None:
                self.s['asset']['description'] = str(description)[:500]
            if valuation is not None:
                self.s['asset']['valuation'] = int(valuation)
            self._save()
            return self.s['asset']

    def spend(self, token, to, dollars, memo=''):
        dollars = int(dollars)
        with self.lock:
            self._steward(token)
            if dollars <= 0 or dollars > self.s['treasury']:
                raise ValueError('amount exceeds treasury')
            self.s['treasury'] -= dollars
            if to in self.s['accounts']:
                self._acct(to)['dollars'] += dollars
            rec = {'to': to, 'dollars': dollars, 'memo': memo, 'at': self.now()}
            self.s['spends'].append(rec)
            self._save()
            return dict(rec, treasury=self.s['treasury'])

    # ── views ──────────────────────────────────────────────────────

    def me(self, token):
        name = self._who(token)
        a = self._acct(name)
        ts = self.s['total_supply']
        bal = a['balance']
        val = self.s['asset']['valuation']
        return {
            'name': name, 'dollars': a['dollars'], 'balance': bal,
            'locked': self.locked_balance(name), 'owed': self.owed(name),
            'share_bps': bal * 10000 // ts if ts else 0,
            'value': val * bal // ts if ts else 0,
            'steward': name == self.s['steward'],
            'positions': [dict(self.s['positions'][str(i)], claimable=self.claimable(i))
                          for i in a['positions']],
        }

    def holders(self, limit=50):
        ts = self.s['total_supply']
        rows = [{'name': n, 'balance': a['balance'],
                 'share_bps': a['balance'] * 10000 // ts if ts else 0}
                for n, a in self.s['accounts'].items()]
        rows.sort(key=lambda r: -r['balance'])
        return rows[:int(limit)]

    def days(self, limit=30):
        d = self.day()
        out = []
        for k in range(max(0, d - int(limit) + 1), d + 1):
            w = self.s['day_weight'].get(str(k), 0)
            e = emission(k)
            out.append({'day': k, 'emission': e, 'weight': w,
                        'state': 'open' if k == d else ('sold' if w else 'burned')})
        return out

    def history(self, limit=50):
        return {'spends': self.s['spends'][-int(limit):],
                'revenues': self.s['revenues'][-int(limit):]}
