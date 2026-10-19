"""
simulate.py — run the whole protocol, years at a time, through the real code.

A simulation is not a spreadsheet of the deal: it is the deal, played. A
throwaway Mod gets a clock of its own and then does exactly what the live
node does — the owner claims the seat and sets the terms, renters pay rent
by ordinary bank transfer (sandbox bank → reference code → reconcile →
pay_rent), the fee pools and every 90 days a quarter closes and splits the
pool by bloctime, and the ledger refuses the first payment past the home
price. Every number that comes back was produced by the same functions the
MCP tools and the REST API call, with the same rounding and the same
refusals; the only thing the simulation fabricates is time.

    run(Mod, years=10, home_price=120, monthly_rent=0.62, ...) -> dict
        params     what actually ran (after clamping)
        months     one row per month: paid, fee, credit, owner income,
                   principal to date, % owned, modeled yield, what happened
        quarters   every quarter the protocol closed, with its payout table
        end        where it landed: each renter's equity, the owner's income,
                   fees, months to own, the stage reached
        versus     the same money under a plain lease and a 15% platform
        bank       the sandbox bank's own view: transfers, booked, unmatched
        events     the notable moments (option fee, skipped months, quarter
                   closes, the month the house became theirs)

Two things are modelled rather than played, and say so in the output:
the owner's lowfi yield (``apy``) — the protocol doesn't hold the owner's
money, so there is no ledger to read it from — and the fiat rate, which is
pinned for the run so the story stays deterministic.

Deterministic on purpose: fixed cast (demo.py), fixed rate, fixed clock
steps, and a seeded choice of which months get skipped.
"""
import hashlib
import importlib.util
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).parent
DAY = 24 * 3600
MONTH = int(365.25 / 12 * DAY)          # the simulation's month: 30.44 days


def _load(name, path):
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        m = importlib.util.module_from_spec(spec)
        sys.modules[name] = m
        spec.loader.exec_module(m)
    return sys.modules[name]


CAST = _load('openhouse_demo_cast', HERE / 'demo.py').CAST
RENTERS = [('maya', 'Maya Ortiz', 'XT279876543210987654'),
           ('jonah', 'Jonah Reyes', 'XT611234567890123456'),
           ('ada', 'Ada Lindqvist', 'XT720000111122223333'),
           ('felix', 'Felix Brandt', 'XT150000999988887777')]

# What a visitor can turn, and how far. Anything outside the band is clamped,
# not refused — the run always happens, and `params` says what actually ran.
LIMITS = {
    'years': (1, 40, 10),
    'home_price': (0.01, 1e6, 120.0),
    'monthly_rent': (0.0001, 1e5, 0.62),
    'fee_pct': (0.0, 5.0, 2.5),
    'credit_pct': (0.0, 100.0, None),      # None: take the model's
    'option_fee_pct': (0.0, 100.0, None),
    'renters': (1, 4, 1),
    'apy': (0.0, 50.0, 5.0),
    'rate': (1.0, 1e9, 2500.0),
    'skip_every': (0, 120, 0),
    'proofs': (0, 24, 0),
}
MODELS = ('full_credit', 'hybrid', 'classic', 'lease')

PRESETS = [
    {'id': 'first_home', 'title': 'A first home, paid off', 'years': 20, 'model': 'full_credit',
     'fee_pct': 2.5, 'renters': 1, 'apy': 5.0,
     'shows': 'Full credit at the default fee: the whole price, one check at a time.'},
    {'id': 'zero_fee', 'title': 'An owner who takes nothing', 'years': 20, 'model': 'full_credit',
     'fee_pct': 0.0, 'renters': 1, 'apy': 5.0,
     'shows': 'Fee 0 — no pool, no quarters to split, every net dollar is a brick.'},
    {'id': 'housemates', 'title': 'Three housemates, one deed', 'years': 20, 'model': 'full_credit',
     'fee_pct': 2.5, 'renters': 3, 'apy': 5.0,
     'shows': 'Rent split three ways; equity lands pro-rata on whoever actually paid.'},
    {'id': 'hybrid', 'title': 'The honest hybrid', 'years': 20, 'model': 'hybrid',
     'fee_pct': 2.5, 'renters': 1, 'apy': 5.0,
     'shows': 'Half equity, half income — the owner still carries a mortgage.'},
    {'id': 'lease_option', 'title': 'Classic lease-option', 'years': 10, 'model': 'classic',
     'fee_pct': 2.5, 'renters': 1, 'apy': 5.0,
     'shows': 'A 3% option fee up front, then 25% of every month credits.'},
    {'id': 'life_happens', 'title': 'Life happens', 'years': 20, 'model': 'full_credit',
     'fee_pct': 2.5, 'renters': 1, 'apy': 5.0, 'skip_every': 11,
     'shows': 'A month missed every eleven: equity pauses, the deal does not break.'},
    {'id': 'plain_lease', 'title': 'The plain lease, for contrast', 'years': 10, 'model': 'lease',
     'fee_pct': 2.5, 'renters': 1, 'apy': 5.0,
     'shows': 'No credit at all — and still 97.5% stays with the home instead of a platform.'},
]


