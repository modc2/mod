"""x402 — every x402 service that exists, in one local index.

x402 is HTTP 402 made real: a resource answers an unpaid request with its
price, the client pays (USDC on Base, Solana, ...) and retries. There is no
single directory of such resources — each facilitator keeps its own list.
This module finds the facilitators, reads every list, and merges them into
one searchable SQLite file on this box.

    m x402                                   # null call → info()
    m x402/sync                              # discover facilitators, crawl all
    m x402/services q=weather max_price=0.01 # search the index
    m x402/service url=https://api.exa.ai/search
    m x402/hosts                             # one row per provider
    m x402/sources                           # every facilitator + crawl status
    m x402/partners category=Facilitators    # the ecosystem registry
    m x402/probe url=https://…               # ask one URL for its 402, pin it
    m x402/add_source base_url=https://my-facilitator.example
    m x402/serve                             # console + API on :51110

Self-sustaining: facilitators are found from the coinbase/x402 ecosystem
registry, not hard-coded, and the server re-crawls on a timer. No keys, no
SDK, no account — the index is a file you can copy, query or delete.

This is the anchor: the orbit loader imports it by path and instantiates
``Mod``; serve.py dispatches into the same class so CLI and API cannot drift.
"""

import hashlib
import json
import os
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
# Appended, never prepended: mod.py here would shadow the protocol's `mod`.
if HERE not in sys.path:
    sys.path.append(HERE)

import x402db as store                                       # noqa: E402
import x402src as src                                        # noqa: E402

DAY = 86400


