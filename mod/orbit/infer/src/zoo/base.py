"""One shape for every place an ONNX model can be found.

`router/` does this for inference routers; this does it for model *files*.
An adapter maps one source's listing onto the same noun:

    Entry   one model you can plant — a repo, a directory, an architecture —
            with the .onnx files in it and whatever the source says about it

and knows how to turn one of those files into bytes `engine.store` accepts.
Everything else — HTTP, rate limits, retries, archives, external-data weights —
lives here, so an adapter stays a mapping file.

Three kinds of source, because "scrape" means three different things:

    local    already on this disk (the onnx package's own test models) —
             no network, ever
    build    constructed here (torch, torchvision, onnx.helper) — no network,
             the bytes are made on demand
    remote   a public registry — listed over HTTP, downloaded on plant

Rate limits are read, not guessed. HuggingFace answers every call with
`ratelimit: "api";r=<left>;t=<seconds to reset>`; a scraper that ignores it
gets its IP blocked for the rest of the window, which is exactly what happened
the first time this was tried. So the client slows down *before* it runs out,
and on a 429 sleeps until the reset the server named.
"""

import io
import json
import os
import re
import tarfile
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

USER_AGENT = 'mod-infer/0.3 (mod protocol; onnx zoo scraper)'
TIMEOUT = 60
MAX_TRIES = 12

# The domain vocabulary. Adapters map their task names into this, so
# `domain=audio` means the same thing whether the row came from a HuggingFace
# pipeline tag, a GitHub directory name or a ModelScope task.
DOMAINS = ('vision', 'text', 'audio', 'multimodal', 'tabular', 'graph',
           'generative', 'operator', 'other')

_TASK_DOMAIN = {
    'vision': ('image', 'object', 'depth', 'video', 'mask-generation', 'keypoint', 'ocr',
               'segmentation', 'detection', 'computer_vision', 'face', 'pose',
               'super-resolution', 'optical', 'classification-vision', 'cv'),
    'audio': ('audio', 'speech', 'voice', 'asr', 'tts', 'sound', 'vad',
              'speaker', 'music'),
    'text': ('text', 'token', 'fill-mask', 'question', 'translation',
             'summar', 'sentence', 'feature-extraction', 'nlp', 'natural_language',
             'zero-shot-classification', 'conversational', 'table-question'),
    'multimodal': ('any-to-any', 'image-text', 'visual-question', 'document',
                   'image-to-text', 'text-to-image', 'text-to-video',
                   'video-text', 'multimodal', 'image-text-to-text'),
    'tabular': ('tabular', 'time-series', 'regression', 'forecast'),
    'graph': ('graph',),
    'generative': ('generative', 'diffusion', 'gan', 'unconditional'),
    'operator': ('operator', 'node', 'onnx-test'),
}


def domain_of(*hints):
    """First vocabulary word any hint contains → its domain. Order matters:
    `image-text-to-text` must land in multimodal, not vision, so the
    multimodal check runs first."""
    text = ' '.join(str(h).lower() for h in hints if h)
    if not text:
        return 'other'
    # Short words match whole tokens only — `cv` is not in `cvt`, `gan` is
    # not in `organ` — longer ones match anywhere.
    words = set(re.split(r'[^a-z0-9]+', text))
    for dom in ('multimodal', 'operator', 'generative', 'graph', 'tabular',
                'audio', 'vision', 'text'):
        if any((w in words) if len(w) <= 4 else (w in text) for w in _TASK_DOMAIN[dom]):
            return dom
    return 'other'


class ZooError(Exception):
    def __init__(self, message, status=400, source=None):
        super().__init__(message)
        self.message, self.status, self.source = message, status, source

    def dict(self):
        return {'error': self.message, 'source': self.source}


# ── HTTP, rate-limit aware ───────────────────────────────────────

_hosts = {}                   # host → {'next': ts before which not to call}
_hosts_lock = threading.Lock()


def _polite_wait(host, job=None):
    with _hosts_lock:
        until = _hosts.get(host, {}).get('next', 0)
    delay = until - time.time()
    if delay > 0:
        if job:
            job.say(f'{host} asked for a pause — waiting {delay:.0f}s')
        _sleep(delay, job)


def _sleep(seconds, job=None):
    end = time.time() + seconds
    while time.time() < end:
        if job and job.stopped:
            raise ZooError('stopped', 499)
        time.sleep(min(1.0, max(0.0, end - time.time())))


def _note_limits(host, headers, status):
    """Read what the server said about its budget and schedule around it."""
    wait = 0.0
    rl = headers.get('ratelimit') or ''
    m_r, m_t = re.search(r'\br=(\d+)', rl), re.search(r'\bt=(\d+)', rl)
    if m_r and m_t and int(m_r.group(1)) <= 3:
        wait = int(m_t.group(1)) + 1
    ra = headers.get('retry-after')
    if status in (429, 503) and ra:
        try:
            wait = max(wait, float(ra))
        except ValueError:
            pass
    if status == 429 and not wait:
        wait = (int(m_t.group(1)) + 1) if m_t else 30
    if wait:
        with _hosts_lock:
            _hosts[host] = {'next': time.time() + wait}
    return wait


