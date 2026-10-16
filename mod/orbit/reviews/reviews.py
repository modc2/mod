"""reviews — ratings and pluggable, transparent aggregators.

The store is deliberately dumb: a rating is (subject, rater, ref) → score. A
subject is any string another module chooses ("build:polymarket" is build's
reviews of the polymarket module; a ref is the review the rating rode in on).

The interesting part is the AGGREGATOR registry. Every aggregator turns the
same list of ratings into one number, and every answer carries its working:
per-rater weight, why that weight, and the formula — so a score can always be
audited by whoever reads it. New aggregators register with one decorator.

    mean       live   every rating counts once                       (default)
    weighted   live   a published weight table: by kind, by rater
    staketime  soon   weight = the rater's stake-time (bloctime) — the
                      interface is here, the weight provider is not wired

One SQLite file at ~/.mod/reviews/reviews.db (REVIEWS_DIR overrides).
"""

import json
import os
import sqlite3
import threading
import time
import urllib.request
from urllib.parse import urlencode

SCALE = 5
DEFAULT_AGGREGATOR = 'mean'
KINDS = ('human', 'agent')


def _db_path(path=None):
    if path:
        return path
    d = os.environ.get('REVIEWS_DIR') or os.path.expanduser('~/.mod/reviews')
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, 'reviews.db')


def _now():
    return int(time.time())


def _clamp_score(score, scale=SCALE):
    try:
        s = float(score)
    except (TypeError, ValueError):
        raise ValueError('score must be a number')
    if s != s or s < 1 or s > scale:
        raise ValueError(f'score must be between 1 and {scale}')
    return round(s, 2)


# ── aggregators ──────────────────────────────────────────────────────

AGGREGATORS = {}


def aggregator(id, title, formula, status='live', params=None):
    """Register an aggregator. The function receives the list of rating dicts
    and a context dict and returns (weights, meta) where weights is a list of
    {rater, kind, score, weight, why} rows in the same order."""
    def deco(fn):
        AGGREGATORS[id] = {'id': id, 'title': title, 'formula': formula,
                           'status': status, 'params': params or {}, 'fn': fn}
        return fn
    return deco


def aggregators():
    """Every aggregator, as the public catalog (no callables)."""
    out = []
    for a in AGGREGATORS.values():
        out.append({k: v for k, v in a.items() if k != 'fn'})
    out.sort(key=lambda a: (a['status'] != 'live', a['id'] != DEFAULT_AGGREGATOR, a['id']))
    return out


@aggregator('mean', 'average',
            'score = Σ score_i / n — every rating counts once, agent or human')
def _agg_mean(ratings, ctx):
    rows = [{'rater': r['rater'], 'kind': r['kind'], 'score': r['score'],
             'weight': 1.0, 'why': 'every rating counts once'} for r in ratings]
    return rows, {}


IDENTITY_WEIGHTS = {'default': 1.0, 'by_kind': {}, 'by_rater': {}}


def normalize_weights(table):
    """A weight table is {default, by_kind:{human,agent}, by_rater:{addr}}.
    Anything missing falls back to identity; anything negative is refused."""
    if not isinstance(table, dict):
        raise ValueError('weights must be an object')
    out = {'default': 1.0, 'by_kind': {}, 'by_rater': {}}
    if 'default' in table and table['default'] is not None:
        out['default'] = float(table['default'])
    for k, v in (table.get('by_kind') or {}).items():
        if k not in KINDS:
            raise ValueError(f'unknown kind {k!r}')
        out['by_kind'][k] = float(v)
    for k, v in (table.get('by_rater') or {}).items():
        out['by_rater'][str(k).lower()] = float(v)
    for w in [out['default'], *out['by_kind'].values(), *out['by_rater'].values()]:
        if w < 0 or w != w:
            raise ValueError('weights must be ≥ 0')
    return out


@aggregator('weighted', 'custom weights',
            'score = Σ w_i·score_i / Σ w_i — w_i from a PUBLISHED table: '
            'by_rater[addr] if set, else by_kind[kind] if set, else default',
            params={'weights': 'table {default, by_kind:{human,agent}, by_rater:{addr}}; '
                               'passed inline, or stored per subject, or stored for "*"'})
def _agg_weighted(ratings, ctx):
    table = normalize_weights(ctx.get('weights') or IDENTITY_WEIGHTS)
    rows = []
    for r in ratings:
        rater = r['rater'].lower()
        if rater in table['by_rater']:
            w, why = table['by_rater'][rater], f'by_rater[{r["rater"]}]'
        elif r['kind'] in table['by_kind']:
            w, why = table['by_kind'][r['kind']], f'by_kind[{r["kind"]}]'
        else:
            w, why = table['default'], 'default'
        rows.append({'rater': r['rater'], 'kind': r['kind'], 'score': r['score'],
                     'weight': w, 'why': f'{why} = {w:g}'})
    return rows, {'weights': table, 'weights_source': ctx.get('weights_source', 'identity')}


