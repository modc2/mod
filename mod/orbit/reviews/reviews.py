"""reviews — ratings and pluggable, transparent aggregators.

The store is deliberately dumb: a rating is (subject, rater, ref) → score. A
subject is any string another module chooses ("build:polymarket" is build's
reviews of the polymarket module; a ref is the review the rating rode in on).

The interesting part is the AGGREGATOR registry. Every aggregator turns the
same list of ratings into one number, and every answer carries its working:
per-rater weight, why that weight, and the formula — so a score can always be
audited by whoever reads it. New aggregators register with one decorator.

    mean       live   every rating counts once                       (default)
    median     live   the middle rating — one loud rater can't move it
    trimmed    live   drop the top and bottom tails, average the rest
    consensus  live   raters far from the crowd's median weigh less
    recent     live   older ratings fade by a half-life
    weighted   live   a published weight table: by kind, by rater
    market     live   every aggregator answers; their answers are blended
                      by how the MARKET ranks each aggregator
    staketime  soon   weight = the rater's stake-time (bloctime) — the
                      interface is here, the weight provider is not wired

THE MARKET. Aggregators are themselves subjects: `agg:<id>` is rated 1–5 like
anything else. Anyone can publish a new one without code — a base from the
list above plus params (a weight table, a trim, a half-life) under a new id.
`market()` ranks every aggregator by its own (smoothed) rating; the `market`
aggregator is the consensus of aggregators, weighted by that ranking.

One SQLite file at ~/.mod/reviews/reviews.db (REVIEWS_DIR overrides).
"""

import json
import os
import re
import sqlite3
import threading
import time
import urllib.request
from urllib.parse import urlencode

SCALE = 5
DEFAULT_AGGREGATOR = 'mean'
KINDS = ('human', 'agent')
AGG_SUBJECT = 'agg:'          # aggregators are rated under agg:<id>
PRIOR, PRIOR_N = 3.0, 2       # an unrated aggregator stands at 3★ as if 2 people said so
MIN_PUBLISHED_RATINGS = 3     # a published aggregator joins the market blend after 3 raters


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


def _row(r, w, why):
    return {'rater': r['rater'], 'kind': r['kind'], 'score': r['score'], 'weight': w, 'why': why}


def _median(xs):
    xs = sorted(xs)
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


@aggregator('median', 'median',
            'score = the middle rating (the two middle ones averaged when n is even)')
