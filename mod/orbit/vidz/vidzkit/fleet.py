"""
Bridges to the fleet's other media tools — vidz renders the raw clips, the
rest of the suite finishes the film:

    artist      drag-drop timeline studio; `edit` pushes a cut there
    artlist     royalty-free songs / sfx / stock footage catalog
    musica      Bandcamp / SoundCloud / YouTube / archive.org audio crate
    sound2text  speech-to-text; `captions`
    voice       in-browser transcription app (a place to point humans at)

Each tool is a config.json "tools" entry {url, health, about}: plain HTTP to
a peer module on this machine, no SDKs. A slept peer refuses connections on
its own port forever — the activator only wakes a module for requests that
arrive through it — so on refusal we knock once on
{activator}/api/{tool}/health and retry. The proxy path on purpose, never
POST /_activator/control: control-wake silently clears a deliberate
`actl disable`. Tests set activator='' so fakes never wake the real fleet.
"""
import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

TIMEOUT = 60
UA = 'vidz/0.2 (mod orbit)'


def registry(config: dict) -> dict:
    return dict(config.get('tools') or {})


def base(config: dict, tool: str) -> str:
    url = (registry(config).get(tool) or {}).get('url')
    if not url:
        raise ValueError(f'unknown tool {tool!r}; have {sorted(registry(config))}')
    return url.rstrip('/')


def _http(url, body=None, raw=None, headers=None, timeout=TIMEOUT):
    data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
    req = urllib.request.Request(url, data=data)
    req.add_header('User-Agent', UA)
    if body is not None:
        req.add_header('Content-Type', 'application/json')
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = r.read()
            ct = r.headers.get('Content-Type') or ''
    except urllib.error.HTTPError as e:
        # Peers answer errors as 4xx JSON on purpose (Cloudflare strips 5xx bodies) — surface the body.
        payload, ct = e.read(), e.headers.get('Content-Type') or ''
        if 'json' not in ct:
            raise
    if 'json' not in ct:
        return payload
    out = json.loads(payload or b'null')
    # mod-serve and musica wrap replies as {"result": ...}; peers' own servers don't.
    if isinstance(out, dict) and set(out) == {'result'}:
        return out['result']
    return out


def _refused(e) -> bool:
    if isinstance(e, ConnectionRefusedError):
        return True
    s = str(getattr(e, 'reason', e)).lower()
    return isinstance(e, urllib.error.URLError) and ('refused' in s or 'errno 111' in s)


def _wake(config: dict, tool: str) -> bool:
    activator = config.get('activator', 'http://127.0.0.1:9000')
    if not activator:
        return False
    try:
        _http(f"{activator.rstrip('/')}/api/{tool}/health", timeout=30)
        return True
    except Exception:
        return False


def call(config, tool, path, body=None, raw=None, headers=None, timeout=TIMEOUT):
    url = base(config, tool) + path
    try:
        return _http(url, body, raw, headers, timeout)
    except Exception as e:
        if not _refused(e) or not _wake(config, tool):
            raise
        return _http(url, body, raw, headers, timeout)


def probe(config: dict) -> dict:
    """Every connected tool: up or down, right now. Down is a fact, not an error."""
    out = {}
    for name, t in registry(config).items():
        row = {'url': t.get('url'), 'about': t.get('about')}
        try:
            _http(t['url'].rstrip('/') + (t.get('health') or '/health'), timeout=4)
            row['up'] = True
        except Exception as e:
            row.update(up=False, error=str(e)[:120], hint=f'm {name}/serve')
        out[name] = row
    return out


# ── artist: the timeline editor ──────────────────────────────────

def artist_add(config, name: str, data: bytes) -> dict:
    """Upload raw bytes into artist's library; the id is a hash, so re-pushing dedupes."""
    return call(config, 'artist', '/artist/api/upload?name=' + urllib.parse.quote(name), raw=data)


def artist_save(config, name: str, timeline: dict, pid=None) -> dict:
    body = {'name': name, 'timeline': timeline}
    if pid:
        body['id'] = pid
    return call(config, 'artist', '/artist/api/save_project', body=body)


# ── artlist: royalty-free catalog ────────────────────────────────

def artlist(config, kind: str, q: str = '', k: int = 10, page: int = 1) -> dict:
    return call(config, 'artlist', f'/{kind}', body={'q': q, 'k': int(k), 'page': int(page)})


def artlist_download(url: str, out: Path) -> Path:
    """Preview off Artlist's CDN: 403 without the Referer, and only *.artlist.io may be fetched."""
    host = urllib.parse.urlparse(url).hostname or ''
    if host != 'artlist.io' and not host.endswith('.artlist.io'):
        raise ValueError(f'not an artlist host: {host!r}')
    data = _http(url, headers={'Referer': 'https://artlist.io/',
                               'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64)'}, timeout=180)
    out.write_bytes(data)
    return out


# ── musica: the five-platform crate ──────────────────────────────

def musica(config, fn: str, **kwargs) -> dict:
    return call(config, 'musica', f'/musica/api/{fn}', body=kwargs, timeout=120)


def musica_download(config, source: str, id, out: Path, track=None) -> dict:
    """Resolve a crate track to audio bytes; no-CORS platforms stream through musica's own proxy."""
    s = musica(config, 'stream', source=source, id=id, track=track)
    url = (s or {}).get('url') or (s or {}).get('stream')
    if not url:
        return {'error': (s or {}).get('error') or f'no stream for {source}:{id}'}
    if url.startswith('/'):
        url = base(config, 'musica') + url
    data = _http(url, timeout=300)
    out.write_bytes(data)
    return {'file': str(out), 'bytes': len(data)}


# ── sound2text: captions ─────────────────────────────────────────

def transcribe(config, path, engine: str = '', policy: str = 'fast') -> dict:
    q = {'path': str(path), 'policy': policy}
    if engine:
        q['engine'] = engine
    return call(config, 'sound2text', '/transcribe?' + urllib.parse.urlencode(q), timeout=900)
