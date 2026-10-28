"""rent2own — rent-to-own agreements you can check with a pencil.

A rent-to-own deal is four numbers and a clock: the purchase price, the
monthly rent, the share of each payment credited toward buying, and the
term. This module turns those into a month-by-month schedule — equity
built, strike price at exercise, what's left to finance — with nothing
but stdlib arithmetic, and keeps saved agreements in one JSON file under
~/.mod/rent2own. No listing API, no lender, no chain: nothing here calls out.

    m rent2own                                        # null call → info()
    m rent2own/quote price=300000 rent=2200 credit=0.25 months=36
    m rent2own/quote price=300000 rent=2200 appreciation=0.03 option_fee=6000
    m rent2own/save name="12 Elm St" price=300000 rent=2200 months=36
    m rent2own/agreements                             # saved, newest first
    m rent2own/agreement id=ab12cd34                  # one, with its schedule
    m rent2own/pay id=ab12cd34 [amount=2200]          # log a payment
    m rent2own/remove id=ab12cd34
    m rent2own/test                                   # offline tests

This is the anchor file: the orbit loader imports it by path and
instantiates ``Mod``. Every public method is a callable function.
"""

import hashlib
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.expanduser(os.environ.get('RENT2OWN_DATA', '~/.mod/rent2own'))

# The terms a quote is made of, with the defaults a bare call gets.
TERMS = {
    'price': None,         # purchase price agreed today
    'rent': None,          # monthly payment
    'credit': 0.25,        # fraction of each payment credited toward purchase
    'months': 36,          # term before the option to buy is exercised
    'option_fee': 0.0,     # paid up front, credited toward purchase
    'appreciation': 0.0,   # yearly; 0 = price locked today
}


def _num(v, name):
    try:
        return float(v)
    except (TypeError, ValueError):
        raise ValueError(f'{name} must be a number, got {v!r}')


def terms(**kw):
    """Normalize and validate deal terms. Raises ValueError on anything
    that would make the schedule meaningless."""
    t = {k: kw.get(k) if kw.get(k) is not None else d for k, d in TERMS.items()}
    for k in ('price', 'rent'):
        if t[k] is None:
            raise ValueError(f'{k}= is required')
    t = {k: _num(v, k) for k, v in t.items()}
    t['months'] = int(t['months'])
    if t['price'] <= 0 or t['rent'] <= 0:
        raise ValueError('price and rent must be positive')
    if not 0 <= t['credit'] <= 1:
        raise ValueError('credit is a fraction of rent: 0..1')
    if not 1 <= t['months'] <= 600:
        raise ValueError('months must be 1..600')
    if t['option_fee'] < 0:
        raise ValueError('option_fee cannot be negative')
    if not -0.5 < t['appreciation'] < 1:
        raise ValueError('appreciation is a yearly fraction, e.g. 0.03')
    return t


def schedule(t, paid=None):
    """Month-by-month rows for normalized terms ``t``. ``paid`` (months
    actually paid) marks where the renter really is on the curve."""
    rows = []
    equity = t['option_fee']
    for m in range(1, t['months'] + 1):
        equity += t['rent'] * t['credit']
        strike = t['price'] * (1 + t['appreciation']) ** (m / 12)
        rows.append({'month': m,
                     'equity': round(equity, 2),
                     'strike': round(strike, 2),
                     'balance': round(max(strike - equity, 0.0), 2),
                     'owned_pct': round(min(equity / strike, 1.0) * 100, 2),
                     'paid': paid is not None and m <= paid})
    return rows


def quote(**kw):
    """The whole deal at exercise, plus the schedule."""
    t = terms(**kw)
    rows = schedule(t)
    end = rows[-1]
    total_rent = t['rent'] * t['months']
    credited = end['equity'] - t['option_fee']
    return {'terms': t,
            'at_exercise': {**{k: end[k] for k in ('equity', 'strike', 'balance', 'owned_pct')},
                            'total_paid': round(total_rent + t['option_fee'], 2),
                            'rent_credited': round(credited, 2),
                            'rent_as_rent': round(total_rent - credited, 2)},
            'schedule': rows}


# ── the ledger: one JSON file, rewritten atomically ─────────────────

def _path():
    return os.path.join(DATA, 'agreements.json')


def _load():
    try:
        with open(_path()) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def _dump(book):
    os.makedirs(DATA, exist_ok=True)
    tmp = _path() + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(book, f, indent=2)
    os.replace(tmp, _path())


