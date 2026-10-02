"""x402sem — find a service (or an MCP server) by what it does, not its words.

Every service in the index gets one 384-dim unit vector (all-MiniLM-L6-v2)
over "name. description. path words. tags", stored as a float32 blob next to
the services in the same SQLite file and held in RAM as one matrix. A query
is one encode plus one matrix-vector product over ~37k rows (a few ms).

Ranking fuses three things:

    semantic   cosine(query, service)                 — meaning
    lexical    FTS5 bm25 over the same text (OR terms) — rescues identifiers
                                                          embeddings blur
    usage      log(paid calls in 30d), tiny            — breaks near-ties

The encoder is borrowed, not owned: modsearch (:51090, POST /embed) holds the
box's one copy of the model. If it is down, the model is loaded in-process;
if that fails too, find() still answers on FTS alone and says mode=lexical.
Nothing here leaves the machine.

MCP servers: a paid x402 resource that lives under an MCP endpoint (a /mcp
path segment, an mcp.* host, or a listing that says type=mcp) is a *tool* of
that server. kind=mcp ranks tools, then folds them into their server — the
server's score is its best tool plus a little for every other tool that
matches — so "the right MCP server" is one row with the reasons attached.
"""

import hashlib
import json
import math
import os
import re
import threading
import time
import urllib.request
from urllib.parse import urlsplit

import x402db as store

MODEL = 'sentence-transformers/all-MiniLM-L6-v2'
MODSEARCH = os.environ.get('MODSEARCH_URL', 'http://127.0.0.1:51090')
BATCH = 64          # small: a live query waits behind at most one batch
MAX_TEXT = 600

W_SEM, W_LEX, W_USE = 0.74, 0.22, 0.04

SCHEMA = """
CREATE TABLE IF NOT EXISTS vectors (url TEXT PRIMARY KEY, h TEXT, v BLOB);
"""

_schema_done = set()

_MCP_SEG = re.compile(r'^mcp(?:[-_](?:x402|server|http|sse|v\d+)\b.*)?$', re.I)


# ── what a service is, as text ───────────────────────────────────

def mcp_server(url, type_=None):
    """The MCP endpoint a paid resource belongs to, or None if it isn't one."""
    p = urlsplit(url or '')
    segs = [s for s in p.path.split('/') if s]
    for i, s in enumerate(segs):
        if _MCP_SEG.match(s):
            return f'{p.scheme}://{p.netloc}/' + '/'.join(segs[:i + 1])
    if type_ == 'mcp':
        return url
    if p.netloc.lower().startswith('mcp.'):
        return f'{p.scheme}://{p.netloc}'
    return None


def doc_text(name, description, url, tags):
    p = urlsplit(url or '')
    path = ' '.join(w for w in re.split(r'[/_\-.]+', p.path) if w and not w.isdigit())
    if isinstance(tags, str):
        try:
            tags = json.loads(tags or '[]')
        except json.JSONDecodeError:
            tags = []
    parts = [name or '', description or '', f'{p.netloc} {path}', ' '.join(tags or [])]
    return '. '.join(x.strip() for x in parts if x and x.strip())[:MAX_TEXT]


def _h(text):
    return hashlib.sha1(f'{MODEL}\0{text}'.encode()).hexdigest()[:20]


# ── the encoder (borrowed) ───────────────────────────────────────

