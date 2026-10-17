"""x402trust — who is calling, and how far this node trusts them.

Every MCP (and API) caller resolves to one id, strongest proof first:

    local     the box itself — stdio MCP, the CLI, loopback with no
              forwarding header. Owner standing.
    0x…       a wallet, proven by a mod-protocol token (base64url of
              {data,time,key,signature}, EIP-191 or raw-keccak over
              {"data","time"}) in `token`, `x-mod-token` or
              `Authorization: Bearer`. Verified here with eth_account —
              the protocol package is never imported.
    key:…     an API key this node issued (x402_register), sent as
              `Authorization: Bearer x402_…`. Only its hash is stored.
    anon:…    nobody: a stable hash of ip + user-agent.

Trust is 0..100 and is *derived*, never stored: each read recomputes it from
the ledger, so a formula change re-scores everyone and nothing drifts. What
moves it, and why each is hard to fake:

    base      by proof: wallet 30 · key 20 · anon 5 (anon is capped at 19)
    tenure    +5·log2(1+days since first seen), ≤ 15
    activity  +2.5 per distinct day with a successful action (90d), ≤ 15
    settled   +8·log2(1+paid calls the caller paid for itself), ≤ 25 —
              real money moved, the costliest signal there is
    vouches   other callers' 1..10 ratings, weighted by the voucher's own
              trust (≥ 50 to count, vouches excluded to stop loops), ±15
    penalties −2 per throttle, −5 per refused request (30d), ≤ −20 / −30
    owner     adjust (±), pin (fixed score) or ban (0)

The score buys a tier, the tier buys capabilities, rate and house budget
(policy, editable in ~/.mod/x402/policy.json — private, off the repo).
"""

import base64
import hashlib
import json
import math
import os
import secrets
import sqlite3
import threading
import time
from collections import deque

STATE = os.path.expanduser(os.environ.get('X402_HOME') or '~/.mod/x402')
DB = os.environ.get('X402_TRUST_DB') or os.path.join(STATE, 'agents.db')
POLICY = os.path.join(STATE, 'policy.json')
DAY = 86400
TOKEN_MAX_AGE = int(os.environ.get('X402_TOKEN_MAX_AGE') or 7 * DAY)

BASE = {'wallet': 30, 'key': 20, 'anon': 5}
ANON_CAP = 19
TIERS = ((70, 'core'), (40, 'trusted'), (20, 'basic'), (0, 'restricted'))

# What each tier may do. Reads are open to everyone; spending, fetching
# arbitrary URLs and vouching are earned.
CAN = {
    'restricted': {'read', 'register'},          # register: the way out of anon
    'basic': {'read', 'quote', 'call', 'register'},
    'trusted': {'read', 'quote', 'call', 'register', 'call_any', 'probe', 'vouch', 'house'},
    'core': {'read', 'quote', 'call', 'register', 'call_any', 'probe', 'vouch', 'house'},
    'owner': {'read', 'quote', 'call', 'register', 'call_any', 'probe', 'vouch', 'house',
              'admin', 'local_urls'},
}