def stake_weights(addresses, url=None):
    """Ask a stake-time provider for each rater's weight. Provider contract:
    GET {url}?address=<addr> → {"weight": <number ≥ 0>}. Returns None when
    there is no provider — the aggregator then says so and falls back."""
    url = url or os.environ.get('REVIEWS_STAKE_URL', '')
    if not url:
        return None
    out = {}
    for a in addresses:
        try:
            with urllib.request.urlopen(f'{url}?{urlencode({"address": a})}', timeout=4) as r:
                out[a] = max(0.0, float(json.load(r).get('weight') or 0))
        except Exception:
            out[a] = 0.0
    return out


@aggregator('staketime', 'stake-time',
            'score = Σ st_i·score_i / Σ st_i — st_i is the rater\'s stake-time '
            '(amount × lock, bloctime); an unstaked rater weighs 0',
            status='soon',
            params={'provider': 'REVIEWS_STAKE_URL — GET ?address= → {weight}'})
def _agg_staketime(ratings, ctx):
    st = stake_weights(sorted({r['rater'] for r in ratings}), ctx.get('stake_url'))
    if st is None:
        return None, {'unavailable': 'no stake-time provider wired (REVIEWS_STAKE_URL)'}
    rows = [{'rater': r['rater'], 'kind': r['kind'], 'score': r['score'],
             'weight': st.get(r['rater'], 0.0),
             'why': f'stake-time {st.get(r["rater"], 0.0):g}'} for r in ratings]
    return rows, {'provider': ctx.get('stake_url') or os.environ.get('REVIEWS_STAKE_URL')}


def aggregate(ratings, aggregator_id=None, ctx=None):
    """Fold ratings into one transparent score. Always answers: an aggregator
    that cannot run (status soon, provider missing, all weights 0) falls back
    to the mean and SAYS so in `fallback`."""
    aid = aggregator_id or DEFAULT_AGGREGATOR
    ctx = dict(ctx or {})
    if aid not in AGGREGATORS:
        raise ValueError(f'unknown aggregator {aid!r} — one of {", ".join(AGGREGATORS)}')
    clean = []
    for r in ratings or []:
        try:
            s = _clamp_score(r.get('score'))
        except ValueError:
            continue
        kind = r.get('kind') if r.get('kind') in KINDS else 'human'
        clean.append({'rater': str(r.get('rater') or 'anon'), 'kind': kind, 'score': s,
                      'ref': r.get('ref')})
    spec = AGGREGATORS[aid]
    base = {'aggregator': aid, 'title': spec['title'], 'formula': spec['formula'],
            'status': spec['status'], 'scale': SCALE, 'n': len(clean),
            'by_kind': {k: sum(1 for r in clean if r['kind'] == k) for k in KINDS}}
    if not clean:
        return {**base, 'score': None, 'breakdown': []}
    rows, meta = spec['fn'](clean, ctx)
    fallback = None
    if rows is None or sum(r['weight'] for r in rows) <= 0:
        fallback = {'to': 'mean',
                    'why': meta.get('unavailable') or 'every weight came out 0'}
        rows, meta2 = AGGREGATORS['mean']['fn'](clean, ctx)
        meta = {**meta, **meta2}
    total = sum(r['weight'] for r in rows)
    score = sum(r['weight'] * r['score'] for r in rows) / total
    for r, c in zip(rows, clean):
        r['share'] = round(r['weight'] / total, 4)
        if c.get('ref'):
            r['ref'] = c['ref']
    out = {**base, 'score': round(score, 2), 'breakdown': rows, **meta}
    if fallback:
        out['fallback'] = fallback
    return out


# ── the store ────────────────────────────────────────────────────────

