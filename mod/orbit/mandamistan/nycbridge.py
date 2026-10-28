"""The dependency seam: mandamistan reads the real city through ``orbit/nyc``.

nyc is a running service (API :50310, see ../nyc/config.json) that already
knows how to talk to NYC Open Data, with every trap handled — text-typed
numbers, stale lotteries, bulk-deed medians, the 11M-row violations file.
This module never re-implements any of that; it asks nyc and remembers the
answer.

Resolution, in order, every call stamped with where it came from:

    memory  → an in-process TTL cache (one process, one dict)
    live    → GET http://localhost:<nyc port>/<fn>?…  (NYC_API env overrides)
    disk    → ~/.mod/mandamistan/cache/<key>.json, any age (marked stale)
    snapshot→ snapshot.json beside this file, baked by `m mandamistan/snapshot`

The console therefore always renders — with live numbers when nyc is up,
with yesterday's when it is slow, and with the committed snapshot when it is
absent (tests run that way, offline). HTTP rather than an in-process import
on purpose: nyc pulls fastapi + a GIS stack, and every mod ships a ``mod.py``
that shadows the protocol package when imported from the wrong cwd.
"""

import json
import os
import threading
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

HERE = os.path.dirname(os.path.abspath(__file__))
SNAPSHOT = os.path.join(HERE, 'snapshot.json')
CACHE_DIR = os.path.join(os.path.expanduser('~'), '.mod', 'mandamistan', 'cache')

# Seconds an answer stays fresh in memory/disk before we ask nyc again.
TTL = {'stats': 6 * 3600, 'affordable': 24 * 3600, 'rents': 24 * 3600,
       'nycha': 24 * 3600, 'violations': 6 * 3600,
       'lotteries': 900, 'homes': 3600, 'health': 60}
DEFAULT_TTL = 3600
TIMEOUT = float(os.environ.get('NYC_TIMEOUT', '120'))

_mem = {}
_lock = threading.Lock()
_snapshot = None


def nyc_url():
    """Where nyc's API is. Env wins; otherwise the sibling module's config."""
    env = os.environ.get('NYC_API')
    if env:
        return env.rstrip('/')
    try:
        with open(os.path.join(HERE, '..', 'nyc', 'config.json')) as f:
            port = json.load(f).get('port', 50310)
    except Exception:
        port = 50310
    return f'http://localhost:{port}'


def _key(fn, args):
    items = sorted((k, str(v)) for k, v in (args or {}).items() if v not in (None, ''))
    return fn + ('?' + urlencode(items) if items else '')


def _disk_path(key):
    safe = ''.join(c if c.isalnum() or c in '-_.' else '_' for c in key)
    return os.path.join(CACHE_DIR, safe + '.json')


def _snap():
    global _snapshot
    if _snapshot is None:
        try:
            with open(SNAPSHOT) as f:
                _snapshot = json.load(f)
        except Exception:
            _snapshot = {}
    return _snapshot


def _http(fn, args):
    url = nyc_url() + '/' + fn
    q = {k: v for k, v in (args or {}).items() if v not in (None, '')}
    if q:
        url += '?' + urlencode(q)
    req = Request(url, headers={'Accept': 'application/json',
                                'User-Agent': 'mandamistan/0.2 (+nyc dep)'})
    with urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode('utf-8'))


def fetch(fn, args=None, max_age=None, fetcher=None):
    """One nyc read. Returns ``{'data': …, 'source': 'live|cache|stale|snapshot',
    'fetched_at': iso, 'error': …}`` — never raises."""
    args = args or {}
    key = _key(fn, args)
    ttl = max_age if max_age is not None else TTL.get(fn, DEFAULT_TTL)
    now = time.time()

    with _lock:
        hit = _mem.get(key)
    if hit and now - hit['t'] < ttl:
        return {'data': hit['data'], 'source': 'cache', 'fetched_at': hit['at']}

    disk = None
    try:
        with open(_disk_path(key)) as f:
            disk = json.load(f)
        if now - disk['t'] < ttl:
            with _lock:
                _mem[key] = disk
            return {'data': disk['data'], 'source': 'cache', 'fetched_at': disk['at']}
    except Exception:
        disk = None

    err = None
    try:
        data = (fetcher or _http)(fn, args)
        if isinstance(data, dict) and data.get('error') and len(data) <= 2:
            raise RuntimeError(data['error'])
        rec = {'t': now, 'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now)),
               'data': data}
        with _lock:
            _mem[key] = rec
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            tmp = _disk_path(key) + '.tmp'
            with open(tmp, 'w') as f:
                json.dump(rec, f)
            os.replace(tmp, _disk_path(key))
        except Exception:
            pass
        return {'data': data, 'source': 'live', 'fetched_at': rec['at']}
    except Exception as e:  # noqa: BLE001 — degrade, never crash the console
        err = f'{type(e).__name__}: {e}'

    if disk:
        return {'data': disk['data'], 'source': 'stale', 'fetched_at': disk['at'],
                'error': err}
    snap = _snap().get(key) or _snap().get(fn)
    if snap is not None:
        return {'data': snap.get('data', snap), 'source': 'snapshot',
                'fetched_at': _snap().get('_meta', {}).get('taken_at'), 'error': err}
    return {'data': None, 'source': 'none', 'fetched_at': None, 'error': err}


def status():
    """Is nyc reachable, and what are we serving from?"""
    r = fetch('health', max_age=60)
    snap = _snap().get('_meta', {})
    return {'nyc_api': nyc_url(), 'reachable': r['source'] in ('live', 'cache'),
            'source': r['source'], 'error': r.get('error'),
            'snapshot_taken_at': snap.get('taken_at'),
            'cache_dir': CACHE_DIR}


# What the snapshot bakes — the exact reads reality.py makes, defaults only.
SNAPSHOT_READS = [
    ('stats', {'geography': 'borough'}),
    ('stats', {'geography': 'nta', 'limit': 400, 'sort': 'median_income'}),
    ('affordable', {}),
    ('rents', {}),
    ('nycha', {}),
    ('violations', {'limit': 5}),
    ('lotteries', {'limit': 50}),
    ('homes', {'max_rent': 1500, 'limit': 50}),
]


def snapshot(path=SNAPSHOT):
    """Re-bake snapshot.json from live nyc. Returns what was captured."""
    out = {'_meta': {'taken_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                     'nyc_api': nyc_url()}}
    got, missed = [], []
    for fn, args in SNAPSHOT_READS:
        r = fetch(fn, args, max_age=0)
        if r['source'] == 'live' or (r['data'] is not None and r['source'] != 'snapshot'):
            out[_key(fn, args)] = {'data': r['data'], 'fetched_at': r['fetched_at']}
            got.append(_key(fn, args))
        else:
            missed.append({'read': _key(fn, args), 'error': r.get('error')})
    if got:
        # keep anything previously baked that this run could not refresh
        for k, v in _snap().items():
            out.setdefault(k, v)
        tmp = path + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(out, f)
        os.replace(tmp, path)
        global _snapshot
        _snapshot = None
    return {'path': path, 'captured': got, 'missed': missed}


def warm(background=True):
    """Pull every scorecard read once so the first console load is instant."""
    def run():
        for fn, args in SNAPSHOT_READS:
            fetch(fn, args)
    if not background:
        run()
        return {'warmed': True}
    t = threading.Thread(target=run, daemon=True, name='mandamistan-warm')
    t.start()
    return {'warming': True}
