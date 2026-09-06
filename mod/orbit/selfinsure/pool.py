#!/usr/bin/env python3
"""selfinsure engine — mutual pools, adjudicated by agents, owned by members.

The whole point of this file is one accounting rule: **a premium becomes pool
money and stays pool money.** There is no house account that skims it, no
underwriter that keeps the upside. What is left after claims is still owed to
the people who paid it in, and `distribute` is how it goes back. An operator fee
exists because some pools want to pay for their own admin, but it defaults to
zero and is capped at 10% — a pool cannot quietly become an insurer.

Everything is integer minor units (cents) internally. Floats are for display
only: a premium split across 40 members has to add back up to the premium, and
0.1 + 0.2 does not.

State lives off-tree in ~/.mod/selfinsure/ — never in the repo, because it holds
the hashes of member and agent keys.
"""

import hashlib
import json
import os
import re
import secrets
import time

STORE = os.path.expanduser(os.environ.get('SELFINSURE_DIR', '~/.mod/selfinsure'))
STATE_FILE = os.path.join(STORE, 'state.json')
LEDGER_FILE = os.path.join(STORE, 'ledger.jsonl')

# A mutual that keeps 10% of every premium is not a mutual. The cap is the
# module's one hard opinion; fee_bps=0 is the default and the intended shape.
FEE_CAP_BPS = int(os.environ.get('SELFINSURE_FEE_CAP_BPS', 1000))
DAY = 86400.0


class SelfInsureError(Exception):
    def __init__(self, message, status=400, **extra):
        super().__init__(message)
        self.message, self.status, self.extra = message, status, extra

    def dict(self):
        return {'error': self.message, **self.extra}


# ── money ────────────────────────────────────────────────────────

def minor(x, field='amount', allow_zero=False, dec=2):
    """A user-supplied amount → integer minor units (cents for a 2-decimal
    unit, wei for an 18-decimal one). Goes through Decimal so 0.1 ETH is
    exactly 10**17 and not one wei less. Rejects the things that quietly
    become zero: '', None, 'abc', negatives, NaN."""
    from decimal import Decimal, InvalidOperation
    if x is None or x == '':
        raise SelfInsureError(f'{field} is required')
    try:
        d = Decimal(str(x))
    except (InvalidOperation, ValueError):
        raise SelfInsureError(f'{field} must be a number, got {x!r}')
    if not d.is_finite():
        raise SelfInsureError(f'{field} must be a finite number')
    if d < 0:
        raise SelfInsureError(f'{field} cannot be negative')
    c = int((d * (Decimal(10) ** int(dec or 0))).to_integral_value())
    if c == 0 and not allow_zero:
        raise SelfInsureError(f'{field} must be greater than zero')
    return c


def major(c, dec=2):
    """Integer minor units → a display number. Exact for 2 decimals; for an
    18-decimal asset the float is a display value and the ledger keeps the int."""
    from decimal import Decimal
    dec = int(dec or 0)
    return float(round(Decimal(c or 0) / (Decimal(10) ** dec), dec))


def split_pro_rata(total, weights):
    """Split `total` cents across weights so the parts sum to exactly `total`.

    Largest-remainder. Plain rounding leaves stray cents in the pot, and over
    enough distributions those strays are the operator quietly keeping money.
    """
    total_w = sum(weights)
    if total <= 0 or total_w <= 0:
        return [0] * len(weights)
    exact = [total * w / total_w for w in weights]
    parts = [int(e) for e in exact]
    rem = total - sum(parts)
    order = sorted(range(len(weights)), key=lambda i: (-(exact[i] - parts[i]), -weights[i], i))
    for i in order[:rem]:
        parts[i] += 1
    return parts


# ── store ────────────────────────────────────────────────────────

def _blank():
    return {'version': 1, 'pools': {}, 'seq': 0}


def load():
    try:
        with open(STATE_FILE) as f:
            s = json.load(f)
    except FileNotFoundError:
        return _blank()
    except json.JSONDecodeError as e:
        raise SelfInsureError(f'{STATE_FILE} is not valid JSON ({e}) — refusing to '
                              'overwrite a ledger I cannot read', status=500)
    s.setdefault('pools', {})
    s.setdefault('seq', 0)
    return s


def save(state):
    os.makedirs(STORE, exist_ok=True)
    tmp = STATE_FILE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(state, f, indent=2, sort_keys=True)
    os.replace(tmp, STATE_FILE)
    try:
        os.chmod(STATE_FILE, 0o600)
    except OSError:
        pass


class _Txn:
    """Load, mutate, append to the ledger, save. The lock is a directory: two
    agents voting on the same claim at the same moment must not both see a
    pre-vote pool and both pay it out."""

    def __init__(self):
        self.lock = os.path.join(STORE, '.lock')
        self.entries = []

    def __enter__(self):
        os.makedirs(STORE, exist_ok=True)
        for i in range(200):
            try:
                os.mkdir(self.lock)
                break
            except FileExistsError:
                try:  # a lock older than 30s belonged to a process that died
                    if time.time() - os.path.getmtime(self.lock) > 30:
                        os.rmdir(self.lock)
                        continue
                except OSError:
                    pass
                time.sleep(0.05)
        else:
            raise SelfInsureError('the ledger is locked by another writer', status=503)
        self.state = load()
        return self

    def log(self, event, pool, **fields):
        # `event`, not `kind`: register_agent logs an agent's kind as a field
        self.entries.append({'at': time.time(), 'kind': event, 'pool': pool, **fields})

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                save(self.state)
                if self.entries:
                    with open(LEDGER_FILE, 'a') as f:
                        for e in self.entries:
                            f.write(json.dumps(e, default=str) + '\n')
        finally:
            try:
                os.rmdir(self.lock)
            except OSError:
                pass
        return False


# ── identity ─────────────────────────────────────────────────────

def _key(role):
    return f'si_{role}_{secrets.token_hex(16)}'