def _agg_median(ratings, ctx):
    order = sorted(range(len(ratings)), key=lambda i: ratings[i]['score'])
    n = len(order)
    mid = {order[n // 2]} if n % 2 else {order[n // 2 - 1], order[n // 2]}
    w = 1.0 if n % 2 else 0.5
    return [_row(r, w if i in mid else 0.0, 'the middle' if i in mid else 'not the middle')
            for i, r in enumerate(ratings)], {}


@aggregator('trimmed', 'trimmed mean',
            'score = mean after dropping ⌊n·trim⌋ ratings from each end',
            params={'trim': 'fraction cut from EACH end, 0–0.45 (default 0.2)'})
def _agg_trimmed(ratings, ctx):
    trim = float((ctx.get('params') or {}).get('trim', 0.2))
    k = int(len(ratings) * trim)
    order = sorted(range(len(ratings)), key=lambda i: ratings[i]['score'])
    low, high = set(order[:k]), set(order[len(order) - k:]) if k else set()
    rows = []
    for i, r in enumerate(ratings):
        if i in low:
            rows.append(_row(r, 0.0, f'trimmed (bottom {trim:g})'))
        elif i in high:
            rows.append(_row(r, 0.0, f'trimmed (top {trim:g})'))
        else:
            rows.append(_row(r, 1.0, 'kept'))
    return rows, {'trimmed': 2 * k}


@aggregator('consensus', 'consensus',
            'c = median score; w_i = 1 / (1 + k·|score_i − c|); score = Σ w_i·score_i / Σ w_i '
            '— agree with the crowd and you count more',
            params={'k': 'how hard disagreement is punished, ≥ 0 (default 1)'})
def _agg_consensus(ratings, ctx):
    k = float((ctx.get('params') or {}).get('k', 1))
    c = _median([r['score'] for r in ratings])
    rows = []
    for r in ratings:
        d = abs(r['score'] - c)
        w = round(1 / (1 + k * d), 4)
        rows.append(_row(r, w, f'|{r["score"]:g} − {c:g}| = {d:g} → {w:g}'))
    return rows, {'consensus': c}


@aggregator('recent', 'recent first',
            'w_i = ½^(age_days_i / half_life); score = Σ w_i·score_i / Σ w_i',
            params={'half_life_days': 'days for a rating to count half, > 0 (default 30)'})
def _agg_recent(ratings, ctx):
    hl = float((ctx.get('params') or {}).get('half_life_days', 30))
    now = ctx.get('now') or _now()
    rows = []
    for r in ratings:
        age = max(0.0, (now - (r.get('ts') or now)) / 86400)
        w = round(0.5 ** (age / hl), 4)
        rows.append(_row(r, w, f'{age:.1f}d old → {w:g}'))
    return rows, {'half_life_days': hl}


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
    table = normalize_weights(ctx.get('weights') or (ctx.get('params') or {}).get('weights')
                              or IDENTITY_WEIGHTS)
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


@aggregator('market', 'market blend',
            'every other live aggregator scores the subject; score = Σ m_a·score_a / Σ m_a '
            'where m_a = standing_a − 1 and standing = the aggregator\'s own smoothed rating '
            '(agg:<id>). Per rater: w_i = Σ m_a·share_ai / Σ m_a',
            params={'standing': f'(Σ stars + {PRIOR:g}·{PRIOR_N}) / (n + {PRIOR_N}) over ratings on agg:<id>'})
def _agg_market(ratings, ctx):
    entries, n = [], len(ratings)
    for e in ctx.get('blend') or []:
        if e['weight'] <= 0:
            continue
        res = aggregate(ratings, e['id'], e['ctx'], spec=e['spec'])
        if res.get('fallback') or res.get('score') is None:
            continue
        entries.append((e, res))
    if not entries:
        return None, {'unavailable': 'no aggregator in the market could answer'}
    total = sum(e['weight'] for e, _ in entries)
    eff = [0.0] * n
    for e, res in entries:
        for i, row in enumerate(res['breakdown']):
            eff[i] += e['weight'] * row['share'] / total
    top = max(entries, key=lambda x: x[0]['weight'])[0]['id']
    rows = [_row(r, round(eff[i], 4), f'blend of {len(entries)} aggregators (heaviest: {top})')
            for i, r in enumerate(ratings)]
    blend = [{'id': e['id'], 'score': res['score'], 'standing': e['standing'],
              'weight': round(e['weight'], 4), 'share': round(e['weight'] / total, 4)}
             for e, res in sorted(entries, key=lambda x: -x[0]['weight'])]
    return rows, {'blend': blend}


# Bases a published aggregator may build on — params only, never code, never a URL.
PUBLISHABLE = ('mean', 'median', 'trimmed', 'consensus', 'recent', 'weighted')


def normalize_params(base, params):
    params = params or {}
    if not isinstance(params, dict):
        raise ValueError('params must be an object')
    if base == 'weighted':
        return {'weights': normalize_weights(params.get('weights') or {})}
    if base == 'trimmed':
        t = float(params.get('trim', 0.2))
        if not 0 <= t <= 0.45:
            raise ValueError('trim must be between 0 and 0.45')
        return {'trim': t}
    if base == 'consensus':
        k = float(params.get('k', 1))
        if not 0 <= k <= 100:
            raise ValueError('k must be between 0 and 100')
        return {'k': k}
    if base == 'recent':
        h = float(params.get('half_life_days', 30))
        if not 0 < h <= 3650:
            raise ValueError('half_life_days must be between 0 and 3650')
        return {'half_life_days': h}
    return {}


def aggregate(ratings, aggregator_id=None, ctx=None, spec=None):
    """Fold ratings into one transparent score. Always answers: an aggregator
    that cannot run (status soon, provider missing, all weights 0) falls back
    to the mean and SAYS so in `fallback`."""
    aid = aggregator_id or DEFAULT_AGGREGATOR
    ctx = dict(ctx or {})
    if spec is None and aid not in AGGREGATORS:
        raise ValueError(f'unknown aggregator {aid!r} — one of {", ".join(AGGREGATORS)}')
    clean = []
    for r in ratings or []:
        try:
            s = _clamp_score(r.get('score'))
        except ValueError:
            continue
        kind = r.get('kind') if r.get('kind') in KINDS else 'human'
        clean.append({'rater': str(r.get('rater') or 'anon'), 'kind': kind, 'score': s,
                      'ref': r.get('ref'), 'ts': r.get('ts')})
    spec = spec or AGGREGATORS[aid]
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
    if spec.get('base'):
        base['base'], base['author'] = spec['base'], spec.get('author', '')
    if ctx.get('params'):
        base['params'] = ctx['params']
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
            CREATE TABLE IF NOT EXISTS published (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, base TEXT NOT NULL,
                params TEXT NOT NULL, author TEXT NOT NULL DEFAULT '',
                note TEXT NOT NULL DEFAULT '', ts INTEGER NOT NULL);
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
        self.resolve(aggregator_id)
        with self._lock:
            self._db.execute('INSERT INTO prefs(subject,aggregator,ts) VALUES(?,?,?) '
                             'ON CONFLICT(subject) DO UPDATE SET aggregator=excluded.aggregator, '
                             'ts=excluded.ts', (subject, aggregator_id, _now()))
            self._db.commit()
        return {'subject': subject, 'aggregator': aggregator_id}

    def aggregator_for(self, subject):
        r = self._db.execute('SELECT aggregator FROM prefs WHERE subject=?', (subject,)).fetchone()
        return r['aggregator'] if r else DEFAULT_AGGREGATOR

    # published aggregators — a base + params, no code
    def publish(self, id, base, params=None, title='', author='', note='', overwrite=False):
        id = str(id or '').strip().lower()
        if not re.fullmatch(r'[a-z0-9][a-z0-9_.-]{1,39}', id):
            raise ValueError('id: 2–40 chars of a-z 0-9 _ . -')
        if id in AGGREGATORS:
            raise ValueError(f'{id!r} is a built-in aggregator')
        if base not in PUBLISHABLE:
            raise ValueError(f'base must be one of {", ".join(PUBLISHABLE)}')
        p = normalize_params(base, params)
        if not overwrite and self._published(id):
            raise ValueError(f'{id!r} is already published')
        row = (id, str(title or id)[:80], base, json.dumps(p), str(author or '').lower()[:120],
               str(note or '')[:1000], _now())
        with self._lock:
            self._db.execute('INSERT OR REPLACE INTO published(id,title,base,params,author,note,ts) '
                             'VALUES(?,?,?,?,?,?,?)', row)
            self._db.commit()
        return self._published(id)

    def retract(self, id):
        with self._lock:
            n = self._db.execute('DELETE FROM published WHERE id=?', (id,)).rowcount
            self._db.execute('DELETE FROM prefs WHERE aggregator=?', (id,))
            self._db.commit()
        return {'removed': n}

    def _published(self, id):
        r = self._db.execute('SELECT * FROM published WHERE id=?', (id,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d['params'] = json.loads(d['params'])
        return d

    def published(self):
        return [self._published(r['id']) for r in
                self._db.execute('SELECT id FROM published ORDER BY ts DESC')]

    def resolve(self, aid):
        """id → (spec, params). Built-ins come from the registry; a published
        id borrows its base's function and formula and brings its own params."""
        if aid in AGGREGATORS:
            return AGGREGATORS[aid], {}
        p = self._published(aid)
        if not p:
            raise ValueError(f'unknown aggregator {aid!r}')
        b = AGGREGATORS[p['base']]
        spec = {**b, 'id': aid, 'title': p['title'], 'base': p['base'], 'author': p['author'],
                'formula': f'{b["formula"]} — params {json.dumps(p["params"])}'}
        return spec, p['params']

    def _ctx(self, aid, subject='', weights=None):
        spec, params = self.resolve(aid)
        ctx = {'params': params} if params else {}
        if aid == 'weighted':
            if weights is not None:
                ctx = {'weights': normalize_weights(weights), 'weights_source': 'inline'}
            else:
                w = self.weights(subject or '*')
                ctx = {'weights': w['weights'], 'weights_source': w['source']}
        elif spec.get('base') == 'weighted':
            ctx = {'weights': params['weights'], 'weights_source': f'published:{aid}'}
        elif aid == 'market':
            ctx = {'blend': self._blend(subject)}
        return spec, ctx

    # the market — aggregators ranked by their own ratings
    def market(self):
        ids = [a['id'] for a in aggregators()] + [p['id'] for p in self.published()]
        adopters = {r['aggregator']: r['n'] for r in self._db.execute(
            'SELECT aggregator, COUNT(*) n FROM prefs GROUP BY aggregator')}
        rated = {r['subject'][len(AGG_SUBJECT):]: (r['n'], r['s']) for r in self._db.execute(
            'SELECT subject, COUNT(*) n, SUM(score) s FROM ratings WHERE subject LIKE ? '
            'GROUP BY subject', (AGG_SUBJECT + '%',))}
        out = []
        for aid in ids:
            spec, params = self.resolve(aid)
            n, tot = rated.get(aid, (0, 0.0))
            standing = round((tot + PRIOR * PRIOR_N) / (n + PRIOR_N), 3)
            published = 'base' in spec
            in_blend = (spec['status'] == 'live' and aid not in ('market', 'staketime')
                        and (not published or n >= MIN_PUBLISHED_RATINGS))
            out.append({'id': aid, 'title': spec['title'], 'formula': spec['formula'],
                        'status': spec['status'], 'published': published,
                        'base': spec.get('base'), 'author': spec.get('author', ''),
                        'params': params or spec.get('params') or {},
                        'rating': {'mean': round(tot / n, 2) if n else None, 'n': n},
                        'standing': standing, 'adopters': adopters.get(aid, 0),
                        'in_blend': in_blend,
                        'blend_weight': round(max(0.0, standing - 1), 3) if in_blend else 0.0})
        out.sort(key=lambda a: (-a['standing'], -a['adopters'], -a['rating']['n'], a['id']))
        for i, a in enumerate(out):
            a['rank'] = i + 1
        return {'aggregators': out,
                'standing': f'(Σ stars + {PRIOR:g}·{PRIOR_N}) / (n + {PRIOR_N}) over ratings on '
                            f'{AGG_SUBJECT}<id> — unrated stands at {PRIOR:g}',
                'blend': 'market aggregator weight m = standing − 1; published aggregators join '
                         f'after {MIN_PUBLISHED_RATINGS} ratings',
                'rate_one': f'rate subject={AGG_SUBJECT}<id> score=1–5'}

    def _blend(self, subject=''):
        out = []
        for a in self.market()['aggregators']:
            if not a['in_blend']:
                continue
            spec, ctx = self._ctx(a['id'], subject)
            out.append({'id': a['id'], 'weight': a['blend_weight'], 'standing': a['standing'],
                        'spec': spec, 'ctx': ctx})
        return out

    # the answer
    def score(self, subject, aggregator_id=None, weights=None):
        aid = aggregator_id or self.aggregator_for(subject)
        spec, ctx = self._ctx(aid, subject, weights)
        out = aggregate(self.ratings(subject), aid, ctx, spec=spec)
        out['subject'] = subject
        out['default_aggregator'] = self.aggregator_for(subject)
        return out

    def aggregate(self, ratings, aggregator_id=None, weights=None, subject=''):
        """Stateless over the ratings passed in — but a subject lets weighted
        read its published table, and published/market ids resolve here."""
        aid = aggregator_id or (self.aggregator_for(subject) if subject else DEFAULT_AGGREGATOR)
        spec, ctx = self._ctx(aid, subject, weights)
        if weights is not None and aid != 'weighted' and spec.get('base') != 'weighted':
            ctx = {**ctx, 'weights': normalize_weights(weights), 'weights_source': 'inline'}
        out = aggregate(ratings, aid, ctx, spec=spec)
        if subject:
            out['subject'] = subject
            out['default_aggregator'] = self.aggregator_for(subject)
        return out

    def stats(self):
        n = self._db.execute('SELECT COUNT(*) FROM ratings').fetchone()[0]
        s = self._db.execute('SELECT COUNT(DISTINCT subject) FROM ratings').fetchone()[0]
        p = self._db.execute('SELECT COUNT(*) FROM published').fetchone()[0]
        return {'ratings': n, 'subjects': s, 'aggregators': [a['id'] for a in aggregators()],
                'published': p}
