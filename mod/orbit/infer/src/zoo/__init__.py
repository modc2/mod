"""The zoo — every ONNX model this box can find, one searchable catalog.

`router/` merges inference routers; this merges the places a *model file* can
come from, so "give me something to optimize" has more than three answers:

    builtin      every architecture family, built here           (no network)
    onnx-tests   the onnx wheel's own ~1,900 conformance models   (no network)
    torchvision  torchvision's 121 registered architectures       (no network)
    github       the ONNX Model Zoo, ~2,300 files, + repos you add
    huggingface  every HuggingFace repo tagged onnx
    modelscope   every ModelScope repo with onnx in its libraries
    kaggle       every Kaggle model instance published as ONNX

Local-first: the three offline sources are listed the moment anything asks,
so the catalog is never empty. The four remote ones are crawled in the
background — all at once, each on its own thread since they are different
hosts — and each page is appended to disk as it arrives, so a crawl that is
interrupted resumes from its last cursor on the next start instead of
beginning again. A re-crawl writes beside the finished catalog and swaps in
only when it completes; readers never see a half-empty zoo.

Planting is the only step that downloads a model, and it lands in the same
content-addressed store as everything else (`engine.store`), so a zoo model is
optimized, benchmarked and run in the browser exactly like an upload.
"""

import json
import os
import threading
import time

from .base import DOMAINS, Source, ZooError, default_file, is_onnx_archive, is_onnx_path  # noqa: F401


def _plantable(p):
    return is_onnx_path(p) or is_onnx_archive(p)
from .builtin import ARCHS, Builtin
from .github import GitHub
from .hf import HuggingFace
from .kaggle import Kaggle
from .modelscope import ModelScope
from .onnxtests import OnnxTests
from .tv import TorchVision

SOURCES = {s.name: s for s in (Builtin(), OnnxTests(), TorchVision(), GitHub(),
                               HuggingFace(), ModelScope(), Kaggle())}
ORDER = list(SOURCES)
TTL = float(os.environ.get('INFER_ZOO_TTL', 7 * 86400))


def _dir():
    import engine
    d = os.path.join(engine.STATE_DIR, 'zoo')
    os.makedirs(d, exist_ok=True)
    return d


def _p(source, kind):
    return os.path.join(_dir(), f'{source}.{kind}')


def _read_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def _write_json(path, obj):
    tmp = path + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(obj, f, default=str)
    os.replace(tmp, path)


def meta(source):
    return _read_json(_p(source, 'state.json'), {}) or {}


def _set_meta(source, **kw):
    m = meta(source)
    m.update(kw)
    _write_json(_p(source, 'state.json'), m)
    return m


# ── reading the catalog ─────────────────────────────────────────

_cache = {}               # source → (signature, rows, haystacks)
_cache_lock = threading.Lock()


def _rows_file(source):
    """The finished catalog if there is one; the crawl in progress if that is
    all there is, so a first crawl shows results as it goes."""
    done, part = _p(source, 'jsonl'), _p(source, 'partial.jsonl')
    if os.path.exists(done):
        return done
    return part if os.path.exists(part) else None


def rows(source):
    path = _rows_file(source)
    if not path:
        return [], []
    st = os.stat(path)
    sig = (path, st.st_mtime_ns, st.st_size)
    with _cache_lock:
        hit = _cache.get(source)
        if hit and hit[0] == sig:
            return hit[1], hit[2]
    by_key = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue              # a torn last line from a killed crawl
            by_key[e['key']] = e
    out = list(by_key.values())
    hay = [' '.join(str(x) for x in (e['key'], e.get('name'), e.get('task'),
                                     e.get('author'), e.get('family'), e.get('library'),
                                     ' '.join(e.get('tags') or []), e.get('about') or '')
                    ).lower() for e in out]
    with _cache_lock:
        _cache[source] = (sig, out, hay)
    return out, hay


def _ensure_local():
    """List the offline sources the first time anything asks — they cost
    nothing and make the zoo non-empty on an air-gapped box."""
    for name, src in SOURCES.items():
        if src.kind != 'remote' and not _rows_file(name) and name not in JOBS:
            try:
                _run(src, fresh=True)
            except Exception as e:
                _set_meta(name, error=f'{type(e).__name__}: {e}')