def _hash(key):
    return hashlib.sha256((key or '').encode()).hexdigest()


def _check(key, want_hash, who):
    if not key:
        raise SelfInsureError(f'this needs the {who} key issued when you registered',
                              status=401)
    if not secrets.compare_digest(_hash(key), want_hash or ''):
        raise SelfInsureError(f'that is not the {who} key for this pool', status=403)


def _slug(name):
    s = re.sub(r'[^a-z0-9]+', '-', (name or '').lower()).strip('-')
    return s[:32] or 'pool'


# ── pools ────────────────────────────────────────────────────────

def _pool(state, ref):
    """Find a pool by id, slug or exact name. Ambiguity is an error, not a guess."""
    if not ref:
        raise SelfInsureError('which pool? pass pool=<id>')
    for p in state['pools'].values():
        p.setdefault('decimals', 2)
    if ref in state['pools']:
        return state['pools'][ref]
    hits = [p for p in state['pools'].values()
            if p['slug'] == _slug(ref) or p['name'].lower() == str(ref).lower()]
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        raise SelfInsureError(f'{ref!r} matches {len(hits)} pools — use the id: '
                              + ', '.join(p['id'] for p in hits))
    raise SelfInsureError(f'no pool {ref!r}', status=404)


def create_pool(name, about='', premium=0, coverage=0, unit='USD', period_days=30,
                deductible=0, fee_bps=0, quorum=1, threshold=0.5, waiting_days=0,
                agent_policy='open', reserve_floor=0, annual_cap=None, owner=None,
                decimals=None, **_):
    """Open a pool. Anyone may; there is no gatekeeper and no application."""
    name = (name or '').strip()
    if not name:
        raise SelfInsureError('a pool needs a name')
    fee_bps = int(fee_bps or 0)
    if not 0 <= fee_bps <= FEE_CAP_BPS:
        raise SelfInsureError(f'fee_bps must be between 0 and {FEE_CAP_BPS} '
                              f'({FEE_CAP_BPS / 100:.0f}%) — this is a mutual, not an '
                              'underwriter. 0 means every cent of premium stays in '
                              'the pool.')
    quorum = int(quorum or 1)
    if quorum < 1:
        raise SelfInsureError('quorum must be at least 1 — a claim nobody adjudicates '
                              'is not a claim')
    threshold = float(threshold if threshold is not None else 0.5)
    if not 0 < threshold <= 1:
        raise SelfInsureError('threshold is the share of votes that must accept, '
                              'between 0 (exclusive) and 1')
    if agent_policy not in ('open', 'approved'):
        raise SelfInsureError("agent_policy is 'open' (anyone may adjudicate) or "
                              "'approved' (the pool owner admits adjudicators)")
    unit_ = (unit or 'USD').upper()[:8]
    dec = int(decimals) if decimals not in (None, '') else \
        {'ETH': 18, 'WEI': 18, 'SOL': 9, 'USDC': 6, 'USDT': 6, 'DAI': 18}.get(unit_, 2)
    if not 0 <= dec <= 18:
        raise SelfInsureError('decimals must be between 0 and 18')
    prem = minor(premium, 'premium', allow_zero=True, dec=dec)
    cov = minor(coverage, 'coverage', allow_zero=True, dec=dec)
    with _Txn() as t:
        pid = f'{_slug(name)}-{secrets.token_hex(2)}'
        key = _key('owner')
        pool = {
            'id': pid, 'name': name, 'slug': _slug(name), 'about': about or '',
            'unit': unit_, 'decimals': dec,
            'premium': prem, 'coverage': cov,
            'deductible': minor(deductible, 'deductible', allow_zero=True, dec=dec),
            'annual_cap': None if annual_cap in (None, '') else minor(annual_cap, 'annual_cap', dec=dec),
            'reserve_floor': minor(reserve_floor, 'reserve_floor', allow_zero=True, dec=dec),
            'period_days': float(period_days or 30),
            'fee_bps': fee_bps, 'quorum': quorum, 'threshold': threshold,
            'waiting_days': float(waiting_days or 0),
            'agent_policy': agent_policy,
            'state': 'open',
            'balance': 0, 'fees_accrued': 0, 'premiums_in': 0, 'paid_out': 0,
            'distributed': 0, 'fees_withdrawn': 0,
            'owner': (owner or 'anonymous')[:64], 'owner_key': _hash(key),
            'created': time.time(),
            'members': {}, 'agents': {}, 'claims': {}, 'claim_seq': 0,
        }
        t.state['pools'][pid] = pool
        t.log('pool_created', pid, name=name, fee_bps=fee_bps, premium=prem,
              coverage=cov, unit=pool['unit'])
        out = view(pool)
    out['owner_key'] = key
    out['keep'] = ('This is the only time the owner key is shown. It is stored '
                   'hashed; nobody, including this server, can recover it.')
    return out


def set_terms(pool, owner_key=None, **changes):
    """Adjust terms. Never retroactive: a claim is judged on the terms it was
    filed under, and the fee cannot be raised on premiums already paid."""
    editable = {'about', 'premium', 'coverage', 'deductible', 'annual_cap',
                'period_days', 'quorum', 'threshold', 'waiting_days',
                'agent_policy', 'reserve_floor', 'fee_bps', 'state'}
    with _Txn() as t:
        p = _pool(t.state, pool)
        _check(owner_key, p['owner_key'], 'owner')
        applied = {}
        for k, v in changes.items():
            if k not in editable or v is None or v == '':
                continue
            if k == 'fee_bps':
                v = int(v)
                if not 0 <= v <= FEE_CAP_BPS:
                    raise SelfInsureError(f'fee_bps must be 0..{FEE_CAP_BPS}')
            elif k in ('premium', 'coverage', 'deductible', 'reserve_floor'):
                v = minor(v, k, allow_zero=True, dec=p['decimals'])
            elif k == 'annual_cap':
                v = minor(v, k, dec=p['decimals'])
            elif k in ('period_days', 'waiting_days', 'threshold'):
                v = float(v)
            elif k == 'quorum':
                v = max(1, int(v))
            elif k == 'state':
                if v not in ('open', 'closed'):
                    raise SelfInsureError("state is 'open' (accepting members) or "
                                          "'closed' (existing members only)")
            elif k == 'agent_policy' and v not in ('open', 'approved'):
                raise SelfInsureError("agent_policy is 'open' or 'approved'")
            if p.get(k) != v:
                applied[k] = v
                p[k] = v
        t.log('terms_changed', p['id'], changes=applied)
        return {'pool': p['id'], 'changed': applied, 'terms': view(p)['terms'],
                'note': 'open claims keep the terms they were filed under'}