class Encoder:
    """modsearch over loopback first; an in-process model second; None last."""

    def __init__(self):
        self.backend = None
        self.error = None
        self._local = None
        self._lock = threading.Lock()

    def _remote(self, texts):
        body = json.dumps({'texts': texts}).encode()
        req = urllib.request.Request(MODSEARCH + '/embed', data=body,
                                     headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=120) as r:
            j = json.load(r)
        if j.get('model', '').split('/')[-1] != MODEL.split('/')[-1]:
            raise RuntimeError(f"modsearch runs {j.get('model')}, not {MODEL}")
        return j['vectors']

    def _inproc(self, texts):
        with self._lock:
            if self._local is None:
                if os.environ.get('X402_LOCAL_ENCODER', '1') == '0':
                    raise RuntimeError('in-process encoder disabled (X402_LOCAL_ENCODER=0)')
                from sentence_transformers import SentenceTransformer
                self._local = SentenceTransformer(MODEL, device='cpu')
            return self._local.encode(texts, batch_size=64, normalize_embeddings=True,
                                      show_progress_bar=False).tolist()

    def encode(self, texts):
        """→ list of unit vectors, or None when no encoder can be had."""
        if not texts:
            return []
        errs = []
        for name, fn in (('modsearch', self._remote), ('local', self._inproc)):
            if name == 'modsearch' and self._local is not None:
                continue            # already paid for a local model; don't flap
            try:
                out = fn(texts)
                self.backend, self.error = name, None
                return out
            except Exception as e:                       # noqa: BLE001 — fall through
                errs.append(f'{name}: {type(e).__name__}: {str(e)[:160]}')
        self.backend, self.error = None, ' | '.join(errs)
        return None


# ── the index ────────────────────────────────────────────────────