DEFAULT_POLICY = {
    'house': False,                  # pay from the node's wallet at all
    'max_call_usd': 0.05,            # one house-paid call, at most
    'global_daily_usd': 2.0,         # every caller together, per UTC day
    'house_daily_usd': {'trusted': 0.10, 'core': 1.00, 'owner': 1e9},
    'rpm': {'restricted': 20, 'basic': 60, 'trusted': 120, 'core': 300, 'owner': 10_000},
    'owners': [],                    # wallets with owner standing besides `local`
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY, kind TEXT, label TEXT, first REAL, last REAL,
    pin INTEGER, adjust INTEGER DEFAULT 0, banned INTEGER DEFAULT 0, note TEXT
);
CREATE TABLE IF NOT EXISTS keys (
    hash TEXT PRIMARY KEY, user TEXT, label TEXT, created REAL,
    revoked INTEGER DEFAULT 0, last_used REAL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user TEXT, at REAL, tool TEXT,
    outcome TEXT, url TEXT, usd REAL DEFAULT 0, detail TEXT
);
CREATE INDEX IF NOT EXISTS events_user ON events(user, at);
CREATE INDEX IF NOT EXISTS events_url ON events(url);
CREATE TABLE IF NOT EXISTS vouches (
    voucher TEXT, subject TEXT, score INTEGER, at REAL, first REAL,
    PRIMARY KEY (voucher, subject)
);
"""

# Outcomes. `ok`/`settled`/`house` count as activity; `settled` is a call the
# caller paid for itself; `house` one the node paid for (usd = spend).
GOOD = ('ok', 'settled', 'house')

_lock = threading.RLock()
_local = threading.local()
_gen = 0


def db():
    c = getattr(_local, 'conn', None)
    if c is None or getattr(_local, 'gen', None) != _gen:
        os.makedirs(os.path.dirname(DB), exist_ok=True)
        c = sqlite3.connect(DB, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA journal_mode=WAL')
        c.executescript(SCHEMA)
        _local.conn, _local.gen = c, _gen
    return c


def reset(path=None, home=None):
    """Point the ledger somewhere else (tests)."""
    global DB, STATE, POLICY, _gen
    with _lock:
        if home:
            STATE = home
            POLICY = os.path.join(home, 'policy.json')
        DB = path or os.path.join(STATE, 'agents.db')
        _gen += 1
        _windows.clear()


# ── policy ────────────────────────────────────────────────────────

def policy():
    p = json.loads(json.dumps(DEFAULT_POLICY))
    try:
        with open(POLICY) as f:
            mine = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        mine = {}
    for k, v in mine.items():
        if isinstance(v, dict) and isinstance(p.get(k), dict):
            p[k].update(v)
        else:
            p[k] = v
    p['owners'] = [str(a).lower() for a in p.get('owners') or []]
    return p


def set_policy(**changes):
    """Owner: change policy keys (merged into policy.json, 0600)."""
    try:
        with open(POLICY) as f:
            mine = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        mine = {}
    for k, v in changes.items():
        if k not in DEFAULT_POLICY:
            raise ValueError(f'unknown policy key {k!r} (one of {", ".join(DEFAULT_POLICY)})')
        if isinstance(v, dict) and isinstance(mine.get(k), dict):
            mine[k].update(v)
        else:
            mine[k] = v
    os.makedirs(STATE, exist_ok=True)
    fd = os.open(POLICY, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(mine, f, indent=2)
    return policy()


# ── identity ──────────────────────────────────────────────────────

def _b64url(s):
    return base64.urlsafe_b64decode(s + '=' * (-len(s) % 4))


def verify_token(token, max_age=TOKEN_MAX_AGE):
    """Address a mod-protocol token proves (lowercase), or '' for no proof.
    Accepts the browser/Rust form (EIP-191 personal_sign) and the Python
    key's raw keccak signature; `data` may be a string or an object."""
    try:
        claim = json.loads(_b64url(str(token).strip()))
        data, t, key, sig = claim['data'], claim['time'], claim['key'], claim['signature']
        if abs(time.time() - float(t)) > max_age:
            return ''
        msg = json.dumps({'data': data, 'time': t}, separators=(',', ':'))
        from eth_account import Account
        from eth_account.messages import encode_defunct
        from eth_utils import keccak
        raw = bytes.fromhex(str(sig).removeprefix('0x'))
        if len(raw) != 65:
            return ''
        v = raw[64]
        rsv = raw[:64] + bytes([v + 27 if v < 27 else v])
        want = str(key).lower()
        for recover in (lambda: Account.recover_message(encode_defunct(text=msg), signature=rsv),
                        lambda: Account._recover_hash(keccak(text=msg), signature=rsv)):
            try:
                if recover().lower() == want:
                    return want
            except Exception:                             # noqa: BLE001 — try the other form
                continue
    except Exception:                                     # noqa: BLE001 — no proof is not an error
        return ''
    return ''


def _key_hash(k):
    return hashlib.sha256(str(k).encode()).hexdigest()


def anon_id(ip=None, ua=None):
    return 'anon:' + hashlib.sha256(f'x402|{ip}|{ua}'.encode()).hexdigest()[:10]