# ── membership ───────────────────────────────────────────────────

def join(pool, name, premium=None, note='', **_):
    """Join a pool and pay the first premium. Coverage starts after the waiting
    period; the receipt says exactly when."""
    name = (name or '').strip()
    if not name:
        raise SelfInsureError('a member needs a name or handle')
    with _Txn() as t:
        p = _pool(t.state, pool)
        if p['state'] != 'open':
            raise SelfInsureError(f"{p['id']} is {p['state']} — not taking new members",
                                  status=409)
        if any(m['name'].lower() == name.lower() for m in p['members'].values()):
            raise SelfInsureError(f'{name!r} is already a member of this pool — use '
                                  'si_premium with your member key to top up')
        amount = p['premium'] if premium in (None, '') else \
            minor(premium, 'premium', allow_zero=True, dec=p['decimals'])
        if p['premium'] and amount < p['premium']:
            raise SelfInsureError(f"this pool's premium is {major(p['premium'], p['decimals'])} "
                                  f"{p['unit']} — {major(amount, p['decimals'])} is short")
        mid = f'mem_{secrets.token_hex(4)}'
        key = _key('member')
        now = time.time()
        p['members'][mid] = {
            'id': mid, 'name': name[:64], 'key': _hash(key), 'joined': now,
            'contributed': 0, 'received': 0, 'refunded': 0, 'note': (note or '')[:280],
            'covered_from': now + p['waiting_days'] * DAY, 'paid_through': now,
        }
        credited = _credit(t, p, mid, amount, 'premium') if amount else {'to_pool': 0, 'fee': 0}
        out = {'pool': p['id'], 'member': mid, 'name': name,
               'member_key': key,
               'paid': major(amount, p['decimals']), 'to_pool': major(credited['to_pool'], p['decimals']),
               'fee': major(credited['fee'], p['decimals']), 'unit': p['unit'],
               'covered_from': p['members'][mid]['covered_from'],
               'covered_now': p['waiting_days'] <= 0,
               'coverage': major(p['coverage'], p['decimals']),
               'keep': 'The member key is shown once. It is what proves a claim is '
                       'yours; store it now.'}
        if p['waiting_days']:
            out['note'] = (f"waiting period is {p['waiting_days']:g} days — a claim "
                           'filed before then will be refused')
        return out


def _member(p, ref, key=None, need_key=True):
    m = p['members'].get(ref)
    if not m:
        hits = [x for x in p['members'].values() if x['name'].lower() == str(ref).lower()]
        if len(hits) > 1:
            raise SelfInsureError(f'{ref!r} matches several members — use the member id')
        m = hits[0] if hits else None
    if not m:
        raise SelfInsureError(f'{ref!r} is not a member of {p["id"]}', status=404)
    if need_key:
        _check(key, m['key'], 'member')
    return m


def _credit(t, p, member_id, amount, kind):
    """Money in. The fee (usually zero) is the only part that leaves the pool;
    everything else lands in the pot and immediately goes to work paying down
    anything the pool already owes."""
    fee = amount * p['fee_bps'] // 10000
    to_pool = amount - fee
    p['balance'] += to_pool
    p['fees_accrued'] += fee
    p['premiums_in'] += amount
    if member_id:
        m = p['members'][member_id]
        m['contributed'] += to_pool
        m['paid_through'] = max(m.get('paid_through', 0), time.time()) + \
            (p['period_days'] * DAY if p['premium'] and amount >= p['premium'] else 0)
    t.log(kind, p['id'], member=member_id, amount=amount, fee=fee, to_pool=to_pool,
          balance=p['balance'])
    settled = _pay_backlog(t, p)
    return {'to_pool': to_pool, 'fee': fee, 'backlog_settled': settled}


def premium(pool, member=None, member_key=None, amount=None, **_):
    """Top up. Also the way an underfunded pool digs itself out: new money pays
    the oldest approved-but-unpaid claim before it becomes surplus."""
    with _Txn() as t:
        p = _pool(t.state, pool)
        m = _member(p, member, member_key)
        amt = p['premium'] if amount in (None, '') else minor(amount, 'amount', dec=p['decimals'])
        if not amt:
            raise SelfInsureError('this pool has no set premium — pass amount=')
        c = _credit(t, p, m['id'], amt, 'premium')
        return {'pool': p['id'], 'member': m['id'], 'paid': major(amt, p['decimals']),
                'to_pool': major(c['to_pool'], p['decimals']), 'fee': major(c['fee'], p['decimals']),
                'balance': major(p['balance'], p['decimals']), 'unit': p['unit'],
                'paid_through': m['paid_through'],
                'backlog_settled': c['backlog_settled']}


def donate(pool, amount, name='donor', **_):
    """Money in from someone who is not buying coverage — a backstop, a grant,
    a founder seeding the pot. It buys no claim rights and no share of surplus."""
    with _Txn() as t:
        p = _pool(t.state, pool)
        amt = minor(amount, 'amount', dec=p['decimals'])
        p['balance'] += amt
        t.log('donation', p['id'], amount=amt, name=(name or 'donor')[:64],
              balance=p['balance'])
        settled = _pay_backlog(t, p)
        return {'pool': p['id'], 'donated': major(amt, p['decimals']), 'balance': major(p['balance'], p['decimals']),
                'unit': p['unit'], 'backlog_settled': settled,
                'note': 'a donation earns no coverage and no share of surplus'}