def _clamp(name, value):
    lo, hi, default = LIMITS[name]
    if value in (None, ''):
        return default
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    v = max(lo, min(hi, v))
    return int(v) if isinstance(lo, int) else v


def presets() -> list:
    return [dict(p) for p in PRESETS]


class _MemStore:
    """The throwaway Mod's JSON files, kept in memory.

    Thirty years of three renters is over a thousand ledger writes, and the
    live code re-reads the ledger several times per payment. On disk that is
    hundreds of megabytes of JSON for one request; in memory it is nothing.
    The code paths are untouched — only _load_json/_save_json are redirected,
    which is the whole storage layer.
    """

    def __init__(self):
        self.files = {}

    def load(self, path, default=None):
        v = self.files.get(str(path))
        if v is None:
            return default if default is not None else {}
        # Callers mutate what they load and then save it; a shallow copy
        # keeps "forgot to save" from silently sticking, cheaply.
        return list(v) if isinstance(v, list) else dict(v)

    def save(self, path, data):
        self.files[str(path)] = data


def run(Mod, **kw) -> dict:
    t_wall = time.time()
    p = {k: _clamp(k, kw.get(k)) for k in LIMITS}
    p['model'] = kw.get('model') if kw.get('model') in MODELS else 'full_credit'
    p['bank'] = kw.get('bank', True) not in (False, 0, '0', 'false', 'no')
    p['currency'] = str(kw.get('currency') or 'USD').upper()[:3]
    seed = str(kw.get('seed') or 'openhouse')
    months = p['years'] * 12
    start = int(kw.get('start') or time.time())
    start -= start % DAY                     # midnight, so periods are clean

    clock = {'t': start}
    events, rows, quarters = [], [], []
    with tempfile.TemporaryDirectory(prefix='openhouse-sim-') as d:
        oh = Mod(store=d)
        oh._clock = lambda: clock['t']
        mem = _MemStore()
        oh._load_json, oh._save_json = mem.load, mem.save

        owner = CAST['owner']
        cast = RENTERS[:p['renters']]
        oh.claim_owner(owner)
        terms = dict(model=p['model'], fee_pct=p['fee_pct'], home_price=p['home_price'],
                     monthly_rent=p['monthly_rent'], owner=owner, treasury=CAST['treasury'])
        if p['credit_pct'] is not None:
            terms['credit_pct'] = p['credit_pct']
        if p['option_fee_pct'] is not None:
            terms['option_fee_pct'] = p['option_fee_pct']
        t = oh.set_terms(**terms)
        if 'error' in t:
            return {'error': t['error']}
        live = t['terms']
        p['credit_pct'] = live['credit_pct']
        p['option_fee_pct'] = live['option_fee_pct']

        refs = {}
        if p['bank']:
            _bank_in_memory(oh)
            oh.bank_connect(kind='sandbox', name='landlord',
                            config={'currency': p['currency'], 'holder': 'DEMO Landlord'})
            for who, name, iban in cast:
                oh.bank_link(CAST[who], payer_iban=iban, payer_name=name)
                refs[who] = oh.bank_reference(CAST[who])['reference']

        def pay(who, amount, kind='rent', note='rent'):
            """One payment, the way a renter actually makes it."""
            if p['bank']:
                fiat = round(amount * p['rate'], 2)
                _, name, iban = next(c for c in cast if c[0] == who)
                oh.bank_receive(fiat, reference=f'{note} {refs[who]}', from_name=name,
                                from_iban=iban, date=clock['t'])
                return None
            return oh.pay_rent(CAST[who], amount, kind=kind)

        def settle(issue):
            if not p['bank']:
                return {'booked': [], 'held': [], 'unmatched': []}
            return oh.bank_reconcile(rate=p['rate'], since=clock['t'] - 45 * DAY,
                                     issue_proofs=issue)

        # The option fee, if the model wants one: the first renter pays it on
        # move-in day, and it is all equity.
        option_due = float(live['home_price']) * float(live['option_fee_pct']) / 100.0
        if option_due > 0:
            if p['bank']:
                pay(cast[0][0], option_due, note='option fee')
                settle(False)
            else:
                oh.pay_rent(CAST[cast[0][0]], option_due, kind='option')
            events.append({'month': 0, 'kind': 'option', 'who': cast[0][0],
                           'amount': round(option_due, 8),
                           'says': f'{cast[0][1].split()[0]} pays the {live["option_fee_pct"]}% option fee — all of it equity.'})

        share = p['monthly_rent'] / len(cast)
        bal = 0.0                      # modelled: principal sitting in lowfi
        yield_cum = 0.0
        r_month = p['apy'] / 100.0 / 12.0
        owned_month = None
        proofs_left = p['proofs']
        prev_principal = oh._principal_paid_total()

        for m in range(1, months + 1):
            clock['t'] = start + m * MONTH
            skipped, paid_by = [], []
            for who, name, _ in cast:
                h = hashlib.sha256(f'{seed}:{who}:{m}'.encode()).digest()[0]
                if p['skip_every'] and (m + h) % p['skip_every'] == 0:
                    skipped.append(who)
                    continue
                paid_by.append(who)
                res = pay(who, share)
                if res is not None and 'error' in res:
                    # Only one refusal exists on this path: the house is paid.
                    paid_by.pop()
            issue = proofs_left > 0
            rec = settle(issue)
            if issue:
                proofs_left -= len(rec.get('booked', []))
            for h_ in rec.get('held', []):
                paid_by = [w for w in paid_by if CAST[w].lower() != h_['renter'].lower()]

            st = oh.rent_stats()
            principal = st['renter_equity']
            credit_m = principal - prev_principal
            prev_principal = principal

            # The owner's lowfi yield is a model, not a ledger line: the
            # protocol never holds the owner's money.
            bal += credit_m
            interest = bal * r_month
            yield_cum += interest
            bal += interest

            # A quarter closes itself once 90 days have run.
            pool = oh.pool()
            if pool['ready'] and not pool['zero_fee']:
                q = oh.close_quarter(caller=owner)
                if q.get('success'):
                    rec_q = q['quarter']
                    quarters.append({
                        'quarter': rec_q['quarter'], 'month': m, 'pool': rec_q['pool'],
                        'total_weight_days': rec_q['total_weight_days'],
                        'allocations': [{'who': _who(a['address']), 'address': a['address'],
                                         'kinds': a['kinds'], 'share_pct': a['share_pct'],
                                         'amount': a['amount']} for a in rec_q['allocations']],
                    })
                    events.append({'month': m, 'kind': 'quarter', 'quarter': rec_q['quarter'],
                                   'amount': rec_q['pool'],
                                   'says': f'Quarter {rec_q["quarter"]} closes: {rec_q["pool"]:.4f} Ξ of fees split by bloctime.'})
            elif pool['ready'] and pool['zero_fee']:
                # nothing to split at 0% — the clock simply rolls on
                oh.close_quarter(caller=owner)

            for who in skipped:
                events.append({'month': m, 'kind': 'skipped', 'who': who,
                               'says': f'{dict((c[0], c[1]) for c in cast)[who].split()[0]} misses month {m}. Nothing is lost — nothing is gained.'})

            owned = st['owned_pct'] >= 100.0 - 1e-9
            rows.append({
                'm': m, 't': clock['t'], 'period': _period(clock['t']),
                'paid': round(st['gross_rent'], 8), 'fees': round(st['protocol_fees'], 8),
                'equity': round(principal, 8), 'owner_income': round(st['owner_income'], 8),
                'credit': round(credit_m, 8),
                'owned_pct': round(min(st['owned_pct'], 100.0), 4),
                'remaining': round(max(p['home_price'] - principal, 0.0), 8),
                'yield': round(yield_cum, 8),
                'paid_by': paid_by, 'skipped': skipped,
            })
            if owned and owned_month is None:
                owned_month = m
                events.append({'month': m, 'kind': 'owned',
                               'says': f'Month {m}: the ledger refuses the next payment — the house is paid for.'})
                break

        end_stats = oh.rent_stats()
        equities = []
        for who, name, _ in cast:
            e = oh.equity(CAST[who])
            equities.append({'who': who, 'name': name, 'address': CAST[who],
                             'payments': e['payments'], 'rent_paid': e['rent_paid'],
                             'principal': e['principal'], 'equity_pct': e['equity_pct'],
                             'fees_paid': e['fees_paid']})
        bank = None
        if p['bank']:
            bs = oh.bank_status()
            accts = oh.bank_accounts()
            accts = accts.get('accounts', []) if isinstance(accts, dict) else accts
            txns = oh.bank_transactions(limit=100000)
            txns = txns.get('transactions', []) if isinstance(txns, dict) else txns
            bank = {'transfers': len(txns),
                    'booked': bs['booked_transfers'], 'booked_units': bs['booked_units'],
                    'currency': p['currency'], 'rate': p['rate'],
                    'balance': round(sum(float(a.get('balance') or 0) for a in accts), 2)}
        proofs_issued = len(oh.proofs()) if p['proofs'] else 0
        pool_now = oh.pool()

    gross = end_stats['gross_rent']
    months_run = len(rows)
    last = rows[-1] if rows else None
    return {
        'ok': True,
        'params': {**p, 'months': months, 'seed': seed, 'start': start},
        'terms': {k: live[k] for k in ('model', 'model_name', 'fee_pct', 'credit_pct', 'option_fee_pct',
                                       'home_price', 'monthly_rent', 'equity_pct_of_rent',
                                       'owner_pct_of_rent', 'to_property_pct')},
        'cast': [{'who': w, 'name': n, 'address': CAST[w], 'reference': refs.get(w)} for w, n, _ in cast]
                + [{'who': 'owner', 'name': 'the owner', 'address': owner}],
        'months': rows,
        'quarters': quarters,
        'events': events,
        'end': {
            'months_run': months_run,
            'owned': owned_month is not None,
            'owned_month': owned_month,
            'owned_pct': last['owned_pct'] if last else 0.0,
            'gross_paid': round(gross, 8),
            'fees': round(end_stats['protocol_fees'], 8),
            'equity': round(end_stats['renter_equity'], 8),
            'owner_income': round(end_stats['owner_income'], 8),
            'yield_modeled': round(yield_cum, 8),
            'to_property_pct': end_stats['to_property_pct'],
            'renters': equities,
            'quarters_closed': len(quarters),
            'fees_distributed': round(sum(q['pool'] for q in quarters), 8),
            'pool_accruing': pool_now['pool'],
            'proofs_issued': proofs_issued,
            'payments': end_stats['payments'],
        },
        'versus': {
            'note': 'The same checks, somewhere else. Headline rates, not a quote.',
            'plain_lease': {'paid': round(gross, 8), 'equity': 0.0, 'platform_take': 0.0},
            'platform_15': {'paid': round(gross, 8), 'equity': 0.0, 'platform_take': round(gross * 0.15, 8)},
            'openhouse': {'paid': round(gross, 8), 'equity': round(end_stats['renter_equity'], 8),
                          'platform_take': round(end_stats['protocol_fees'], 8)},
        },
        'bank': bank,
        'modeled': ['yield (apy) — the protocol never holds the owner\'s money, so this is arithmetic, not a ledger line',
                    'rate — pinned for the run so the story is deterministic'],
        'played': ['claim_owner', 'set_terms', 'bank_connect', 'bank_link', 'bank_receive',
                   'bank_reconcile → pay_rent', 'rent_stats', 'equity', 'pool', 'close_quarter']
                  + (['proof_issue'] if p['proofs'] else []),
        'store': 'throwaway — a temp directory with the live module\'s own code; the live store is untouched',
        'elapsed_ms': int((time.time() - t_wall) * 1000),
    }


def _bank_in_memory(oh):
    """Same trick for the bank rail: the hub's JSON files and the sandbox's
    books live in dicts for the length of the run. The hub instance is the
    throwaway Mod's own, and the adapter table is copied before the sandbox
    is swapped, so nothing leaks into the live module."""
    hub = oh._bank()
    oh._bank = lambda: hub            # the live Mod opens a fresh hub per call
    files = {}
    hub._read = lambda path, default: files.get(str(path), default)
    hub._write = lambda path, data, private=False: files.__setitem__(str(path), data)
    hub.adapters = dict(hub.adapters)
    Sandbox = hub.adapters['sandbox']
    books = {}

    class MemSandbox(Sandbox):
        def _books(self):
            return books.setdefault(self.conn['id'], {'accounts': {}, 'txns': [], 'payments': []})

        def _save(self, b):
            books[self.conn['id']] = b

    hub.adapters['sandbox'] = MemSandbox


def _who(address: str) -> str:
    for k, v in CAST.items():
        if v.lower() == (address or '').lower():
            return k
    return address


def _period(t: int) -> int:
    y, mo = time.gmtime(t)[:2]
    return y * 100 + mo