def get(key):
    source = key.split(':', 1)[0]
    if source not in SOURCES:
        raise ZooError(f'no source {source!r} — have: {", ".join(SOURCES)}', 404)
    _ensure_local()
    for e in rows(source)[0]:
        if e['key'] == key:
            return e
    raise ZooError(f'{key} is not in the {source} catalog — scrape it first, or '
                   f'check the spelling', 404, source)


def _size(e):
    f = default_file(e.get('files') or [])
    for p, s in e.get('files') or []:
        if p == f:
            return s
    return None


def search(q=None, source=None, domain=None, task=None, author=None, local=None,
           max_bytes=None, sort='downloads', limit=50, offset=0, facets=True):
    """One row per model across every source, filtered and ranked."""
    _ensure_local()
    names = [s.strip() for s in str(source).split(',')] if source else ORDER
    bad = [n for n in names if n not in SOURCES]
    if bad:
        raise ZooError(f'no source {bad[0]!r} — have: {", ".join(SOURCES)}', 404)
    terms = [t for t in str(q or '').lower().split() if t]
    doms = set(str(domain).split(',')) if domain else None
    hits = []
    for n in names:
        rs, hay = rows(n)
        for e, h in zip(rs, hay):
            if terms and not all(t in h for t in terms):
                continue
            if doms and e.get('domain') not in doms:
                continue
            if task and task.lower() not in str(e.get('task') or '').lower():
                continue
            if author and str(e.get('author') or '').lower() != author.lower():
                continue
            if local is not None and bool(e.get('local')) != bool(local):
                continue
            if max_bytes:
                s = _size(e)
                if s is None or s > max_bytes:
                    continue
            hits.append(e)
    keyf = {
        'downloads': lambda e: -(e.get('downloads') or 0),
        'likes': lambda e: -(e.get('likes') or 0),
        'name': lambda e: str(e.get('name')).lower(),
        'recent': lambda e: str(e.get('updated') or e.get('created') or ''),
        'size': lambda e: _size(e) if _size(e) is not None else 1 << 62,
        'source': lambda e: (ORDER.index(e['source']), str(e.get('name')).lower()),
    }.get(sort or 'downloads')
    if keyf is None:
        raise ZooError('sort= is one of downloads, likes, name, recent, size, source')
    hits.sort(key=keyf, reverse=(sort == 'recent'))
    out = {'count': len(hits), 'offset': int(offset), 'sort': sort,
           'models': [_public(e) for e in hits[int(offset):int(offset) + int(limit)]]}
    if facets:
        f = {'source': {}, 'domain': {}, 'task': {}, 'author': {}}
        for e in hits:
            for k in f:
                v = e.get(k)
                if v is not None:
                    f[k][v] = f[k].get(v, 0) + 1
        top = lambda d, n: dict(sorted(d.items(), key=lambda kv: -kv[1])[:n])  # noqa: E731
        out['facets'] = {'source': f['source'], 'domain': top(f['domain'], 20),
                         'task': top(f['task'], 40), 'author': top(f['author'], 40)}
    return out


def _public(e):
    d = dict(e)
    d['default_file'] = default_file(e.get('files') or [])
    d['bytes'] = _size(e)
    d['n_files'] = len([f for f in e.get('files') or [] if _plantable(f[0])])
    d.pop('files', None)
    return d


def show(key, job=None):
    """One row with every file in it, sizes resolved from the source."""
    e = get(key)
    src = SOURCES[e['source']]
    try:
        files = src.files(e, job=job)
    except ZooError as ex:
        files, err = e.get('files') or [], ex.message
    else:
        err = None
    d = dict(e)
    d['files'] = [{'path': p, 'bytes': s, 'onnx': _plantable(p)} for p, s in files]
    d['default_file'] = default_file(files)
    if err:
        d['files_error'] = err
    return d


# ── crawling ─────────────────────────────────────────────────────

JOBS = {}                 # source → Job
_jobs_lock = threading.Lock()


class Job:
    def __init__(self, source):
        self.source, self.stopped, self.log = source, False, []
        self.started, self.rows, self.pages, self.total = time.time(), 0, 0, None
        self.error, self.done, self.thread = None, False, None

    def say(self, msg):
        self.log = (self.log + [f'{time.strftime("%H:%M:%S")} {msg}'])[-15:]

    def dict(self):
        return {'source': self.source, 'running': bool(self.thread and self.thread.is_alive()),
                'rows': self.rows, 'pages': self.pages, 'total': self.total,
                'started': self.started, 'error': self.error, 'done': self.done,
                'stopped': self.stopped, 'log': self.log[-6:]}