def identify(headers=None, ip=None, local=False):
    """Request headers → caller id + kind. A bad credential is not an error:
    it simply proves nothing and the caller falls through to anon."""
    h = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    auth = h.get('authorization', '')
    bearer = auth[7:].strip() if auth.lower().startswith('bearer ') else ''
    if bearer.startswith('x402_'):
        with _read():
            r = db().execute('SELECT user FROM keys WHERE hash=? AND revoked=0',
                             (_key_hash(bearer),)).fetchone()
        if r:
            with _lock:
                db().execute('UPDATE keys SET last_used=? WHERE hash=?',
                             (time.time(), _key_hash(bearer)))
                db().commit()
            return _seen(r['user'], 'key')
    for tok in (h.get('token'), h.get('x-mod-token'), bearer if not bearer.startswith('x402_') else ''):
        addr = verify_token(tok) if tok else ''
        if addr:
            return _seen(addr, 'wallet')
    if local:
        return _seen('local', 'local')
    return _seen(anon_id(ip, h.get('user-agent')), 'anon')


class _read:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _seen(uid, kind, label=None):
    now = time.time()
    with _lock:
        c = db()
        c.execute('INSERT INTO users (id, kind, label, first, last) VALUES (?, ?, ?, ?, ?)'
                  ' ON CONFLICT(id) DO UPDATE SET last=excluded.last',
                  (uid, kind, label, now, now))
        c.commit()
    return {'id': uid, 'kind': kind}


def register(label=None, owner=None):
    """Issue an API key. The key is shown once; only its hash is kept."""
    key = 'x402_' + secrets.token_urlsafe(24)
    uid = 'key:' + _key_hash(key)[:10]
    label = (str(label or '').strip() or uid)[:60]
    now = time.time()
    with _lock:
        c = db()
        c.execute('INSERT INTO users (id, kind, label, first, last, note) VALUES (?, ?, ?, ?, ?, ?)',
                  (uid, 'key', label, now, now, f'issued to {owner}' if owner else None))
        c.execute('INSERT INTO keys (hash, user, label, created) VALUES (?, ?, ?, ?)',
                  (_key_hash(key), uid, label, now))
        c.commit()
    return {'id': uid, 'key': key, 'label': label,
            'use': 'send it as  Authorization: Bearer ' + key}


def revoke(uid):
    with _lock:
        c = db()
        n = c.execute('UPDATE keys SET revoked=1 WHERE user=?', (uid,)).rowcount
        c.commit()
    return {'revoked': n}


def is_owner(uid):
    return uid == 'local' or uid in policy()['owners']


# ── the ledger ────────────────────────────────────────────────────

def log(uid, tool, outcome, url=None, usd=0.0, detail=None):
    with _lock:
        c = db()
        c.execute('INSERT INTO events (user, at, tool, outcome, url, usd, detail)'
                  ' VALUES (?, ?, ?, ?, ?, ?, ?)',
                  (uid, time.time(), tool, outcome, url, float(usd or 0),
                   json.dumps(detail, default=str)[:2000] if detail is not None else None))
        c.commit()


def events(uid=None, limit=50):
    q, a = 'SELECT * FROM events', []
    if uid:
        q, a = q + ' WHERE user=?', [uid]
    rows = db().execute(q + ' ORDER BY id DESC LIMIT ?', a + [max(1, min(int(limit), 500))])
    return [dict(r) for r in rows]


def _midnight():
    t = time.gmtime()
    return time.time() - (t.tm_hour * 3600 + t.tm_min * 60 + t.tm_sec)


def spent_today(uid=None):
    q, a = "SELECT COALESCE(SUM(usd),0) FROM events WHERE outcome='house' AND at>=?", [_midnight()]
    if uid:
        q, a = q + ' AND user=?', a + [uid]
    return round(db().execute(q, a).fetchone()[0], 6)


def observed(url):
    """What callers saw when they used one service through this node."""
    r = db().execute(
        "SELECT COUNT(*) n, SUM(outcome IN ('ok','settled','house')) good,"
        " SUM(outcome IN ('settled','house')) paid, COUNT(DISTINCT user) users,"
        " MAX(at) last FROM events WHERE url=? AND tool='call'", (url,)).fetchone()
    n = r['n'] or 0
    return {'calls': n, 'ok_rate': round((r['good'] or 0) / n, 3) if n else None,
            'paid': r['paid'] or 0, 'users': r['users'] or 0, 'last': r['last']}