# ── agents ───────────────────────────────────────────────────────

def register_agent(pool, name, kind='ai', model='', about='', **_):
    """Register as an adjudicator. On an `open` pool this is instant; on an
    `approved` pool the registration is pending until the owner admits it."""
    name = (name or '').strip()
    if not name:
        raise SelfInsureError('an agent needs a name — claimants can see who judged them')
    if kind not in ('ai', 'human'):
        raise SelfInsureError("kind is 'ai' or 'human' — a claimant is entitled to "
                              'know which one read their evidence')
    with _Txn() as t:
        p = _pool(t.state, pool)
        if any(a['name'].lower() == name.lower() for a in p['agents'].values()):
            raise SelfInsureError(f'{name!r} is already an adjudicator here')
        aid = f'agt_{secrets.token_hex(4)}'
        key = _key('agent')
        p['agents'][aid] = {
            'id': aid, 'name': name[:64], 'kind': kind, 'model': (model or '')[:64],
            'about': (about or '')[:280], 'key': _hash(key),
            'active': p['agent_policy'] == 'open', 'registered': time.time(),
            'votes': 0, 'accepts': 0, 'rejects': 0, 'with_majority': 0,
        }
        t.log('agent_registered', p['id'], agent=aid, name=name, kind=kind,
              active=p['agents'][aid]['active'])
        return {'pool': p['id'], 'agent': aid, 'name': name, 'kind': kind,
                'agent_key': key,
                'active': p['agents'][aid]['active'],
                'note': ('you can vote now' if p['agents'][aid]['active'] else
                         'this pool admits adjudicators by hand — the owner must run '
                         'si_admit_agent before your votes count'),
                'keep': 'The agent key is shown once and stored hashed.'}


def admit_agent(pool, agent, owner_key=None, active=True, **_):
    """Owner admits (or suspends) an adjudicator."""
    with _Txn() as t:
        p = _pool(t.state, pool)
        _check(owner_key, p['owner_key'], 'owner')
        a = p['agents'].get(agent) or next(
            (x for x in p['agents'].values() if x['name'].lower() == str(agent).lower()), None)
        if not a:
            raise SelfInsureError(f'no agent {agent!r} in {p["id"]}', status=404)
        a['active'] = bool(active)
        t.log('agent_admitted' if active else 'agent_suspended', p['id'], agent=a['id'])
        return {'pool': p['id'], 'agent': a['id'], 'name': a['name'],
                'active': a['active']}


def agents(pool, **_):
    p = _pool(load(), pool)
    return {'pool': p['id'], 'policy': p['agent_policy'],
            'quorum': p['quorum'], 'threshold': p['threshold'],
            'agents': [_agent_view(a) for a in
                       sorted(p['agents'].values(), key=lambda a: -a['votes'])]}


def _agent_view(a):
    return {'id': a['id'], 'name': a['name'], 'kind': a['kind'],
            'model': a['model'] or None, 'about': a['about'] or None,
            'active': a['active'], 'votes': a['votes'],
            'accepts': a['accepts'], 'rejects': a['rejects'],
            'accept_rate': round(a['accepts'] / a['votes'], 3) if a['votes'] else None,
            # how often this agent landed on the side the pool settled on — a
            # lone perpetual rejecter and a rubber stamp both show up here
            'concordance': round(a['with_majority'] / a['votes'], 3) if a['votes'] else None,
            'registered': a['registered']}


# ── claims ───────────────────────────────────────────────────────

def file_claim(pool, member=None, member_key=None, amount=None, title='', detail='',
               evidence=None, **_):
    with _Txn() as t:
        p = _pool(t.state, pool)
        m = _member(p, member, member_key)
        amt = minor(amount, 'amount', dec=p['decimals'])
        now = time.time()
        if now < m['covered_from']:
            days = (m['covered_from'] - now) / DAY
            raise SelfInsureError(
                f'coverage starts in {days:.1f} days — this pool has a '
                f'{p["waiting_days"]:g}-day waiting period and it applies to you',
                status=409, covered_from=m['covered_from'])
        if not (title or '').strip():
            raise SelfInsureError('a claim needs a title — the adjudicators read it first')
        if p['coverage'] and amt > p['coverage']:
            # Not an error: file it, and say plainly that the top will be cut off.
            pass
        p['claim_seq'] += 1
        cid = f"{p['id']}#{p['claim_seq']}"
        ev = evidence if isinstance(evidence, list) else \
            ([e.strip() for e in str(evidence).split(',') if e.strip()] if evidence else [])
        p['claims'][cid] = {
            'id': cid, 'pool': p['id'], 'member': m['id'], 'member_name': m['name'],
            'amount': amt, 'title': title.strip()[:200], 'detail': (detail or '')[:4000],
            'evidence': ev[:20], 'state': 'open', 'filed': now,
            'votes': [], 'decided': None, 'paid': 0, 'shortfall': 0,
            # frozen at filing: changing terms later must not change this claim
            'terms': {'coverage': p['coverage'], 'deductible': p['deductible'],
                      'quorum': p['quorum'], 'threshold': p['threshold'],
                      'annual_cap': p['annual_cap']},
        }
        t.log('claim_filed', p['id'], claim=cid, member=m['id'], amount=amt,
              title=title.strip()[:200])
        c = p['claims'][cid]
        return {'claim': cid, 'pool': p['id'], 'state': 'open',
                'amount': major(amt, p['decimals']), 'unit': p['unit'],
                'payable_if_accepted': major(_payable(p, c), p['decimals']),
                'needs': f"{p['quorum']} agent vote(s), "
                         f"{p['threshold'] * 100:.0f}% must accept",
                'funded_now': _payable(p, c) <= p['balance'],
                'pool_balance': major(p['balance'], p['decimals'])}


