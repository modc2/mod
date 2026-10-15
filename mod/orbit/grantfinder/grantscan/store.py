"""Store — one SQLite file at ~/.mod/grantfinder/grants.db.

grants: one row per program, keyed by its normalised URL, so the same program
found by two lanes (or two sweeps) merges instead of duplicating.
runs:   one row per agent run (lane or verify), with its cost and outcome.
"""
import hashlib
import json
import os
import re
import sqlite3
import threading
import time
import urllib.parse
from pathlib import Path

from . import tags as T

HOME = Path(os.environ.get('GRANTFINDER_HOME', '~/.mod/grantfinder')).expanduser()
DB = HOME / 'grants.db'
_lock = threading.Lock()

FIELDS = ('name', 'funder', 'url', 'apply_url', 'summary', 'eligibility', 'ecosystem',
          'amount_text', 'amount_min_usd', 'amount_max_usd', 'deadline', 'status', 'evidence_url')


def _db():
    HOME.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB, timeout=30)
    c.row_factory = sqlite3.Row
    c.executescript('''
        CREATE TABLE IF NOT EXISTS grants (
            id TEXT PRIMARY KEY, data TEXT NOT NULL, tags TEXT NOT NULL,
            lanes TEXT NOT NULL, first_seen REAL, last_seen REAL);
        CREATE TABLE IF NOT EXISTS runs (
            id TEXT PRIMARY KEY, sweep TEXT, lane TEXT, kind TEXT, status TEXT,
            started REAL, finished REAL, found INTEGER DEFAULT 0, new INTEGER DEFAULT 0,
            cost REAL DEFAULT 0, turns INTEGER DEFAULT 0, searches INTEGER DEFAULT 0,
            error TEXT, steps TEXT DEFAULT '[]');
    ''')
    return c


def norm_url(url: str) -> str:
    u = urllib.parse.urlsplit(str(url or '').strip())
    host = (u.netloc or '').lower()
    host = host[4:] if host.startswith('www.') else host
    path = re.sub(r'/+$', '', u.path or '')
    return f'{host}{path}'.lower()


STOP = {'the', 'grant', 'grants', 'program', 'programme', 'fund', 'funding', 'foundation', 'for', 'of', 'and', 'open', 'source'}


def name_core(name: str) -> str:
    """'Alpha-Omega Grants' and 'Alpha-Omega' -> 'alpha-omega'."""
    words = [w for w in re.findall(r'[a-z0-9]+', str(name).lower()) if w not in STOP]
    return '-'.join(words)


def host(url: str) -> str:
    return norm_url(url).split('/')[0]


def _twin(c, g: dict):
    """Same program under another URL on the same site (a /grants page and the home page)."""
    h, core = host(g.get('url')), name_core(g.get('name'))
    if not core:
        return None
    for r in c.execute('SELECT * FROM grants'):
        d = json.loads(r['data'])
        if host(d.get('url')) == h and name_core(d.get('name')) == core:
            return r
    return None


def gid(g: dict) -> str:
    key = norm_url(g.get('url')) or re.sub(r'\W+', '-', str(g.get('name', '')).lower())
    return hashlib.sha1(key.encode()).hexdigest()[:12]


def clean(g: dict) -> dict:
    out = {}
    for k in FIELDS:
        v = g.get(k)
        if isinstance(v, str):
            v = v.strip()
        if v not in (None, '', []):
            out[k] = v
    for k in ('amount_min_usd', 'amount_max_usd'):
        try:
            out[k] = float(out[k]) if k in out else None
        except (TypeError, ValueError):
            out[k] = None
        if out.get(k) is None:
            out.pop(k, None)
    if out.get('status') not in T.STATUS:
        out['status'] = 'unknown'
    return out


def upsert(items: list, lane: str = '', category: str = '') -> dict:
    """Merge agent records in. Newer non-empty fields win; tags and lanes union."""
    new = seen = 0
    now = time.time()
    with _lock, _db() as c:
        for raw in items:
            g = clean(raw)
            if not g.get('name') or not g.get('url', '').startswith(('http://', 'https://')):
                continue
            g['tags'] = raw.get('tags') or []
            g['categories'] = raw.get('categories') or []
            i = gid(g)
            row = c.execute('SELECT * FROM grants WHERE id=?', (i,)).fetchone() or _twin(c, g)
            if row:
                i = row['id']
                old = json.loads(row['data'])
                merged = {**old, **{k: v for k, v in g.items() if k not in ('tags', 'categories')}}
                lanes = sorted(set(json.loads(row['lanes'])) | ({lane} if lane else set()))
                # a re-tag over the merged record keeps derived tags honest
                tagset = set(json.loads(row['tags'])) | set(T.tag(g, category)) | set(T.tag(merged, category))
                stale = {t for t in tagset if t.startswith('status-')} - {'status-' + merged['status']}
                tagset = sorted(tagset - stale - set(T.STATUS))
                c.execute('UPDATE grants SET data=?, tags=?, lanes=?, last_seen=? WHERE id=?',
                          (json.dumps(merged), json.dumps(tagset), json.dumps(lanes), now, i))
                seen += 1
            else:
                data = {k: v for k, v in g.items() if k not in ('tags', 'categories')}
                c.execute('INSERT INTO grants VALUES (?,?,?,?,?,?)',
                          (i, json.dumps(data), json.dumps(T.tag(g, category)),
                           json.dumps([lane] if lane else []), now, now))
                new += 1
    return {'new': new, 'updated': seen}


def _row(r) -> dict:
    return {'id': r['id'], **json.loads(r['data']), 'tags': json.loads(r['tags']),
            'lanes': json.loads(r['lanes']), 'first_seen': r['first_seen'], 'last_seen': r['last_seen']}