def _run(src, fresh=False, job=None):
    job = job or Job(src.name)
    m = meta(src.name)
    part = _p(src.name, 'partial.jsonl')
    resume = not fresh and m.get('complete') is False and os.path.exists(part) \
        and m.get('state')
    state = m.get('state') if resume else {}
    if not resume and os.path.exists(part):
        os.remove(part)
    _set_meta(src.name, complete=False, running=True, started=time.time(), error=None,
              state=state, resumed=bool(resume), stopped_by_user=False)
    job.say(('resuming' if resume else 'starting') + f' {src.name}')
    try:
        for page, state, done, total in src.scrape(job, state or {}):
            with open(part, 'a') as f:
                for e in page:
                    f.write(json.dumps(e, default=str) + '\n')
            job.rows += len(page)
            job.pages += 1
            job.total = total or job.total
            _set_meta(src.name, state=state, pages=job.pages, total=job.total)
            if job.stopped:
                raise ZooError('stopped', 499)
            if done:
                break
        os.replace(part, _p(src.name, 'jsonl'))
        n = len(rows(src.name)[0])
        _set_meta(src.name, complete=True, running=False, finished=time.time(),
                  count=n, state={}, error=None)
        job.done = True
        job.say(f'done — {n:,} models')
    except ZooError as e:
        job.error = e.message
        _set_meta(src.name, running=False, error=e.message,
                  stopped_by_user=e.status == 499)
        job.say(f'stopped: {e.message}')
        if e.status != 499:
            raise
    except Exception as e:
        job.error = f'{type(e).__name__}: {e}'
        _set_meta(src.name, running=False, error=job.error)
        job.say(f'failed: {job.error}')
        raise
    return job


def scrape(sources=None, fresh=False, wait=False):
    """Crawl sources in the background — every source by default, each on
    its own thread. Returns immediately with the job table unless wait."""
    names = [s.strip() for s in str(sources).split(',')] if sources else ORDER
    bad = [n for n in names if n not in SOURCES]
    if bad:
        raise ZooError(f'no source {bad[0]!r} — have: {", ".join(SOURCES)}', 404)
    started = []
    with _jobs_lock:
        for n in names:
            j = JOBS.get(n)
            if j and j.thread and j.thread.is_alive():
                continue
            j = Job(n)

            def go(src=SOURCES[n], job=j):
                try:
                    _run(src, fresh=fresh, job=job)
                except Exception:
                    pass              # recorded on the job and in state.json
            j.thread = threading.Thread(target=go, name=f'zoo-{n}', daemon=True)
            JOBS[n] = j
            j.thread.start()
            started.append(n)
    if wait:
        for n in names:
            JOBS[n].thread.join()
    return {'started': started, **status()}


def stop(sources=None):
    names = [s.strip() for s in str(sources).split(',')] if sources else list(JOBS)
    for n in names:
        if n in JOBS:
            JOBS[n].stopped = True
    return {'stopping': names}


def status():
    out = []
    for n, src in SOURCES.items():
        m = meta(n)
        j = JOBS.get(n)
        running = bool(j and j.thread and j.thread.is_alive())
        count = len(rows(n)[0])
        out.append({**src.describe(), 'count': count, 'complete': bool(m.get('complete')),
                    'running': running, 'finished': m.get('finished'),
                    'started': m.get('started'), 'error': m.get('error'),
                    'pages': (j.pages if j else m.get('pages')),
                    'total': (j.total if j and j.total else m.get('total')),
                    'progress_rows': j.rows if j else None,
                    'log': j.log[-6:] if j else []})
    return {'sources': out, 'total': sum(s['count'] for s in out),
            'running': [s['name'] for s in out if s['running']],
            'dir': _dir()}