def _year_paid(p, member_id, before=None):
    cutoff = (before or time.time()) - 365 * DAY
    return sum(c['paid'] for c in p['claims'].values()
               if c['member'] == member_id and (c['decided'] or 0) >= cutoff)


def _payable(p, claim):
    """The most this claim could ever pay, under the terms it was filed under.
    Deductible first, then the per-claim cap, then what is left of the member's
    annual cap. Nothing here looks at the balance — that is solvency, not terms."""
    tm = claim.get('terms') or {}
    amt = max(0, claim['amount'] - (tm.get('deductible') or 0))
    cov = tm.get('coverage') or 0
    if cov:
        amt = min(amt, cov)
    cap = tm.get('annual_cap')
    if cap:
        amt = min(amt, max(0, cap - _year_paid(p, claim['member'])))
    return amt


def vote(pool=None, claim=None, agent=None, agent_key=None, accept=None, reason='', **_):
    """An adjudicator accepts or rejects. This is the whole mechanism: a claim
    is only ever decided by the pool's own agents, and a decision that costs
    money settles in the same breath as the vote that carried it."""
    if accept is None:
        raise SelfInsureError('accept must be true (pay it) or false (refuse it)')
    accept = accept if isinstance(accept, bool) else \
        str(accept).lower() in ('1', 'true', 'yes', 'accept', 'y')
    reason = (reason or '').strip()
    if not reason:
        raise SelfInsureError('a vote needs a reason — the claimant reads it, and an '
                              'unexplained rejection is how mutuals die')
    with _Txn() as t:
        p, c = _find_claim(t.state, pool, claim)
        a = p['agents'].get(agent) or next(
            (x for x in p['agents'].values() if x['name'].lower() == str(agent).lower()), None)
        if not a:
            raise SelfInsureError(f'no agent {agent!r} in {p["id"]} — register with '
                                  'si_register_agent first', status=404)
        _check(agent_key, a['key'], 'agent')
        if not a['active']:
            raise SelfInsureError('this adjudicator has not been admitted to the pool '
                                  'yet (agent_policy=approved)', status=403)
        if c['state'] != 'open':
            raise SelfInsureError(f"claim {c['id']} is already {c['state']}", status=409)
        if any(v['agent'] == a['id'] for v in c['votes']):
            raise SelfInsureError('you already voted on this claim; votes are final')
        # An adjudicator judging their own claim is the failure mode that makes
        # a mutual worthless. Blocked, by member name as well as by key.
        claimant = p['members'].get(c['member'], {})
        if a['name'].lower() == (claimant.get('name') or '').lower():
            raise SelfInsureError('you cannot adjudicate your own claim', status=403)
        c['votes'].append({'agent': a['id'], 'agent_name': a['name'],
                           'kind': a['kind'], 'accept': accept,
                           'reason': reason[:1000], 'at': time.time()})
        a['votes'] += 1
        a['accepts' if accept else 'rejects'] += 1
        t.log('vote', p['id'], claim=c['id'], agent=a['id'], accept=accept,
              reason=reason[:200])

        tm = c['terms']
        quorum, threshold = tm.get('quorum', 1), tm.get('threshold', 0.5)
        yes = sum(1 for v in c['votes'] if v['accept'])
        n = len(c['votes'])
        out = {'claim': c['id'], 'pool': p['id'], 'your_vote': 'accept' if accept else 'reject',
               'votes': n, 'accepts': yes, 'rejects': n - yes,
               'quorum': quorum, 'threshold': threshold}
        if n < quorum:
            out.update(state='open', needs=quorum - n,
                       note=f'{quorum - n} more vote(s) before this settles')
            return out
        decided_accept = (yes / n) >= threshold
        for v in c['votes']:
            if v['accept'] == decided_accept:
                p['agents'][v['agent']]['with_majority'] += 1
        c['decided'] = time.time()
        if not decided_accept:
            c['state'] = 'rejected'
            t.log('claim_rejected', p['id'], claim=c['id'], accepts=yes, votes=n)
            out.update(state='rejected', paid=0,
                       note='the pool keeps the money; the claimant keeps the reasons')
            return out
        c['state'] = 'accepted'
        t.log('claim_accepted', p['id'], claim=c['id'], accepts=yes, votes=n)
        paid = _pay(t, p, c)
        out.update(state=c['state'], paid=major(paid, p['decimals']), owed=major(c['shortfall'], p['decimals']),
                   pool_balance=major(p['balance'], p['decimals']), unit=p['unit'])
        if c['shortfall']:
            out['note'] = (f"the pool could only fund {major(paid, p['decimals'])} of "
                           f"{major(paid + c['shortfall'], p['decimals'])} {p['unit']} — the rest is "
                           'queued and paid from the next premiums in')
        return out


def _pay(t, p, c):
    """Pay what the pool actually has. An accepted claim the pool cannot fund is
    recorded as a debt, not silently downgraded — `unfunded` is the honest state
    and `si_pool` reports it as insolvency."""
    due = _payable(p, c) - c['paid']
    pay = max(0, min(due, p['balance']))
    if pay:
        p['balance'] -= pay
        p['paid_out'] += pay
        c['paid'] += pay
        p['members'][c['member']]['received'] += pay
        t.log('payout', p['id'], claim=c['id'], member=c['member'], amount=pay,
              balance=p['balance'])
    c['shortfall'] = max(0, _payable(p, c) - c['paid'])
    c['state'] = 'unfunded' if c['shortfall'] else 'paid'
    if c['shortfall']:
        t.log('claim_unfunded', p['id'], claim=c['id'], shortfall=c['shortfall'])
    return pay


