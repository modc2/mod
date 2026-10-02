"""x402db — the local index. One SQLite file under data/; nothing else.

A service is keyed by its canonical URL. Every source that lists it leaves a
*sighting*; a crawl that completes retires the sightings it did not renew, and
a service nobody lists any more leaves the index — unless it was pinned by a
direct probe, which is first-hand evidence and outranks any directory.

    services     one row per paid resource (merged across sources)
    services_fts full-text over url/host/name/description/tags
    sightings    (url, source) — who lists what, and when they last did
    sources      facilitators and the like: what to crawl, how it went
    partners     the coinbase/x402 ecosystem registry, cached
"""

import json
import os
import sqlite3
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.environ.get('X402_DB') or os.path.join(HERE, 'data', 'x402.db')

_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS services (
    url TEXT PRIMARY KEY, host TEXT, name TEXT, description TEXT,
    method TEXT, type TEXT, tags TEXT, icon TEXT, x402_version INTEGER,
    networks TEXT, price_usd REAL, offers TEXT,
    calls_30d INTEGER DEFAULT 0, payers_30d INTEGER DEFAULT 0,
    last_updated TEXT, first_seen REAL, last_seen REAL,
    pinned INTEGER DEFAULT 0, raw TEXT
);
CREATE INDEX IF NOT EXISTS services_host ON services(host);
CREATE INDEX IF NOT EXISTS services_price ON services(price_usd);
CREATE VIRTUAL TABLE IF NOT EXISTS services_fts USING fts5(
    url, host, name, description, tags, tokenize='unicode61'
);
CREATE TABLE IF NOT EXISTS sightings (
    url TEXT, source TEXT, last_seen REAL, PRIMARY KEY (url, source)
);
CREATE INDEX IF NOT EXISTS sightings_source ON sightings(source);
CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY, kind TEXT, name TEXT, base_url TEXT,
    origin TEXT, enabled INTEGER DEFAULT 1, lists INTEGER,
    total INTEGER, count INTEGER DEFAULT 0, status TEXT DEFAULT 'idle',
    progress TEXT, error TEXT, last_sync REAL, last_ok REAL
);
CREATE TABLE IF NOT EXISTS partners (
    slug TEXT PRIMARY KEY, name TEXT, category TEXT, description TEXT,
    website TEXT, logo TEXT, facilitator TEXT, fetched REAL
);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""


def connect(path=None):
    path = path or DB
    os.makedirs(os.path.dirname(path), exist_ok=True)
    c = sqlite3.connect(path, timeout=60)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL')
    c.execute('PRAGMA synchronous=NORMAL')
    c.executescript(SCHEMA)
    return c


# One connection per thread. Under WAL, readers never wait for a writer, so
# the console stays live while four facilitators are being crawled; only
# writers queue behind _lock.
_local = threading.local()
_gen = 0


def db():
    c = getattr(_local, 'conn', None)
    if c is None or getattr(_local, 'gen', None) != _gen:
        c = _local.conn = connect(DB)
        _local.gen = _gen
    return c


class _Reader:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


_read = _Reader()


def reset(path=None):
    """Point the store at another file (tests)."""
    global DB, _gen
    with _lock:
        DB = path or DB
        _gen += 1


# ── services ──────────────────────────────────────────────────────

_COLS = ('url', 'host', 'name', 'description', 'method', 'type', 'tags', 'icon',
         'x402_version', 'networks', 'price_usd', 'offers', 'calls_30d',
         'payers_30d', 'last_updated', 'raw')


def _row_values(s):
    v = dict(s)
    v['tags'] = json.dumps(s.get('tags') or [])
    v['networks'] = ',' + ','.join(s.get('networks') or []) + ','
    v['offers'] = json.dumps(s.get('offers') or [])
    v['raw'] = json.dumps(s.get('raw') or {}, default=str)
    return [v.get(k) for k in _COLS]


def upsert(rows, source, now=None, pinned=False):
    """Merge services in. Richer data wins field-by-field: a later listing with
    an empty name never blanks a name another source supplied."""
    now = now or time.time()
    n = 0
    with _lock:
        c = db()
        for s in rows:
            vals = _row_values(s)
            old = c.execute('SELECT * FROM services WHERE url=?', (s['url'],)).fetchone()
            if old:
                merged = []
                for k, v in zip(_COLS, vals):
                    if k in ('calls_30d', 'payers_30d'):
                        merged.append(max(v or 0, old[k] or 0))
                    elif v in (None, '', '[]', ',,', '{}'):
                        merged.append(old[k])
                    else:
                        merged.append(v)
                c.execute(f'UPDATE services SET {", ".join(k + "=?" for k in _COLS)},'
                          ' last_seen=?, pinned=MAX(pinned, ?) WHERE url=?',
                          merged + [now, int(pinned), s['url']])
                c.execute('DELETE FROM services_fts WHERE url=?', (s['url'],))
                vals = merged
            else:
                c.execute(f'INSERT INTO services ({", ".join(_COLS)}, first_seen, last_seen, pinned)'
                          f' VALUES ({", ".join("?" * len(_COLS))}, ?, ?, ?)',
                          vals + [now, now, int(pinned)])
            d = dict(zip(_COLS, vals))
            c.execute('INSERT INTO services_fts (url, host, name, description, tags)'
                      ' VALUES (?, ?, ?, ?, ?)',
                      (d['url'], d['host'], d['name'], d['description'],
                       ' '.join(json.loads(d['tags'] or '[]'))))
            c.execute('INSERT INTO sightings (url, source, last_seen) VALUES (?, ?, ?)'
                      ' ON CONFLICT(url, source) DO UPDATE SET last_seen=excluded.last_seen',
                      (s['url'], source, now))
            n += 1
        c.commit()
    return n