class Index:
    """Vectors on disk (SQLite) + one matrix in RAM. Thread-safe; refresh()
    is incremental — only rows whose text changed are re-encoded."""

    def __init__(self, encoder=None):
        self.enc = encoder or Encoder()
        self.state = 'idle'            # idle | building | ready | off
        self.progress = None
        self.built = None
        self._lock = threading.Lock()
        self._busy = threading.Lock()
        self._m = None                 # np.ndarray (n, 384) float32
        self._rows = []                # per matrix row: dict of filterable fields
        self._pos = {}                 # url → matrix row

    # disk ───────────────────────────────────────────────────────

    def _db(self):
        c = store.db()
        if id(c) not in _schema_done:
            c.executescript(SCHEMA)
            _schema_done.add(id(c))
        return c

    def refresh(self):
        """Embed whatever is new or changed, drop what left, rebuild the matrix."""
        if not self._busy.acquire(blocking=False):
            return self.status()
        try:
            import numpy as np
            self.state = 'building'
            c = self._db()
            have = dict(c.execute('SELECT url, h FROM vectors').fetchall())
            todo, live = [], set()
            for url, name, desc, tags, typ in c.execute(
                    'SELECT url, name, description, tags, type FROM services'
                    ' ORDER BY calls_30d DESC, payers_30d DESC'):
                live.add(url)
                t = doc_text(name, desc, url, tags)
                if have.get(url) != _h(t):
                    todo.append((url, t, mcp_server(url, typ) is not None))
            # MCP tools first, then the most-used: a cold first build is
            # useful for the questions people actually ask within seconds.
            todo = [(u, t) for u, t, _ in sorted(todo, key=lambda x: not x[2])]
            gone = [u for u in have if u not in live]
            if gone:
                with store._lock:
                    c.executemany('DELETE FROM vectors WHERE url=?', [(u,) for u in gone])
                    c.commit()
            for i in range(0, len(todo), BATCH):
                chunk = todo[i:i + BATCH]
                self.progress = f'{i}/{len(todo)}'
                vecs = self.enc.encode([t for _, t in chunk])
                if vecs is None:
                    break
                with store._lock:
                    c.executemany(
                        'INSERT OR REPLACE INTO vectors (url, h, v) VALUES (?, ?, ?)',
                        [(u, _h(t), np.asarray(v, dtype=np.float32).tobytes())
                         for (u, t), v in zip(chunk, vecs)])
                    c.commit()
                if i % (BATCH * 32) == 0:
                    self._load()       # searchable while it grows
            self._load()
            self.progress = None
            self.built = time.time()
            self.state = 'ready' if self._m is not None and len(self._rows) else (
                'off' if self.enc.error else 'idle')
        except Exception as e:                           # noqa: BLE001 — reported, not raised
            self.state, self.enc.error = 'off', f'{type(e).__name__}: {e}'
        finally:
            self._busy.release()
        return self.status()

    def _load(self):
        import numpy as np
        c = self._db()
        rows, blobs = [], []
        for r in c.execute(
                'SELECT s.url, s.type, s.networks, s.price_usd, s.calls_30d, v.v'
                ' FROM vectors v JOIN services s ON s.url=v.url'):
            rows.append({'url': r[0], 'mcp': mcp_server(r[0], r[1]),
                         'networks': r[2] or '', 'price': r[3], 'calls': r[4] or 0})
            blobs.append(r[5])
        m = np.frombuffer(b''.join(blobs), dtype=np.float32).reshape(len(blobs), -1) \
            if blobs else None
        with self._lock:
            self._m, self._rows = m, rows
            self._pos = {r['url']: i for i, r in enumerate(rows)}

    def status(self):
        n = len(self._rows)
        return {'state': self.state, 'vectors': n, 'services': store.count(),
                'progress': self.progress, 'backend': self.enc.backend,
                'model': MODEL, 'built': self.built, 'error': self.enc.error,
                'mcp_servers': len({r['mcp'] for r in self._rows if r['mcp']})}

    # search ─────────────────────────────────────────────────────

    def _lexical(self, q, limit=400):
        """FTS5 bm25 over OR'd prefix terms → {url: 0..1}."""
        words = [w for w in re.findall(r'[\w.]+', q.lower()) if len(w) > 1][:12]
        if not words:
            return {}
        expr = ' OR '.join(f'"{w}"*' for w in words)
        try:
            rows = store.db().execute(
                'SELECT url, bm25(services_fts) FROM services_fts WHERE services_fts MATCH ?'
                ' ORDER BY bm25(services_fts) LIMIT ?', (expr, limit)).fetchall()
        except Exception:                                # noqa: BLE001 — bad FTS syntax
            return {}
        if not rows:
            return {}
        best = -min(r[1] for r in rows) or 1.0
        return {u: max(0.0, -s) / best for u, s in rows}

    def find(self, q, kind='all', k=20, network=None, max_price=None):
        import numpy as np
        t0 = time.time()
        q = (q or '').strip()[:400]
        if not q:
            raise ValueError('q is required — describe what you need')
        k = max(1, min(int(k or 20), 200))
        mcp_only = kind == 'mcp'
        lex = self._lexical(q)

        with self._lock:
            m, rows, pos = self._m, self._rows, self._pos
        sem = None
        if m is not None and len(rows):
            qv = self.enc.encode([q])
            if qv:
                sem = m @ np.asarray(qv[0], dtype=np.float32)
        mode = 'semantic' if sem is not None else 'lexical'
        if mode == 'semantic' and len(rows) < store.count() * 0.95:
            mode = 'partial'

        def ok(r):
            if mcp_only and not r['mcp']:
                return False
            if network and f',{network},' not in r['networks']:
                return False
            if max_price is not None and (r['price'] is None or r['price'] > max_price):
                return False
            return True

        cand = {}
        if sem is not None:
            top = np.argsort(-sem)[:4000 if (mcp_only or network or max_price is not None) else 600]
            floor = None              # set by the best row that passes the filters
            for i in top:
                s = float(sem[i])
                if floor is not None and s < floor:
                    break
                if ok(rows[i]):
                    floor = floor if floor is not None else max(0.18, s * 0.55)
                    if s >= floor:
                        cand[rows[i]['url']] = (s, rows[i])
        for u, lv in lex.items():
            if u in cand:
                continue
            i = pos.get(u)
            if i is not None:
                r = rows[i]
                if lv >= 0.35 and ok(r):
                    cand[u] = (float(sem[i]) if sem is not None else 0.0, r)
            elif sem is None or mode == 'partial':
                cand[u] = (0.0, None)     # not embedded yet: lexical only

        scored = []
        for u, (s, r) in cand.items():
            use = min(1.0, math.log10(1 + (r['calls'] if r else 0)) / 4)
            w_sem = W_SEM if sem is not None else 0.0
            score = w_sem * max(s, 0.0) + W_LEX * lex.get(u, 0.0) + W_USE * use
            scored.append((score, s, lex.get(u, 0.0), u))
        scored.sort(reverse=True)

        details = _details([u for *_, u in scored[:2000 if (mcp_only or sem is None) else k]])
        if not (sem is not None):          # lexical-only: filters on the rows we fetched
            scored = [x for x in scored if x[3] in details and _ok_detail(
                details[x[3]], mcp_only, network, max_price)]

        hits = []
        for score, s, lv, u in scored:
            d = details.get(u)
            if not d:
                continue
            d = dict(d, score=round(score, 4), sem=round(s, 4), lex=round(lv, 4),
                     why=_why(q, d))
            hits.append(d)
        out = {'q': q, 'kind': kind, 'mode': mode, 'model': MODEL if sem is not None else None,
               'index': self.status()}
        if mcp_only:
            servers = fold_servers(hits)
            out.update(total=len(servers), items=servers[:k])
        else:
            out.update(total=len(hits), items=hits[:k])
        out['elapsed_ms'] = int((time.time() - t0) * 1000)
        return out