def _pay_backlog(t, p):
    """Money arrived. Oldest unfunded claim first — a queue, so a pool that dips
    under water does not reward whoever shouts loudest when it recovers."""
    settled = []
    queue = sorted([c for c in p['claims'].values() if c['state'] == 'unfunded'],
                   key=lambda c: c['decided'] or c['filed'])
    for c in queue:
        if p['balance'] <= 0:
            break
        before = c['paid']
        _pay(t, p, c)
        if c['paid'] > before:
            settled.append({'claim': c['id'], 'paid': major(c['paid'] - before, p['decimals']),
                            'state': c['state'], 'still_owed': major(c['shortfall'], p['decimals'])})
    return settled


def withdraw_claim(pool=None, claim=None, member_key=None, reason='', **_):
    with _Txn() as t:
        p, c = _find_claim(t.state, pool, claim)
        _check(member_key, p['members'][c['member']]['key'], 'member')
        if c['state'] != 'open':
            raise SelfInsureError(f"claim {c['id']} is {c['state']} and cannot be "
                                  'withdrawn', status=409)
        c['state'] = 'withdrawn'
        c['decided'] = time.time()
        c['withdrawn_reason'] = (reason or '')[:280]
        t.log('claim_withdrawn', p['id'], claim=c['id'])
        return {'claim': c['id'], 'state': 'withdrawn'}


def _find_claim(state, pool, claim):
    if not claim:
        raise SelfInsureError('which claim? pass claim=<id>')
    if pool:
        p = _pool(state, pool)
    else:
        pid = str(claim).split('#')[0]
        p = _pool(state, pid)
    c = p['claims'].get(claim)
    if not c and str(claim).isdigit():
        c = p['claims'].get(f"{p['id']}#{claim}")
    if not c:
        raise SelfInsureError(f'no claim {claim!r} in {p["id"]}', status=404)
    return p, c


def claims(pool=None, state=None, member=None, limit=50, **_):
    """The work queue. `state=open` is what an adjudicating agent asks for."""
    st = load()
    pools = [_pool(st, pool)] if pool else list(st['pools'].values())
    rows = []
    for p in pools:
        for c in p['claims'].values():
            if state and c['state'] != state:
                continue
            if member and c['member'] != member and \
                    c['member_name'].lower() != str(member).lower():
                continue
            rows.append(_claim_view(p, c))
    rows.sort(key=lambda r: (r['state'] != 'open', -r['filed']))
    return {'count': len(rows), 'claims': rows[:int(limit or 50)]}


def claim(pool=None, claim=None, **_):
    p, c = _find_claim(load(), pool, claim)
    v = _claim_view(p, c, full=True)
    v['pool_balance'] = major(p['balance'], p['decimals'])
    return v


def _claim_view(p, c, full=False):
    payable = _payable(p, c)
    yes = sum(1 for v in c['votes'] if v['accept'])
    out = {
        'id': c['id'], 'pool': p['id'], 'pool_name': p['name'], 'unit': p['unit'],
        'member': c['member'], 'member_name': c['member_name'],
        'title': c['title'], 'amount': major(c['amount'], p['decimals']),
        'payable_if_accepted': major(payable, p['decimals']), 'state': c['state'],
        'filed': c['filed'], 'decided': c['decided'],
        'paid': major(c['paid'], p['decimals']), 'owed': major(c['shortfall'], p['decimals']),
        'votes': len(c['votes']), 'accepts': yes, 'rejects': len(c['votes']) - yes,
        'quorum': c['terms'].get('quorum', p['quorum']),
        'threshold': c['terms'].get('threshold', p['threshold']),
        'funded_now': payable - c['paid'] <= p['balance'],
    }
    if full:
        out.update(detail=c['detail'], evidence=c['evidence'],
                   ballots=[{'agent': v['agent_name'], 'kind': v['kind'],
                             'vote': 'accept' if v['accept'] else 'reject',
                             'reason': v['reason'], 'at': v['at']} for v in c['votes']],
                   terms={k: (major(v, p['decimals']) if k in ('coverage', 'deductible', 'annual_cap')
                              and v is not None else v)
                          for k, v in c['terms'].items()},
                   already_voted=[v['agent'] for v in c['votes']])
    return out


# ── surplus ──────────────────────────────────────────────────────

def exposure(p):
    """What the pool could still be asked to pay: every open claim at its full
    payable amount, plus everything already accepted and not yet funded."""
    open_ = sum(_payable(p, c) for c in p['claims'].values() if c['state'] == 'open')
    owed = sum(c['shortfall'] for c in p['claims'].values() if c['state'] == 'unfunded')
    return open_, owed


def distributable(p):
    open_, owed = exposure(p)
    return max(0, p['balance'] - owed - open_ - p['reserve_floor'])


def distribute(pool, owner_key=None, amount=None, confirm=False, **_):
    """Give the surplus back. This is where 'no profit' stops being a claim in a
    README: the money that did not turn into claims returns to the people who
    paid it, in proportion to what each paid in, and the operator gets none of
    it — their cut, if any, was taken at the premium and is capped."""
    with _Txn() as t:
        p = _pool(t.state, pool)
        _check(owner_key, p['owner_key'], 'owner')
        open_, owed = exposure(p)
        free = distributable(p)
        want = free if amount in (None, '') else minor(amount, 'amount', dec=p['decimals'])
        if owed:
            raise SelfInsureError(
                f'{major(owed, p['decimals'])} {p["unit"]} of accepted claims are still unpaid — '
                'the pool owes its own members before it returns anything',
                status=409, owed=major(owed, p['decimals']))
        if want > free:
            raise SelfInsureError(
                f'only {major(free, p['decimals'])} {p["unit"]} is free to distribute: balance '
                f'{major(p["balance"], p['decimals'])} less {major(open_, p['decimals'])} reserved against open '
                f'claims and {major(p["reserve_floor"], p['decimals'])} reserve floor',
                status=409, distributable=major(free, p['decimals']))
        if not want:
            raise SelfInsureError('nothing to distribute', status=409,
                                  balance=major(p['balance'], p['decimals']))
        # A member's stake is what they put in that has not already come back to
        # them, as a claim or as an earlier rebate.
        mem = [m for m in p['members'].values()
               if m['contributed'] - m['received'] - m['refunded'] > 0]
        if not mem:
            raise SelfInsureError('every member has already had back at least what '
                                  'they paid in — nothing to apportion', status=409)
        weights = [m['contributed'] - m['received'] - m['refunded'] for m in mem]
        parts = split_pro_rata(want, weights)
        preview = [{'member': m['id'], 'name': m['name'],
                    'net_contributed': major(w, p['decimals']), 'rebate': major(part, p['decimals'])}
                   for m, w, part in zip(mem, weights, parts) if part]
        if not confirm:
            return {'pool': p['id'], 'would_distribute': major(want, p['decimals']),
                    'unit': p['unit'], 'to': preview, 'confirmed': False,
                    'note': 'nothing moved — call again with confirm=true'}
        for m, part in zip(mem, parts):
            m['refunded'] += part
        p['balance'] -= want
        p['distributed'] += want
        t.log('distribution', p['id'], amount=want, members=len(preview),
              balance=p['balance'])
        return {'pool': p['id'], 'distributed': major(want, p['decimals']), 'unit': p['unit'],
                'to': preview, 'confirmed': True, 'balance': major(p['balance'], p['decimals'])}