class Mod:
    description = """
    rent2own — rent-to-own agreements you can check with a pencil. Price,
    rent, credit share and term in; a month-by-month schedule out: equity
    built, strike price at exercise, balance left to finance. Saved deals
    and their payment logs live in one local JSON file. Stdlib only,
    nothing calls out.
    """

    def __init__(self, **kwargs):
        self.dir = HERE

    # ── plumbing ─────────────────────────────────────────────────

    def config(self):
        try:
            with open(os.path.join(HERE, 'config.json')) as f:
                return json.load(f)
        except Exception:
            return {}

    def info(self):
        """Null call — what this module is and what it exposes."""
        cfg = self.config()
        return {'name': 'rent2own', 'description': self.description.strip(),
                'version': cfg.get('version'), 'fns': cfg.get('fns', []),
                'terms': TERMS, 'data': DATA}

    forward = info

    def health(self):
        """Liveness plus the ledger at a glance. Local disk only."""
        return {'ok': True, 'agreements': len(_load()), 'data': DATA}

    def readme(self):
        """The project README."""
        p = os.path.join(HERE, 'README.md')
        if os.path.exists(p):
            with open(p) as f:
                return f.read()
        return None

    # ── the calculator ──────────────────────────────────────────

    def quote(self, price=None, rent=None, credit=None, months=None,
              option_fee=None, appreciation=None, full=False):
        """What a deal builds by exercise. full=True returns every month;
        otherwise the schedule is thinned to one row per year."""
        q = quote(price=price, rent=rent, credit=credit, months=months,
                  option_fee=option_fee, appreciation=appreciation)
        if not full:
            q['schedule'] = [r for r in q['schedule']
                             if r['month'] % 12 == 0 or r['month'] == q['terms']['months']]
        return q

    # ── the ledger ──────────────────────────────────────────────

    def save(self, name=None, price=None, rent=None, credit=None, months=None,
             option_fee=None, appreciation=None, owner=None, renter=None):
        """Persist an agreement. Content-addressed: identical terms and
        parties save to the same short id."""
        t = terms(price=price, rent=rent, credit=credit, months=months,
                  option_fee=option_fee, appreciation=appreciation)
        body = {'name': name or 'untitled', 'owner': owner, 'renter': renter, 'terms': t}
        aid = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:10]
        book = _load()
        if aid not in book:
            book[aid] = {**body, 'id': aid, 'created': int(time.time()), 'payments': []}
            _dump(book)
        return book[aid]

    def agreements(self, limit=60):
        """Saved agreements, newest first, with where each one stands."""
        rows = sorted(_load().values(), key=lambda a: a['created'], reverse=True)
        return [{k: a[k] for k in ('id', 'name', 'owner', 'renter', 'terms', 'created')}
                | {'months_paid': len(a['payments'])} for a in rows[:int(limit)]]

    def agreement(self, id=None):
        """One agreement, whole, with its schedule marked up to today."""
        a = _get(id)
        return {**a, 'schedule': schedule(a['terms'], paid=len(a['payments']))}

    def pay(self, id=None, amount=None, note=None):
        """Log one monthly payment against an agreement."""
        book = _load()
        a = _get(id, book)
        if len(a['payments']) >= a['terms']['months']:
            raise ValueError('term complete — every month is already paid')
        amt = _num(amount if amount is not None else a['terms']['rent'], 'amount')
        if amt <= 0:
            raise ValueError('amount must be positive')
        a['payments'].append({'at': int(time.time()), 'amount': amt, 'note': note})
        _dump(book)
        n = len(a['payments'])
        return {'id': a['id'], 'months_paid': n, **schedule(a['terms'])[n - 1]}

    def remove(self, id=None):
        """Delete an agreement from the local ledger."""
        book = _load()
        _get(id, book)
        del book[id]
        _dump(book)
        return {'removed': id}

    def test(self):
        """Run the module's tests (offline)."""
        r = subprocess.run([sys.executable, '-m', 'pytest', '-q', 'tests'],
                           cwd=HERE, capture_output=True, text=True)
        return {'ok': r.returncode == 0, 'output': (r.stdout + r.stderr)[-4000:]}


def _get(id, book=None):
    if not id:
        raise ValueError('which agreement? id=...')
    book = _load() if book is None else book
    if id not in book:
        raise ValueError(f'no agreement {id!r}')
    return book[id]