def retire(source, before):
    """After a complete crawl: drop the source's stale sightings, then any
    unpinned service no source lists any more. → number of services removed."""
    with _lock:
        c = db()
        c.execute('DELETE FROM sightings WHERE source=? AND last_seen<?', (source, before))
        orphans = [r[0] for r in c.execute(
            'SELECT url FROM services WHERE pinned=0 AND url NOT IN (SELECT url FROM sightings)')]
        for u in orphans:
            c.execute('DELETE FROM services WHERE url=?', (u,))
            c.execute('DELETE FROM services_fts WHERE url=?', (u,))
        c.commit()
        return len(orphans)


def forget(url):
    with _lock:
        c = db()
        c.execute('DELETE FROM services WHERE url=?', (url,))
        c.execute('DELETE FROM services_fts WHERE url=?', (url,))
        c.execute('DELETE FROM sightings WHERE url=?', (url,))
        c.commit()


def _service(r, full=False):
    d = {k: r[k] for k in r.keys() if k != 'raw'}
    d['tags'] = json.loads(d.get('tags') or '[]')
    d['networks'] = [n for n in (d.get('networks') or '').split(',') if n]
    d['offers'] = json.loads(d.get('offers') or '[]')
    d['pinned'] = bool(d.get('pinned'))
    if 'sources' in d:
        d['sources'] = [s for s in (d['sources'] or '').split(',') if s]
    if full:
        d['raw'] = json.loads(r['raw'] or '{}')
    return d


SORTS = {
    'popular': 'calls_30d DESC, payers_30d DESC, s.last_seen DESC',
    'cheap': 'price_usd IS NULL, price_usd ASC',
    'pricey': 'price_usd IS NULL, price_usd DESC',
    'new': 'first_seen DESC',
    'recent': 'last_updated DESC',
    'host': 'host ASC, url ASC',
}


def _fts_query(q):
    """User text → a safe FTS5 query: each word a quoted prefix term."""
    words = [w.replace('"', '') for w in str(q).split() if w.replace('"', '')]
    return ' '.join(f'"{w}"*' for w in words[:12])


def search(q=None, network=None, host=None, source=None, max_price=None,
           min_price=None, priced=None, sort='popular', limit=50, offset=0):
    where, args = [], []
    if q and _fts_query(q):
        where.append('s.url IN (SELECT url FROM services_fts WHERE services_fts MATCH ?)')
        args.append(_fts_query(q))
    if network:
        where.append('s.networks LIKE ?')
        args.append(f'%,{network},%')
    if host:
        where.append('s.host = ?')
        args.append(str(host).lower())
    if source:
        where.append('s.url IN (SELECT url FROM sightings WHERE source=?)')
        args.append(source)
    if max_price is not None:
        where.append('s.price_usd <= ?')
        args.append(float(max_price))
    if min_price is not None:
        where.append('s.price_usd >= ?')
        args.append(float(min_price))
    if priced:
        where.append('s.price_usd IS NOT NULL')
    w = ('WHERE ' + ' AND '.join(where)) if where else ''
    order = SORTS.get(sort or 'popular', SORTS['popular'])
    limit = max(1, min(int(limit or 50), 500))
    offset = max(0, int(offset or 0))
    with _read:
        c = db()
        total = c.execute(f'SELECT COUNT(*) FROM services s {w}', args).fetchone()[0]
        rows = c.execute(
            f'SELECT s.*, (SELECT GROUP_CONCAT(source) FROM sightings g WHERE g.url=s.url) AS sources'
            f' FROM services s {w} ORDER BY {order} LIMIT ? OFFSET ?',
            args + [limit, offset]).fetchall()
    return {'total': total, 'limit': limit, 'offset': offset,
            'items': [_service(r) for r in rows]}


def get(url):
    with _read:
        r = db().execute(
            'SELECT s.*, (SELECT GROUP_CONCAT(source) FROM sightings g WHERE g.url=s.url) AS sources'
            ' FROM services s WHERE url=?', (url,)).fetchone()
    return _service(r, full=True) if r else None