def http(url, method='GET', body=None, headers=None, job=None, raw=False,
         limit=None, timeout=TIMEOUT, tries=MAX_TRIES):
    """GET/PUT/POST with retries that respect the server's own rate limits.

    Returns parsed JSON, or (bytes, headers) when raw=True. `limit` caps how
    many bytes are read, so a model over INFER_MAX_BYTES fails fast rather
    than filling memory first.
    """
    host = urllib.parse.urlsplit(url).netloc
    hdrs = {'user-agent': USER_AGENT, 'accept': '*/*' if raw else 'application/json'}
    hdrs.update(headers or {})
    data = None
    if body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        hdrs.setdefault('content-type', 'application/json')
    last = None
    for attempt in range(tries):
        _polite_wait(host, job)
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                _note_limits(host, {k.lower(): v for k, v in r.headers.items()}, r.status)
                # r.read(-1) is NOT 'everything' in http.client — it returns
                # one 64 KiB chunk. Only a bare read() drains the body.
                blob = r.read(limit + 1) if limit else r.read()
                if limit and len(blob) > limit:
                    raise ZooError(f'{url} is over {limit:,} bytes', 413)
                if raw:
                    return blob, {k.lower(): v for k, v in r.headers.items()}
                return json.loads(blob or b'null')
        except urllib.error.HTTPError as e:
            h = {k.lower(): v for k, v in (e.headers or {}).items()}
            wait = _note_limits(host, h, e.code)
            last = f'HTTP {e.code} from {host}'
            if e.code in (429, 500, 502, 503, 504):
                if job:
                    job.say(f'{last} — retry {attempt + 1}/{tries}'
                            + (f' after {wait:.0f}s' if wait else ''))
                if not wait:
                    _sleep(min(60, 2 ** attempt), job)
                continue
            try:
                detail = e.read(300).decode('utf-8', 'replace')
            except Exception:
                detail = ''
            raise ZooError(f'{last}: {detail}'.strip(': '), e.code)
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            last = f'{type(e).__name__}: {e}'
            if job:
                job.say(f'{host} unreachable ({last}) — retry {attempt + 1}')
            _sleep(min(60, 2 ** attempt), job)
    raise ZooError(f'gave up on {host} after {tries} tries — {last}', 502)


def next_link(headers):
    m = re.search(r'<([^>]+)>;\s*rel="next"', headers.get('link') or '')
    return m.group(1) if m else None


# ── turning a download into one self-contained .onnx ─────────────

def is_onnx_path(p):
    p = p.lower()
    return p.endswith('.onnx')


def is_onnx_archive(p):
    """An .onnx shipped zipped — Qualcomm's HuggingFace repos do this, and
    HuggingFace does not tag them onnx, so `filter=onnx` never lists them."""
    return p.lower().endswith(('.onnx.zip', '.onnx.tar.gz', '.onnx.tgz', '.onnx.tar'))


def is_data_path(p):
    """External weight files: model.onnx_data, model.onnx.data, *.onnx.data_0 …"""
    p = p.lower()
    return ('.onnx_data' in p or '.onnx.data' in p
            or p.endswith(('.onnx.bin', '.onnx_weights')))


def companions(onnx_path, all_paths):
    """The external-data files a listing *suggests* belong to one .onnx —
    only for showing sizes. What is actually fetched is what the graph
    names (see `assemble`), because `model.onnx` and `model_fp16.onnx_data`
    share a prefix and not a model."""
    d, _, base = onnx_path.rpartition('/')
    d = d + '/' if d else ''
    return [p for p in all_paths
            if p != onnx_path and is_data_path(p) and p.startswith(d + base)]


def external_locations(onnx_bytes):
    """Relative paths of every external weight file the graph refers to."""
    import onnx
    from onnx.external_data_helper import _get_all_tensors
    m = onnx.load_from_string(onnx_bytes)
    locs = []
    for t in _get_all_tensors(m):
        if t.data_location == onnx.TensorProto.EXTERNAL:
            for kv in t.external_data:
                if kv.key == 'location' and kv.value not in locs:
                    locs.append(kv.value)
    return locs


def assemble(onnx_bytes, get, onnx_path='', max_bytes=None):
    """A graph → one self-contained file, pulling each external weight file
    it names through `get(relative_path) -> bytes`."""
    locs = external_locations(onnx_bytes)
    if not locs:
        return onnx_bytes
    d = onnx_path.rpartition('/')[0]
    extra = {loc: get(f'{d}/{loc}' if d else loc) for loc in locs}
    return inline(onnx_bytes, extra, max_bytes)