class Mod:
    description = """
    x402 — aggregates every x402 (HTTP 402 pay-per-request) service that
    exists into one local, searchable index. Facilitators are discovered from
    the coinbase/x402 ecosystem registry and crawled through the spec's
    discovery API (CDP Bazaar, PayAI and any other that lists); single URLs
    can be probed for their 402 and pinned. Keyless, stdlib-only, SQLite.
    """

    _sync_lock = threading.Lock()
    _syncing = None

    def __init__(self, port=None, local=True, **kwargs):
        self.dir = HERE
        cfg = self.config()
        self.port = int(port or os.environ.get('PORT') or cfg.get('port', 51110))
        self.base = cfg.get('base_path', '/x402')
        self.local = bool(local)
        self.sync_every = int(cfg.get('sync_every', 6 * 3600))
        for s in src.SEEDS:
            if not store.get_source(s['id']):
                store.put_source(s['id'], 'facilitator', s['name'], s['base_url'], 'seed')

    # ── plumbing ─────────────────────────────────────────────────

    def config(self):
        try:
            with open(os.path.join(HERE, 'config.json')) as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def forward(self, *args, **kwargs):
        """Protocol entry point — a bare `m x402` lands here."""
        return self.info()

    def info(self):
        """What this module is, and the shape of the index right now."""
        cfg = self.config()
        return {
            'module': 'x402',
            'version': cfg.get('version'),
            'what': cfg.get('description'),
            'index': store.stats(),
            'syncing': Mod._syncing,
            'last_sync': store.meta('last_sync'),
            'store': store.DB,
            'urls': cfg.get('urls'),
        }

    def health(self):
        s = store.stats()
        return {'ok': True, 'services': s['services'], 'syncing': Mod._syncing}

    def readme(self):
        p = os.path.join(HERE, 'README.md')
        return open(p).read() if os.path.exists(p) else self.description

    # ── reading the index ────────────────────────────────────────

    def services(self, q=None, network=None, host=None, source=None,
                 max_price=None, min_price=None, priced=False,
                 sort='popular', limit=50, offset=0):
        """Search every known x402 service. q is full-text (prefix words);
        prices are USD per call; sort = popular|cheap|pricey|new|recent|host."""
        return store.search(q=q, network=network, host=host, source=source,
                            max_price=_num(max_price), min_price=_num(min_price),
                            priced=_flag(priced), sort=sort,
                            limit=int(limit or 50), offset=int(offset or 0))

    def service(self, url=None):
        """One service in full: offers, sources that list it, raw listing."""
        url = src.canonical(_need(url, 'url'))
        s = store.get(url)
        if not s:
            raise ValueError(f'not indexed: {url} — try probe url=…')
        return s

    def hosts(self, q=None, limit=100, offset=0):
        """Providers: services grouped by host, busiest first."""
        return store.hosts(q=q, limit=int(limit or 100), offset=int(offset or 0))

    def networks(self):
        """How many services accept payment on each network."""
        return store.stats()['networks']

    def stats(self):
        return store.stats()

    def sources(self):
        """Every source: facilitators (seeded, discovered, added) + status."""
        return store.get_sources()

    def partners(self, category=None, q=None):
        """The coinbase/x402 ecosystem registry (cached; `discover` refreshes)."""
        return store.get_partners(category=category, q=q)

    def facilitators(self):
        """Ecosystem partners that run a facilitator, with their crawl state."""
        by_url = {_key(s['base_url']): s for s in store.get_sources()}
        out = []
        for p in store.get_partners():
            f = p.get('facilitator')
            if not f or not f.get('baseUrl'):
                continue
            s = by_url.get(_key(f['baseUrl'])) or {}
            out.append({'slug': p['slug'], 'name': p['name'], 'base_url': f['baseUrl'],
                        'networks': f.get('networks') or [], 'website': p['website'],
                        'lists': s.get('lists'), 'indexed': s.get('indexed', 0),
                        'source': s.get('id')})
        return sorted(out, key=lambda r: (-(r['indexed'] or 0), r['name'].lower()))

    # ── growing the index ────────────────────────────────────────

    def discover(self):
        """Refresh the ecosystem registry and turn every facilitator in it into
        a source. Each is asked once whether it lists; only listers get crawled."""
        partners = src.ecosystem()
        store.put_partners(partners)
        known = {_key(s['base_url']): s for s in store.get_sources()}
        added = []
        for p in partners:
            f = p.get('facilitator') or {}
            base = (f.get('baseUrl') or '').strip()
            if not base.startswith('http'):
                continue
            if _key(base) not in known:
                sid = p['slug'] if not store.get_source(p['slug']) else _sid(base)
                store.put_source(sid, 'facilitator', p['name'], base, 'ecosystem')
                known[_key(base)] = {'id': sid}
                added.append(sid)
        self._check_listing([s for s in store.get_sources() if s['kind'] == 'facilitator'])
        store.meta('last_discover', time.time())
        return {'partners': len(partners), 'added': added,
                'listing': [s['id'] for s in store.get_sources() if s['lists']]}

    def _check_listing(self, rows):
        from concurrent.futures import ThreadPoolExecutor

        def one(s):
            total = src.lists(s['base_url'])
            store.set_source(s['id'], lists=int(total is not None), total=total)
        with ThreadPoolExecutor(12) as ex:
            list(ex.map(one, rows))

    def add_source(self, base_url=None, name=None):
        """Add a facilitator by base URL. Checked for the discovery API first."""
        base = _need(base_url, 'base_url').rstrip('/')
        if not base.startswith(('http://', 'https://')):
            raise ValueError('base_url must be http(s)')
        total = src.lists(base)
        sid = _sid(base)
        store.put_source(sid, 'facilitator', name or src.host_of(base), base, 'manual')
        store.set_source(sid, lists=int(total is not None), total=total)
        return {'id': sid, 'lists': total is not None, 'total': total}

    def remove_source(self, id=None):
        """Remove a source; services only it listed leave the index."""
        sid = _need(id, 'id')
        if not store.get_source(sid):
            raise ValueError(f'no source {sid}')
        return {'removed': sid, 'services_dropped': store.drop_source(sid)}

    def probe(self, url=None, method='GET', pin=True):
        """Ask one URL directly. A 402 with payment requirements is an x402
        service; it is indexed and pinned (no directory can retire it)."""
        url = _need(url, 'url')
        r = src.probe(url, method=method)
        if r.get('x402') and _flag(pin):
            store.put_source('probe', 'probe', 'Direct probes', '', 'local')
            store.upsert([r['service']], 'probe', pinned=True)
        if r.get('service'):
            r['service'] = {k: v for k, v in r['service'].items() if k != 'raw'}
        return r

    def forget(self, url=None):
        """Drop one service (a pinned probe, say). Directories may re-add it."""
        url = src.canonical(_need(url, 'url'))
        store.forget(url)
        return {'forgot': url}

    def sync(self, source=None, background=False, discover=None):
        """Crawl every listing source (or one: source=<id>). Discovers new
        facilitators first when the registry is over a day old."""
        if _flag(background):
            threading.Thread(target=self._sync_safe, args=(source, discover),
                             daemon=True).start()
            return {'started': True, 'source': source or 'all'}
        return self._sync(source, discover)

    def _sync_safe(self, source=None, discover=None):
        try:
            self._sync(source, discover)
        except Exception as e:                           # noqa: BLE001 — logged to meta
            store.meta('last_error', f'{type(e).__name__}: {e}')

    def _sync(self, source=None, discover=None):
        if not Mod._sync_lock.acquire(blocking=False):
            return {'busy': True, 'syncing': Mod._syncing}
        try:
            report = {'discover': None, 'sources': {}}
            stale = time.time() - (store.meta('last_discover') or 0) > DAY
            if source is None and (_flag(discover) if discover is not None else stale):
                Mod._syncing = 'discover'
                try:
                    report['discover'] = self.discover()
                except src.SourceError as e:              # registry down: crawl what we know
                    report['discover'] = {'error': str(e)}
            rows = store.get_sources()
            if source:
                rows = [s for s in rows if s['id'] == source]
                if not rows:
                    raise ValueError(f'no source {source}')
            todo = [s for s in rows if s['kind'] == 'facilitator' and s['enabled']]
            self._check_listing([s for s in todo if s['lists'] is None])
            todo = [store.get_source(s['id']) for s in todo]
            todo = [s for s in todo if s and s['lists']]
            Mod._syncing = 'crawl'
            # One thread per facilitator: the wait is theirs, not ours, and the
            # store serializes writes behind its own lock.
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max(1, min(4, len(todo)))) as ex:
                for sid, res in zip([s['id'] for s in todo], ex.map(self._crawl, todo)):
                    report['sources'][sid] = res
            store.meta('last_sync', time.time())
            report['index'] = store.stats()
            return report
        finally:
            Mod._syncing = None
            Mod._sync_lock.release()

    def _crawl(self, s):
        sid, start, n, batch = s['id'], time.time(), 0, []
        store.set_source(sid, status='crawling', error=None, last_sync=start)

        def on_page(offset, total):
            store.set_source(sid, progress=f'{offset}/{total if total is not None else "?"}',
                             total=total)
        try:
            for row in src.crawl(s['base_url'], on_page=on_page):
                batch.append(row)
                if len(batch) >= 500:
                    n += store.upsert(batch, sid, now=start)
                    batch = []
            n += store.upsert(batch, sid, now=start)
        except src.SourceError as e:
            # Partial crawl: keep what we have, retire nothing.
            store.set_source(sid, status='error', error=str(e), count=n, progress=None)
            return {'ok': False, 'indexed': n, 'error': str(e)}
        dropped = store.retire(sid, start)
        store.set_source(sid, status='ok', count=n, last_ok=time.time(), progress=None)
        return {'ok': True, 'indexed': n, 'dropped': dropped,
                'seconds': round(time.time() - start, 1)}

    def autosync(self):
        """Loop forever, re-crawling when the index is older than sync_every."""
        while True:
            last = store.meta('last_sync') or 0
            if time.time() - last > self.sync_every:
                self._sync_safe()
            time.sleep(60)

    # ── ops ──────────────────────────────────────────────────────

    def serve(self, port=None, background=False):
        """Console + API on one port (default :51110)."""
        port = int(port or self.port)
        if not background:
            import serve as srv
            return srv.serve(port)
        proc = subprocess.Popen([sys.executable, os.path.join(HERE, 'serve.py'),
                                 '--port', str(port)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                stdin=subprocess.DEVNULL, cwd=HERE)
        return {'pid': proc.pid, 'url': f'http://localhost:{port}{self.base}/'}

    def test(self):
        r = subprocess.run([sys.executable, '-m', 'pytest', '-q', 'tests'],
                           cwd=HERE, capture_output=True, text=True)
        return {'ok': r.returncode == 0, 'out': r.stdout[-4000:] + r.stderr[-2000:]}

    def kill(self, port=None):
        """Stop whatever listens on this module's port (by port, never by name)."""
        port = int(port or self.port)
        pids = subprocess.run(['bash', '-c', f'lsof -ti tcp:{port} || true'],
                              capture_output=True, text=True).stdout.split()
        for pid in pids:
            subprocess.run(['kill', pid], capture_output=True)
        return {'killed': pids}


def _need(value, name):
    if value in (None, ''):
        raise ValueError(f'{name} is required')
    return str(value)


def _flag(v):
    return str(v).lower() in ('1', 'true', 'yes', 'on') if v is not None else False


def _num(v):
    return None if v in (None, '') else float(v)


def _key(url):
    return src.canonical(url or '').rstrip('/')


def _sid(base):
    h = src.host_of(base).replace('.', '-') or 'src'
    return f'{h}-{hashlib.sha1(base.encode()).hexdigest()[:6]}'