class Store:
    def __init__(self, path=None):
        self.path = _db_path(path)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS ratings (
                subject TEXT NOT NULL, rater TEXT NOT NULL, ref TEXT NOT NULL DEFAULT '',
                kind TEXT NOT NULL DEFAULT 'human', score REAL NOT NULL,
                note TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT '',
                ts INTEGER NOT NULL,
                PRIMARY KEY (subject, rater, ref));
            CREATE INDEX IF NOT EXISTS ratings_subject ON ratings(subject);
            CREATE TABLE IF NOT EXISTS weights (
                subject TEXT PRIMARY KEY, weights TEXT NOT NULL, ts INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS prefs (
                subject TEXT PRIMARY KEY, aggregator TEXT NOT NULL, ts INTEGER NOT NULL);
        """)

    def close(self):
        self._db.close()

    # ratings
    def rate(self, subject, rater, score, kind='human', ref='', note='', source=''):
        if not subject or not rater:
            raise ValueError('subject and rater are required')
        if kind not in KINDS:
            raise ValueError(f'kind must be one of {", ".join(KINDS)}')
        s = _clamp_score(score)
        row = (str(subject), str(rater).lower(), str(ref or ''), kind, s,
               str(note or '')[:2000], str(source or '')[:200], _now())
        with self._lock:
            self._db.execute(
                'INSERT INTO ratings(subject,rater,ref,kind,score,note,source,ts) '
                'VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(subject,rater,ref) DO UPDATE SET '
                'kind=excluded.kind, score=excluded.score, note=excluded.note, '
                'source=excluded.source, ts=excluded.ts', row)
            self._db.commit()
        return self.get(subject, rater, ref)

    def unrate(self, subject, rater, ref=''):
        with self._lock:
            n = self._db.execute('DELETE FROM ratings WHERE subject=? AND rater=? AND ref=?',
                                 (subject, str(rater).lower(), ref or '')).rowcount
            self._db.commit()
        return {'removed': n}

    def get(self, subject, rater, ref=''):
        r = self._db.execute('SELECT * FROM ratings WHERE subject=? AND rater=? AND ref=?',
                             (subject, str(rater).lower(), ref or '')).fetchone()
        return dict(r) if r else None

    def ratings(self, subject, kind=None):
        q, a = 'SELECT * FROM ratings WHERE subject=?', [subject]
        if kind:
            q += ' AND kind=?'
            a.append(kind)
        return [dict(r) for r in self._db.execute(q + ' ORDER BY ts DESC', a)]

    def subjects(self, prefix=''):
        rows = self._db.execute(
            'SELECT subject, COUNT(*) n, AVG(score) mean, MAX(ts) last FROM ratings '
            'WHERE subject LIKE ? GROUP BY subject ORDER BY last DESC', (prefix + '%',))
        return [{'subject': r['subject'], 'n': r['n'], 'mean': round(r['mean'], 2),
                 'last': r['last']} for r in rows]

    # weights (the transparent table the `weighted` aggregator reads)
    def set_weights(self, subject, table):
        t = normalize_weights(table)
        with self._lock:
            self._db.execute('INSERT INTO weights(subject,weights,ts) VALUES(?,?,?) '
                             'ON CONFLICT(subject) DO UPDATE SET weights=excluded.weights, '
                             'ts=excluded.ts', (subject or '*', json.dumps(t), _now()))
            self._db.commit()
        return {'subject': subject or '*', 'weights': t}

    def weights(self, subject):
        """The table in force for a subject: its own, else "*", else identity."""
        for s in (subject, '*'):
            r = self._db.execute('SELECT weights, ts FROM weights WHERE subject=?', (s,)).fetchone()
            if r:
                return {'subject': subject, 'source': s, 'weights': json.loads(r['weights']),
                        'ts': r['ts']}
        return {'subject': subject, 'source': 'identity', 'weights': IDENTITY_WEIGHTS}

    def set_aggregator(self, subject, aggregator_id):
        if aggregator_id not in AGGREGATORS:
            raise ValueError(f'unknown aggregator {aggregator_id!r}')
        with self._lock:
            self._db.execute('INSERT INTO prefs(subject,aggregator,ts) VALUES(?,?,?) '
                             'ON CONFLICT(subject) DO UPDATE SET aggregator=excluded.aggregator, '
                             'ts=excluded.ts', (subject, aggregator_id, _now()))
            self._db.commit()
        return {'subject': subject, 'aggregator': aggregator_id}

    def aggregator_for(self, subject):
        r = self._db.execute('SELECT aggregator FROM prefs WHERE subject=?', (subject,)).fetchone()
        return r['aggregator'] if r else DEFAULT_AGGREGATOR

    # the answer
    def score(self, subject, aggregator_id=None, weights=None):
        aid = aggregator_id or self.aggregator_for(subject)
        ctx = {}
        if aid == 'weighted':
            if weights is not None:
                ctx = {'weights': normalize_weights(weights), 'weights_source': 'inline'}
            else:
                w = self.weights(subject)
                ctx = {'weights': w['weights'], 'weights_source': w['source']}
        out = aggregate(self.ratings(subject), aid, ctx)
        out['subject'] = subject
        out['default_aggregator'] = self.aggregator_for(subject)
        return out

    def stats(self):
        n = self._db.execute('SELECT COUNT(*) FROM ratings').fetchone()[0]
        s = self._db.execute('SELECT COUNT(DISTINCT subject) FROM ratings').fetchone()[0]
        return {'ratings': n, 'subjects': s, 'aggregators': [a['id'] for a in aggregators()]}