def get(i: str) -> dict:
    with _db() as c:
        r = c.execute('SELECT * FROM grants WHERE id=?', (i,)).fetchone()
    if not r:
        raise KeyError(f'no grant {i}')
    return _row(r)


def grants(q: str = '', tags=None, status: str = '', sort: str = 'deadline', k: int = 0,
           total: bool = False):
    """Filter: q = substring over every field; tags = all must match (AND)."""
    if isinstance(tags, str):
        tags = [t for t in tags.split(',') if t]
    want = {T.norm(t) if not t.startswith(('status-', 'amount-')) else t for t in (tags or [])}
    with _db() as c:
        rows = [_row(r) for r in c.execute('SELECT * FROM grants')]
    ql = q.lower().strip()
    out = []
    for g in rows:
        if want and not want <= set(g['tags']):
            continue
        if status and g.get('status') != status:
            continue
        if ql and ql not in json.dumps(g).lower():
            continue
        out.append(g)
    if sort == 'amount':
        out.sort(key=lambda g: -(g.get('amount_max_usd') or 0))
    elif sort == 'new':
        out.sort(key=lambda g: -g['first_seen'])
    elif sort == 'name':
        out.sort(key=lambda g: g['name'].lower())
    else:  # deadline: open-with-date soonest first, then rolling/open, closed last
        rank = {'open': 0, 'rolling': 1, 'upcoming': 2, 'unknown': 3, 'closed': 4}
        out.sort(key=lambda g: (rank.get(g.get('status'), 3), g.get('deadline') or '9999', g['name'].lower()))
    page = out[:int(k)] if k else out
    return (page, len(out)) if total else page


def retag() -> dict:
    """Re-derive every row's tags under the current tagger and fold same-site twins."""
    with _lock, _db() as c:
        rows = [_row(r) for r in c.execute('SELECT * FROM grants ORDER BY first_seen')]
        c.execute('DELETE FROM grants')
    for g in rows:
        cat = [t for t in ('crypto', 'open-source') if t in g['tags']]
        keep = [t for t in g['tags'] if not t.startswith(('status-', 'amount-')) and t not in T.STATUS]
        upsert([{**g, 'tags': keep, 'categories': cat}], category=cat[0] if cat else '')
    # upsert stamps lanes=[] and first_seen=now: restore both from the old rows
    with _lock, _db() as c:
        for g in rows:
            r = c.execute('SELECT * FROM grants WHERE id=?', (gid(clean(g)),)).fetchone() or _twin(c, g)
            if r:
                lanes = sorted(set(json.loads(r['lanes'])) | set(g['lanes']))
                c.execute('UPDATE grants SET lanes=?, first_seen=MIN(first_seen, ?), last_seen=MAX(last_seen, ?) '
                          'WHERE id=?', (json.dumps(lanes), g['first_seen'], g['last_seen'], r['id']))
    return {'before': len(rows), 'after': stats()['grants']}


def tag_counts() -> dict:
    counts = {}
    with _db() as c:
        for r in c.execute('SELECT tags FROM grants'):
            for t in json.loads(r['tags']):
                counts[t] = counts.get(t, 0) + 1
    groups = {}
    for t, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        groups.setdefault(T.group(t), []).append({'tag': t, 'n': n})
    return groups


def delete(i: str) -> dict:
    with _lock, _db() as c:
        n = c.execute('DELETE FROM grants WHERE id=?', (i,)).rowcount
    return {'deleted': n}


# ── runs ─────────────────────────────────────────────────────────────

def run_start(run_id, sweep, lane, kind):
    with _lock, _db() as c:
        c.execute('INSERT INTO runs (id,sweep,lane,kind,status,started) VALUES (?,?,?,?,?,?)',
                  (run_id, sweep, lane, kind, 'running', time.time()))


def run_step(run_id, step: dict):
    with _lock, _db() as c:
        r = c.execute('SELECT steps FROM runs WHERE id=?', (run_id,)).fetchone()
        steps = (json.loads(r['steps']) if r else [])[-59:] + [step]
        c.execute('UPDATE runs SET steps=?, searches=searches+? WHERE id=?',
                  (json.dumps(steps), 1 if step.get('tool') == 'WebSearch' else 0, run_id))


def run_end(run_id, status, **kw):
    cols = {k: kw[k] for k in ('found', 'new', 'cost', 'turns', 'error') if k in kw}
    sets = ', '.join(f'{k}=?' for k in cols)
    with _lock, _db() as c:
        c.execute(f'UPDATE runs SET status=?, finished=?{", " + sets if sets else ""} WHERE id=?',
                  (status, time.time(), *cols.values(), run_id))


def runs(sweep: str = '', k: int = 50) -> list:
    with _db() as c:
        q, a = ('SELECT * FROM runs WHERE sweep=? ORDER BY started DESC LIMIT ?', (sweep, k)) if sweep \
            else ('SELECT * FROM runs ORDER BY started DESC LIMIT ?', (k,))
        return [{**dict(r), 'steps': json.loads(r['steps'])} for r in c.execute(q, a)]


def mark_orphans():
    """A run still 'running' when the server boots died with the old process."""
    with _lock, _db() as c:
        c.execute("UPDATE runs SET status='lost', finished=?, error='server restarted mid-run' "
                  "WHERE status='running'", (time.time(),))


def stats() -> dict:
    with _db() as c:
        n = c.execute('SELECT COUNT(*) FROM grants').fetchone()[0]
        cost = c.execute('SELECT COALESCE(SUM(cost),0) FROM runs').fetchone()[0]
        last = c.execute("SELECT MAX(finished) FROM runs WHERE status='done'").fetchone()[0]
    return {'grants': n, 'spent_usd': round(cost, 4), 'last_run': last, 'db': str(DB)}