def withdraw_fees(pool, owner_key=None, amount=None, **_):
    """The operator's own cut, if the pool has one. Separate from the pot on
    purpose: a fee that has to be withdrawn explicitly is a fee members can see."""
    with _Txn() as t:
        p = _pool(t.state, pool)
        _check(owner_key, p['owner_key'], 'owner')
        avail = p['fees_accrued'] - p['fees_withdrawn']
        want = avail if amount in (None, '') else minor(amount, 'amount', dec=p['decimals'])
        if want > avail:
            raise SelfInsureError(f'only {major(avail, p['decimals'])} {p["unit"]} of fees accrued',
                                  status=409)
        if not want:
            raise SelfInsureError(
                'no fees accrued — fee_bps is 0, which is the point of this pool',
                status=409)
        p['fees_withdrawn'] += want
        t.log('fee_withdrawn', p['id'], amount=want)
        return {'pool': p['id'], 'withdrawn': major(want, p['decimals']), 'unit': p['unit'],
                'fees_remaining': major(avail - want, p['decimals'])}


# ── views ────────────────────────────────────────────────────────

def view(p, full=False):
    open_, owed = exposure(p)
    net_in = p['premiums_in'] - p['fees_accrued']
    by_state = {}
    for c in p['claims'].values():
        by_state[c['state']] = by_state.get(c['state'], 0) + 1
    out = {
        'id': p['id'], 'name': p['name'], 'about': p['about'], 'unit': p['unit'],
        'state': p['state'], 'created': p['created'], 'owner': p['owner'],
        'members': len(p['members']),
        'agents': sum(1 for a in p['agents'].values() if a['active']),
        'claims': by_state,
        'terms': {
            'premium': major(p['premium'], p['decimals']), 'period_days': p['period_days'],
            'coverage': major(p['coverage'], p['decimals']), 'deductible': major(p['deductible'], p['decimals']),
            'annual_cap': major(p['annual_cap'], p['decimals']) if p['annual_cap'] else None,
            'waiting_days': p['waiting_days'],
            'fee_bps': p['fee_bps'], 'fee_pct': round(p['fee_bps'] / 100, 2),
            'quorum': p['quorum'], 'threshold': p['threshold'],
            'agent_policy': p['agent_policy'],
            'reserve_floor': major(p['reserve_floor'], p['decimals']),
        },
        'decimals': p['decimals'],
        'money': {
            'balance': major(p['balance'], p['decimals']),
            'premiums_in': major(p['premiums_in'], p['decimals']),
            'operator_fees': major(p['fees_accrued'], p['decimals']),
            'paid_in_claims': major(p['paid_out'], p['decimals']),
            'returned_to_members': major(p['distributed'], p['decimals']),
            'open_exposure': major(open_, p['decimals']),
            'unfunded_claims': major(owed, p['decimals']),
            'distributable': major(distributable(p), p['decimals']),
            # of every unit of premium, how much came back to members as claims
            'loss_ratio': round(p['paid_out'] / net_in, 3) if net_in else None,
            # and how much never left the mutual at all
            'member_share': round((net_in - 0) / p['premiums_in'], 4)
            if p['premiums_in'] else 1.0,
        },
        'solvency': _solvency(p, open_, owed),
    }
    if full:
        out['members_list'] = [_member_view(p, m) for m in p['members'].values()]
        out['agents_list'] = [_agent_view(a) for a in p['agents'].values()]
        out['claims_list'] = [_claim_view(p, c) for c in
                              sorted(p['claims'].values(), key=lambda c: -c['filed'])]
    return out


def _solvency(p, open_, owed):
    need = open_ + owed
    if owed:
        verdict = (f'insolvent — {major(owed, p['decimals'])} {p["unit"]} of claims were accepted and '
                   'cannot be paid until more premiums arrive')
    elif not need:
        verdict = 'no claims outstanding'
    elif p['balance'] >= need:
        verdict = 'every open claim could be paid in full today'
    else:
        verdict = (f'thin — if every open claim were accepted the pool would be short '
                   f'{major(need - p["balance"], p['decimals'])} {p["unit"]}')
    return {'balance': major(p['balance'], p['decimals']), 'outstanding': major(need, p['decimals']),
            'ratio': round(p['balance'] / need, 3) if need else None,
            'unfunded': major(owed, p['decimals']), 'verdict': verdict}