# ── vouches ───────────────────────────────────────────────────────

def vouch(voucher, subject, score):
    """One 1..10 rating per (voucher, subject), replaced on re-vouch;
    score 0 withdraws it."""
    if voucher == subject:
        raise ValueError('you cannot vouch for yourself')
    if not user(subject):
        raise ValueError(f'unknown user {subject}')
    s = int(score)
    now = time.time()
    with _lock:
        c = db()
        if s == 0:
            c.execute('DELETE FROM vouches WHERE voucher=? AND subject=?', (voucher, subject))
        elif 1 <= s <= 10:
            c.execute('INSERT INTO vouches (voucher, subject, score, at, first) VALUES (?, ?, ?, ?, ?)'
                      ' ON CONFLICT(voucher, subject) DO UPDATE SET score=excluded.score, at=excluded.at',
                      (voucher, subject, s, now, now))
        else:
            raise ValueError('score is 1..10 (0 withdraws)')
        c.commit()
    return trust(subject)


# ── the score ─────────────────────────────────────────────────────

def user(uid):
    r = db().execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
    return dict(r) if r else None


def _kind(uid):
    if uid == 'local':
        return 'local'
    if uid.startswith('0x'):
        return 'wallet'
    return uid.split(':', 1)[0] if ':' in uid else 'anon'


def _core(uid, u, now):
    """Everything but vouches → (score, parts). Vouch weights use this, so a
    ring of callers vouching for each other cannot lift itself."""
    c = db()
    kind = u['kind'] if u else _kind(uid)
    parts = {'base': BASE.get(kind, 5)}
    if u:
        days = max(0.0, (now - (u['first'] or now)) / DAY)
        parts['tenure'] = round(min(15, 5 * math.log2(1 + days)), 1)
    active = c.execute(
        f"SELECT COUNT(DISTINCT CAST(at/{DAY} AS INTEGER)) FROM events WHERE user=? AND at>=?"
        " AND outcome IN ('ok','settled','house')", (uid, now - 90 * DAY)).fetchone()[0]
    parts['activity'] = round(min(15, 2.5 * active), 1)
    settled = c.execute("SELECT COUNT(*) FROM events WHERE user=? AND outcome='settled'",
                        (uid,)).fetchone()[0]
    parts['settled'] = round(min(25, 8 * math.log2(1 + settled)), 1)
    bad = {r[0]: r[1] for r in c.execute(
        "SELECT outcome, COUNT(*) FROM events WHERE user=? AND at>=? AND outcome IN"
        " ('throttled','denied') GROUP BY outcome", (uid, now - 30 * DAY))}
    parts['throttled'] = -min(20, 2 * bad.get('throttled', 0))
    parts['denied'] = -min(30, 5 * bad.get('denied', 0))
    if u and u['adjust']:
        parts['owner_adjust'] = int(u['adjust'])
    return sum(parts.values()), parts


def trust(uid, now=None):
    """Score, tier and the breakdown that produced them."""
    now = now or time.time()
    u = user(uid)
    kind = u['kind'] if u else _kind(uid)
    out = {'id': uid, 'kind': kind, 'label': (u or {}).get('label'),
           'first_seen': (u or {}).get('first'), 'last_seen': (u or {}).get('last')}
    if is_owner(uid):
        return {**out, 'score': 100, 'tier': 'owner', 'parts': {'owner': 100}, 'fixed': 'owner'}
    if u and u['banned']:
        return {**out, 'score': 0, 'tier': 'restricted', 'parts': {'banned': 0},
                'fixed': 'banned', 'note': u['note']}
    if u and u['pin'] is not None:
        s = max(0, min(100, int(u['pin'])))
        return {**out, 'score': s, 'tier': tier(s), 'parts': {'pinned': s}, 'fixed': 'pinned'}
    total, parts = _core(uid, u, now)
    v, n = 0.0, 0
    for r in db().execute('SELECT voucher, score FROM vouches WHERE subject=?', (uid,)):
        w = 100.0 if is_owner(r['voucher']) else _core(r['voucher'], user(r['voucher']), now)[0]
        if w >= 50:
            v += (min(w, 100) / 100) * (r['score'] - 5.5) / 4.5 * 5
            n += 1
    if n:
        parts['vouches'] = round(max(-15, min(15, v)), 1)
        total += parts['vouches']
    if kind == 'anon':
        total = min(total, ANON_CAP)
    s = int(round(max(0, min(100, total))))
    return {**out, 'score': s, 'tier': tier(s), 'parts': parts, 'vouches': n}