def hosts(q=None, limit=100, offset=0):
    """Services grouped by host — one row per provider."""
    where, args = '', []
    if q:
        where, args = 'WHERE host LIKE ? OR name LIKE ?', [f'%{q}%', f'%{q}%']
    with _read:
        c = db()
        total = c.execute(f'SELECT COUNT(DISTINCT host) FROM services {where}', args).fetchone()[0]
        rows = c.execute(
            f'SELECT host, COUNT(*) AS services, MAX(name) AS name, MAX(icon) AS icon,'
            f' MIN(price_usd) AS min_price, MAX(price_usd) AS max_price,'
            f' SUM(calls_30d) AS calls_30d, MAX(payers_30d) AS payers_30d'
            f' FROM services {where} GROUP BY host'
            f' ORDER BY calls_30d DESC, services DESC LIMIT ? OFFSET ?',
            args + [max(1, min(int(limit), 500)), int(offset)]).fetchall()
    return {'total': total, 'items': [dict(r) for r in rows]}


def count():
    return db().execute('SELECT COUNT(*) FROM services').fetchone()[0]


def stats():
    with _read:
        c = db()
        one = lambda sql, *a: c.execute(sql, a).fetchone()[0]  # noqa: E731
        nets = {}
        for (n,) in c.execute('SELECT networks FROM services'):
            for x in (n or '').split(','):
                if x:
                    nets[x] = nets.get(x, 0) + 1
        prices = [r[0] for r in c.execute(
            'SELECT price_usd FROM services WHERE price_usd IS NOT NULL ORDER BY price_usd')]
        return {
            'services': one('SELECT COUNT(*) FROM services'),
            'hosts': one('SELECT COUNT(DISTINCT host) FROM services'),
            'pinned': one('SELECT COUNT(*) FROM services WHERE pinned=1'),
            'active_30d': one('SELECT COUNT(*) FROM services WHERE calls_30d>0'),
            'calls_30d': one('SELECT COALESCE(SUM(calls_30d),0) FROM services'),
            'sources': one('SELECT COUNT(*) FROM sources WHERE enabled=1'),
            'listing_sources': one('SELECT COUNT(*) FROM sources WHERE enabled=1 AND lists=1'),
            'partners': one('SELECT COUNT(*) FROM partners'),
            'networks': dict(sorted(nets.items(), key=lambda kv: -kv[1])),
            'median_price_usd': prices[len(prices) // 2] if prices else None,
            'overlap': one('SELECT COUNT(*) FROM (SELECT url FROM sightings'
                           ' GROUP BY url HAVING COUNT(*)>1)'),
        }


# ── sources ───────────────────────────────────────────────────────

def put_source(id, kind, name, base_url, origin, enabled=True):
    with _lock:
        c = db()
        c.execute('INSERT INTO sources (id, kind, name, base_url, origin, enabled)'
                  ' VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET'
                  ' name=excluded.name, base_url=excluded.base_url',
                  (id, kind, name, base_url, origin, int(enabled)))
        c.commit()


def set_source(id, **fields):
    if not fields:
        return
    with _lock:
        c = db()
        c.execute(f'UPDATE sources SET {", ".join(k + "=?" for k in fields)} WHERE id=?',
                  list(fields.values()) + [id])
        c.commit()


def drop_source(id):
    with _lock:
        c = db()
        c.execute('DELETE FROM sources WHERE id=?', (id,))
        c.commit()
    return retire(id, float('inf'))


def get_sources():
    with _read:
        rows = db().execute(
            'SELECT s.*, (SELECT COUNT(*) FROM sightings g WHERE g.source=s.id) AS indexed'
            ' FROM sources s ORDER BY lists DESC, indexed DESC, id').fetchall()
    return [dict(r) for r in rows]


def get_source(id):
    with _read:
        r = db().execute('SELECT * FROM sources WHERE id=?', (id,)).fetchone()
    return dict(r) if r else None


# ── partners ──────────────────────────────────────────────────────

def put_partners(rows, now=None):
    now = now or time.time()
    with _lock:
        c = db()
        c.execute('DELETE FROM partners')
        for p in rows:
            c.execute('INSERT INTO partners VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                      (p['slug'], p['name'], p['category'], p['description'],
                       p['website'], p['logo'],
                       json.dumps(p['facilitator']) if p.get('facilitator') else None, now))
        c.commit()


def get_partners(category=None, q=None):
    where, args = [], []
    if category:
        where.append('category=?')
        args.append(category)
    if q:
        where.append('(name LIKE ? OR description LIKE ? OR website LIKE ?)')
        args += [f'%{q}%'] * 3
    w = ('WHERE ' + ' AND '.join(where)) if where else ''
    with _read:
        rows = db().execute(f'SELECT * FROM partners {w} ORDER BY category, name', args).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d['facilitator'] = json.loads(d['facilitator']) if d['facilitator'] else None
        out.append(d)
    return out


# ── meta ──────────────────────────────────────────────────────────

def meta(k, v=None):
    if v is None:
        r = db().execute('SELECT v FROM meta WHERE k=?', (k,)).fetchone()
        return json.loads(r[0]) if r else None
    with _lock:
        c = db()
        c.execute('INSERT OR REPLACE INTO meta VALUES (?, ?)', (k, json.dumps(v)))
        c.commit()
        return v