def _member_view(p, m):
    mine = [c for c in p['claims'].values() if c['member'] == m['id']]
    now = time.time()
    return {'id': m['id'], 'name': m['name'], 'joined': m['joined'],
            'contributed': major(m['contributed'], p['decimals']), 'claimed_back': major(m['received'], p['decimals']),
            'rebated': major(m['refunded'], p['decimals']),
            'net': major(m['contributed'] - m['received'] - m['refunded'], p['decimals']),
            'covered': now >= m['covered_from'],
            'covered_from': m['covered_from'],
            'paid_through': m.get('paid_through'),
            'current': now <= m.get('paid_through', 0) if p['premium'] else True,
            'claims': len(mine),
            'claims_paid': sum(1 for c in mine if c['state'] == 'paid'),
            'note': m.get('note') or None}


def pool_info(pool, full=True, **_):
    return view(_pool(load(), pool), full=full)


def member(pool, member=None, **_):
    p = _pool(load(), pool)
    m = _member(p, member, need_key=False)
    v = _member_view(p, m)
    v['pool'] = p['id']
    v['unit'] = p['unit']
    v['claims_list'] = [_claim_view(p, c) for c in p['claims'].values()
                        if c['member'] == m['id']]
    # if the pool were wound up today, this is their share of what is left
    free = distributable(p)
    mem = [x for x in p['members'].values()
           if x['contributed'] - x['received'] - x['refunded'] > 0]
    weights = [x['contributed'] - x['received'] - x['refunded'] for x in mem]
    parts = split_pro_rata(free, weights)
    v['surplus_share'] = major(next((pt for x, pt in zip(mem, parts)
                                     if x['id'] == m['id']), 0), p['decimals'])
    return v


def pools(q=None, state=None, limit=100, **_):
    st = load()
    rows = []
    for p in st['pools'].values():
        if state and p['state'] != state:
            continue
        if q and q.lower() not in (p['name'] + ' ' + p['about'] + ' ' + p['id']).lower():
            continue
        rows.append(view(p))
    rows.sort(key=lambda r: (-r['members'], -r['created']))
    return {'count': len(rows), 'pools': rows[:int(limit or 100)],
            'note': 'anyone can open one — si_create_pool takes a name and nothing else'}


def quote(pool, amount=None, **_):
    """What coverage would cost, what it would actually pay, and — the question
    a glossy brochure never answers — whether the pool could pay it today."""
    p = _pool(load(), pool)
    amt = minor(amount, 'amount', dec=p['decimals']) if amount not in (None, '') else p['coverage']
    net = max(0, amt - p['deductible'])
    payable = min(net, p['coverage']) if p['coverage'] else net
    open_, owed = exposure(p)
    per_period = p['premium']
    annual = per_period * (365.0 / p['period_days']) if p['period_days'] else 0
    return {
        'pool': p['id'], 'name': p['name'], 'unit': p['unit'],
        'premium': major(per_period, p['decimals']), 'period_days': p['period_days'],
        'annualised_premium': major(int(annual), p['decimals']),
        'claim_of': major(amt, p['decimals']), 'deductible': major(p['deductible'], p['decimals']),
        'would_pay': major(payable, p['decimals']),
        'covered_share': round(payable / amt, 3) if amt else None,
        'waiting_days': p['waiting_days'],
        'decided_by': f"{p['quorum']} of {sum(1 for a in p['agents'].values() if a['active'])} "
                      f"active adjudicator(s), {p['threshold'] * 100:.0f}% must accept",
        'funded_today': payable <= p['balance'],
        'pool_balance': major(p['balance'], p['decimals']),
        'already_committed': major(open_ + owed, p['decimals']),
        'operator_fee': f"{p['fee_bps'] / 100:g}% of premium"
                        + (' — the whole premium stays in the pool'
                           if not p['fee_bps'] else ''),
        'honest_note': ('this is a mutual: if enough members claim at once, claims are '
                        'accepted but queued until premiums cover them. Coverage is a '
                        'claim on the pool, not a guarantee from a balance sheet.'),
    }


def ledger(pool=None, kind=None, limit=100, **_):
    """Every movement, append-only, oldest first. The pool's balance is not a
    number you have to trust — it is the sum of these."""
    rows = []
    decs = {pid: p.get('decimals', 2) for pid, p in load()['pools'].items()}
    try:
        with open(LEDGER_FILE) as f:
            for line in f:
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                if pool and e.get('pool') != pool:
                    if pool not in (e.get('pool') or ''):
                        continue
                if kind and e.get('kind') != kind:
                    continue
                dec = decs.get(e.get('pool'), 2)
                for k in ('amount', 'fee', 'to_pool', 'balance', 'shortfall'):
                    if isinstance(e.get(k), int):
                        e[k] = major(e[k], dec)
                rows.append(e)
    except FileNotFoundError:
        pass
    n = int(limit or 100)
    return {'count': len(rows), 'entries': rows[-n:] if n else rows,
            'file': LEDGER_FILE}


def stats(**_):
    st = load()
    ps = list(st['pools'].values())
    tot = lambda k: sum(p[k] for p in ps)  # noqa: E731
    claims_ = [c for p in ps for c in p['claims'].values()]
    decided = [c for c in claims_ if c['state'] in ('paid', 'unfunded', 'rejected')]
    accepted = [c for c in decided if c['state'] != 'rejected']
    return {
        'pools': len(ps), 'open_pools': sum(1 for p in ps if p['state'] == 'open'),
        'members': sum(len(p['members']) for p in ps),
        'agents': sum(len(p['agents']) for p in ps),
        'claims': len(claims_), 'open_claims': sum(1 for c in claims_ if c['state'] == 'open'),
        'accept_rate': round(len(accepted) / len(decided), 3) if decided else None,
        'premiums_in': major(tot('premiums_in')),
        'paid_in_claims': major(tot('paid_out')),
        'returned_to_members': major(tot('distributed')),
        'operator_fees': major(tot('fees_accrued')),
        'held_in_pools': major(tot('balance')),
        'operator_share': round(tot('fees_accrued') / tot('premiums_in'), 4)
        if tot('premiums_in') else 0.0,
        'note': 'units are mixed across pools when pools declare different units',
        'store': STORE,
    }