def tier(score):
    return next(name for floor, name in TIERS if score >= floor)


def standing(uid):
    """trust() plus what the tier buys right now."""
    t = trust(uid)
    p = policy()
    t['can'] = sorted(CAN[t['tier']])
    t['rpm'] = p['rpm'].get(t['tier'], 20)
    budget = p['house_daily_usd'].get(t['tier'], 0) if p['house'] else 0
    # The node sponsors only callers who have paid their own way at least
    # once: tenure and activity are cheap to farm with many keys, a settled
    # payment is not.
    paid = t['tier'] == 'owner' or t['parts'].get('settled', 0) > 0 or t.get('fixed') == 'pinned'
    why = (None if p['house'] and 'house' in CAN[t['tier']] and paid else
           'this node does not pay for callers (policy house=false)' if not p['house'] else
           f'the node pays only for trusted callers (score >= 40; you are {t["score"]})'
           if 'house' not in CAN[t['tier']] else
           'the node sponsors callers after their first self-paid call (x402_quote + x402_call)')
    t['house'] = {'enabled': why is None, 'why': why, 'daily_usd': budget,
                  'spent_today': spent_today(uid), 'max_call_usd': p['max_call_usd']}
    nxt = next(((f, n) for f, n in reversed(TIERS) if f > t['score']), None)
    if nxt and t['tier'] != 'owner':
        t['next_tier'] = {'tier': nxt[1], 'at': nxt[0], 'need': nxt[0] - t['score']}
    return t


def users(limit=100, kind=None):
    q, a = 'SELECT id FROM users', []
    if kind:
        q, a = q + ' WHERE kind=?', [kind]
    ids = [r[0] for r in db().execute(q + ' ORDER BY last DESC LIMIT 2000', a)]
    rows = []
    for uid in ids:
        t = trust(uid)
        n = db().execute("SELECT COUNT(*), SUM(outcome IN ('ok','settled','house')),"
                         " SUM(outcome='settled'), COALESCE(SUM(usd),0) FROM events WHERE user=?",
                         (uid,)).fetchone()
        t.update(events=n[0], good=n[1] or 0, settled=n[2] or 0, house_usd=round(n[3], 6))
        rows.append(t)
    rows.sort(key=lambda r: (-r['score'], -(r['last_seen'] or 0)))
    return {'total': len(rows), 'items': rows[:max(1, min(int(limit), 500))]}


def set_trust(uid, pin=None, adjust=None, ban=None, note=None, clear=False):
    """Owner: pin a score, nudge it, ban, or clear all of that."""
    if not user(uid):
        raise ValueError(f'unknown user {uid}')
    with _lock:
        c = db()
        if clear:
            c.execute('UPDATE users SET pin=NULL, adjust=0, banned=0 WHERE id=?', (uid,))
        if pin is not None:
            c.execute('UPDATE users SET pin=? WHERE id=?',
                      (None if str(pin).lower() in ('', 'none', 'null') else int(pin), uid))
        if adjust is not None:
            c.execute('UPDATE users SET adjust=? WHERE id=?', (int(adjust), uid))
        if ban is not None:
            c.execute('UPDATE users SET banned=? WHERE id=?', (int(bool(ban)), uid))
        if note is not None:
            c.execute('UPDATE users SET note=? WHERE id=?', (str(note)[:500], uid))
        c.commit()
    return trust(uid)


# ── rate ──────────────────────────────────────────────────────────

_windows = {}


def allow(uid, rpm):
    """Sliding one-minute window. → True, or False (caller is throttled)."""
    now = time.time()
    with _lock:
        w = _windows.setdefault(uid, deque())
        while w and w[0] < now - 60:
            w.popleft()
        if len(w) >= rpm:
            return False
        w.append(now)
        return True