def _ok_detail(d, mcp_only, network, max_price):
    if mcp_only and not d.get('mcp_server'):
        return False
    if network and network not in d['networks']:
        return False
    if max_price is not None and (d['price_usd'] is None or d['price_usd'] > max_price):
        return False
    return True


def _details(urls):
    if not urls:
        return {}
    out = {}
    c = store.db()
    for i in range(0, len(urls), 500):
        chunk = urls[i:i + 500]
        for r in c.execute(
                'SELECT url, host, name, description, method, type, tags, icon, networks,'
                ' price_usd, calls_30d, payers_30d FROM services WHERE url IN (%s)'
                % ','.join('?' * len(chunk)), chunk):
            d = dict(r)
            d['tags'] = json.loads(d['tags'] or '[]')
            d['networks'] = [n for n in (d['networks'] or '').split(',') if n]
            d['mcp_server'] = mcp_server(d['url'], d.pop('type'))
            out[d['url']] = d
    return out


_STOP = frozenset('a an and are as at be by for from i in is it me my of on or the to with '
                  'want need find server mcp tool api that this some any'.split())


def _why(q, d):
    """The query words the service literally shares — shown as the reason."""
    words = {w for w in re.findall(r'[a-z0-9]+', q.lower()) if w not in _STOP and len(w) > 2}
    text = f"{d.get('name') or ''} {d.get('description') or ''} {d['url']}".lower()
    return sorted(w for w in words if w in text)[:6]


def fold_servers(hits):
    """Tool hits → one row per MCP server, best server first."""
    by = {}
    for h in hits:
        srv = h.get('mcp_server')
        if not srv:
            continue
        g = by.setdefault(srv, {'server': srv, 'host': h['host'], 'name': '', 'icon': None,
                                'tools': [], 'networks': set(), 'calls_30d': 0})
        g['tools'].append({k: h[k] for k in ('url', 'name', 'description', 'method',
                                              'price_usd', 'score', 'why')})
        g['name'] = g['name'] or h.get('name') or ''
        g['icon'] = g['icon'] or h.get('icon')
        g['networks'].update(h['networks'])
        g['calls_30d'] += h.get('calls_30d') or 0
    out = []
    for g in by.values():
        sc = sorted((t['score'] for t in g['tools']), reverse=True)
        # Best tool carries the server; each further matching tool adds a
        # little, so a server with five relevant tools edges out a one-off.
        g['score'] = round(sc[0] + 0.02 * sum(sc[1:4]), 4)
        prices = [t['price_usd'] for t in g['tools'] if t['price_usd'] is not None]
        g['min_price'] = min(prices) if prices else None
        g['max_price'] = max(prices) if prices else None
        g['networks'] = sorted(g['networks'])
        g['name'] = g['name'] or g['host']
        g['why'] = sorted({w for t in g['tools'] for w in t['why']})[:6]
        g['connect'] = connect_hint(g['server'], g['host'])
        out.append(g)
    out.sort(key=lambda g: -g['score'])
    return out


def connect_hint(server, host):
    slug = re.sub(r'[^a-z0-9]+', '-', (host or 'x402').lower()).strip('-')[:40]
    return {
        'claude': f'claude mcp add --transport http {slug} {server}',
        'json': {'mcpServers': {slug: {'type': 'http', 'url': server}}},
        'note': 'paid per call over x402: the server answers 402 with its price; '
                'an x402-capable client pays and retries',
    }