def inline(onnx_bytes, extra=None, max_bytes=None):
    """A graph plus its external weight files → one file with weights inline.

    The store is content-addressed on a single blob and the browser fetches a
    single URL, so a model whose weights live beside it has to be folded back
    together here. Protobuf caps a message at 2 GiB, so past that the honest
    answer is 'too big to be one file', not a truncated model.
    """
    if not extra:
        return onnx_bytes
    import onnx
    total = len(onnx_bytes) + sum(len(b) for b in extra.values())
    if max_bytes and total > max_bytes:
        raise ZooError(f'graph + external weights are {total:,} bytes — over '
                       f'INFER_MAX_BYTES', 413)
    if total >= 2 ** 31 - 1:
        raise ZooError('over 2 GiB with weights inline — protobuf cannot hold it '
                       'as one file', 413)
    tmp = tempfile.mkdtemp(prefix='infer-zoo-')
    try:
        main = os.path.join(tmp, 'model.onnx')
        with open(main, 'wb') as f:
            f.write(onnx_bytes)
        for rel, blob in extra.items():
            dst = os.path.join(tmp, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, 'wb') as f:
                f.write(blob)
        return onnx.load(main, load_external_data=True).SerializeToString()
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def unpack(blob, path, max_bytes=None, want=None):
    """An archive download → one self-contained .onnx from inside it."""
    members = from_archive(blob)
    onnx_files = [[p, len(b)] for p, b in members.items() if is_onnx_path(p)]
    if not onnx_files:
        raise ZooError(f'{path} holds no .onnx — only {list(members)[:6]}', 422)
    pick = want if want in members else default_file(onnx_files)
    by_name = {os.path.basename(k): v for k, v in members.items()}
    return assemble(members[pick], lambda p: members.get(p) or by_name[os.path.basename(p)],
                    onnx_path=pick, max_bytes=max_bytes)


def from_archive(blob, want=None):
    """tar/tar.gz/zip → {path: bytes} for every .onnx (and its data) inside."""
    out = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(blob), mode='r:*') as t:
            for m in t.getmembers():
                if m.isfile() and (is_onnx_path(m.name) or is_data_path(m.name)):
                    out[m.name] = t.extractfile(m).read()
        return out
    except tarfile.TarError:
        pass
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            for n in z.namelist():
                if is_onnx_path(n) or is_data_path(n):
                    out[n] = z.read(n)
        return out
    except zipfile.BadZipFile:
        pass
    raise ZooError('download is neither a .onnx, a tarball nor a zip')


# ── an entry, and a source ───────────────────────────────────────

def entry(source, ref, name=None, files=None, **kw):
    """The one row shape. `files` is [[path, bytes|None], …] — compact on
    purpose, because the HuggingFace listing alone runs to tens of thousands
    of rows and this is what sits in memory."""
    e = {'key': f'{source}:{ref}', 'source': source, 'ref': ref,
         'name': name or ref.rsplit('/', 1)[-1], 'files': files or []}
    for k, v in kw.items():
        if v not in (None, '', [], {}):
            e[k] = v
    if 'domain' not in e:
        e['domain'] = domain_of(e.get('task'), *(e.get('tags') or [])[:12])
    return e


def default_file(files):
    """Which file to plant when the caller does not say.

    Prefer the full-precision graph with the plainest name, because a zoo row
    with model.onnx, model_fp16.onnx, model_q4.onnx and model_quantized.onnx
    is one model in four encodings and the optimizer is the thing meant to
    make the smaller ones. Among equals, the smaller file wins.
    """
    onnx_files = [f for f in files if is_onnx_path(f[0])] or \
        [f for f in files if is_onnx_archive(f[0])]
    if not onnx_files:
        return None
    lossy = re.compile(r'(fp16|f16|int8|uint8|q4|q8|qint|quant|bnb4|_o[1-4]\b|'
                       r'_o[1-4]\.|arm64|avx|bf16|int4|q4f16)', re.I)

    def rank(f):
        p = f[0].lower()
        base = os.path.basename(p)
        for ext in ('.zip', '.tar.gz', '.tgz', '.tar'):
            if base.endswith(ext):
                base = base[:-len(ext)]
        return (bool(lossy.search(base)) or 'w8a' in base or 'precompiled' in base,
                base not in ('model.onnx', 'encoder_model.onnx', 'decoder_model.onnx'),
                p.count('/'),
                f[1] if isinstance(f[1], int) else 1 << 40,
                p)
    return sorted(onnx_files, key=rank)[0][0]


class Source:
    name = ''
    title = ''
    kind = 'remote'          # local | build | remote
    home = ''
    note = ''
    # How the scrape is sized, for the status line before it has run.
    expect = None

    def describe(self):
        return {'name': self.name, 'title': self.title, 'kind': self.kind,
                'home': self.home, 'note': self.note, 'expect': self.expect}

    # A scrape yields pages: (entries, state, done, total). `state` is
    # persisted after every page, so a restart resumes where it stopped
    # rather than beginning the crawl again.
    def scrape(self, job, state):
        raise NotImplementedError

    def files(self, e, job=None):
        """The file list, sizes included where the source will say. Remote
        sources that list lazily override this."""
        return e.get('files') or []

    def fetch(self, e, path, job=None, max_bytes=None):
        """→ bytes of one self-contained .onnx"""
        raise NotImplementedError