def resume():
    """On start: finish any crawl a restart interrupted, and refresh what is
    older than INFER_ZOO_TTL. A crawl the user stopped stays stopped."""
    _ensure_local()
    todo = []
    for n, src in SOURCES.items():
        m = meta(n)
        if m.get('stopped_by_user'):
            continue
        interrupted = m.get('complete') is False and m.get('state')
        stale = TTL > 0 and src.kind == 'remote' and (
            not m.get('finished') or time.time() - m['finished'] > TTL)
        if interrupted or stale:
            todo.append(n)
    if todo:
        scrape(','.join(todo))
    return todo


# ── planting ─────────────────────────────────────────────────────

def plant(key, file=None, name=None, weights=None):
    """Download (or build) one zoo model into the store."""
    import engine as E
    e = get(key)
    src = SOURCES[e['source']]
    files = e.get('files') or []
    if not files and hasattr(src, 'files'):
        files = src.files(e)
    path = file or default_file(files) or (files[0][0] if files else None)
    if not path and src.kind == 'remote':
        raise ZooError(f'{key} lists no .onnx file', 422, src.name)
    kw = {'weights': weights} if weights and src.name == 'torchvision' else {}
    t0 = time.time()
    data = src.fetch(e, path, max_bytes=E.MAX_BYTES, **kw)
    # The row's name, unless one row holds several graphs and this is not
    # the obvious one — then name/variant, so whisper's encoder and decoder
    # do not both land as "whisper-tiny".
    plain = (not path or src.kind != 'remote' or not is_onnx_path(path)
             or os.path.basename(path) in ('model.onnx', f"{e['name']}.onnx")
             or len([f for f in files if _plantable(f[0])]) <= 1)
    label = name or (e['name'] if plain else f"{e['name']}/{os.path.basename(path)[:-5]}")
    rec = E.store(data, name=label, source=f'zoo:{key}',
                  note=e.get('about') or e.get('task'))
    if rec.get('error'):
        E.delete(rec['id'])
        raise ZooError(f"{key} downloaded but will not parse — {rec['error']}", 422, src.name)
    reg = E.registry()
    reg[rec['id']].update({'zoo': key, 'zoo_file': path, 'zoo_url': e.get('url'),
                           'task': e.get('task'), 'domain': e.get('domain')})
    E._write_registry(reg)
    return {**reg[rec['id']], 'fetched_s': round(time.time() - t0, 2)}


PLANTS = {}               # id → {'done', 'failed', 'total', 'running'}


def plant_many(keys=None, source=None, q=None, domain=None, limit=50, max_bytes=None,
               wait=False):
    """Plant a list of keys, or everything a filter matches, in the background."""
    if not keys:
        hits = search(q=q, source=source, domain=domain, max_bytes=max_bytes,
                      sort='source', limit=int(limit), facets=False)['models']
        keys = [h['key'] for h in hits]
    elif isinstance(keys, str):
        keys = [k.strip() for k in keys.split(',') if k.strip()]
    pid = f'plant-{int(time.time() * 1000)}'
    st = {'id': pid, 'total': len(keys), 'done': [], 'failed': {}, 'running': True,
          'started': time.time()}
    PLANTS[pid] = st

    def go():
        for k in keys:
            try:
                r = plant(k)
                st['done'].append({'key': k, 'id': r['id'], 'name': r['name']})
            except Exception as ex:
                st['failed'][k] = getattr(ex, 'message', None) or f'{type(ex).__name__}: {ex}'
        st['running'] = False
        st['finished'] = time.time()
    if wait:
        go()
        return st
    threading.Thread(target=go, name=pid, daemon=True).start()
    return {'id': pid, 'total': len(keys), 'keys': keys[:50], 'running': True}


def plant_status(pid=None):
    if pid:
        if pid not in PLANTS:
            raise ZooError(f'no plant job {pid}', 404)
        return PLANTS[pid]
    return {'jobs': [{k: v for k, v in s.items() if k != 'done'} | {'n_done': len(s['done'])}
                     for s in PLANTS.values()]}


def examples(which=None):
    """Plant every builtin architecture (or the named ones) — synchronously,
    they are small and built in about a second each."""
    names = [w.strip() for w in str(which).split(',')] if which else list(ARCHS)
    out, failed = [], {}
    for n in names:
        try:
            out.append(plant(f'builtin:{n}'))
        except Exception as ex:
            failed[n] = getattr(ex, 'message', None) or f'{type(ex).__name__}: {ex}'
    return {'planted': [r['id'] for r in out], 'models': out, 'failed': failed,
            'count': len(out)}
