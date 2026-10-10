"""Transport: POST GraphQL to search-api.artlist.io, with a TTL cache.

The endpoint is keyless for search/metadata but validates like a browser
client: the UA and Origin headers below are required (verified 2026-10-05 —
without them artlist.io's edge returns 403). Introspection is disabled
upstream; the schema in api.py was derived from validation-error leaks and
each query there is verified against the live endpoint.

An optional bearer token (~/.mod/artlist/token or $ARTLIST_TOKEN) is attached
when present, for account-scoped queries via gql(). It never lives in
config.json or any on-chain/store-published artifact.
"""
import hashlib
import json
import os
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ENDPOINT = 'https://search-api.artlist.io/v1/graphql'
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36',
    'Content-Type': 'application/json',
    'Origin': 'https://artlist.io',
    'Referer': 'https://artlist.io/',
}
STATE_DIR = Path(os.environ.get('ARTLIST_STATE', Path.home() / '.mod' / 'artlist'))
CACHE_TTL = 15 * 60  # search results move slowly; 15 min keeps repeat agent calls free
TIMEOUT = 25


class ArtlistError(RuntimeError):
    """Upstream returned GraphQL errors or a non-200."""


def jstr(s) -> str:
    """A safely-escaped GraphQL string literal, for args whose exact variable type is unconfirmed."""
    return json.dumps(str(s or ''))


def _token() -> str:
    tok = os.environ.get('ARTLIST_TOKEN', '')
    f = STATE_DIR / 'token'
    if not tok and f.exists():
        tok = f.read_text().strip()
    return tok


def _db() -> sqlite3.Connection:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(STATE_DIR / 'cache.db')
    conn.execute('CREATE TABLE IF NOT EXISTS cache (k TEXT PRIMARY KEY, ts REAL, body TEXT)')
    return conn


def cache_stats() -> dict:
    with _db() as c:
        n, = c.execute('SELECT COUNT(*) FROM cache').fetchone()
    return {'entries': n, 'db': str(STATE_DIR / 'cache.db'), 'ttl_s': CACHE_TTL}


def clear_cache() -> dict:
    with _db() as c:
        c.execute('DELETE FROM cache')
    return {'cleared': True}


def gql(query: str, variables: dict | None = None, cache: bool = True, auth: bool = False) -> dict:
    """Run one GraphQL document. Returns the `data` dict; raises ArtlistError on errors."""
    payload = json.dumps({'query': query, 'variables': variables or {}}).encode()
    key = hashlib.sha256(payload).hexdigest()
    if cache and not auth:
        with _db() as c:
            row = c.execute('SELECT ts, body FROM cache WHERE k=?', (key,)).fetchone()
        if row and time.time() - row[0] < CACHE_TTL:
            return json.loads(row[1])

    headers = dict(HEADERS)
    if auth:
        tok = _token()
        if not tok:
            raise ArtlistError('no Artlist token: put one in ~/.mod/artlist/token or $ARTLIST_TOKEN')
        headers['Authorization'] = f'Bearer {tok}'

    req = urllib.request.Request(ENDPOINT, data=payload, headers=headers, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            body = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise ArtlistError(f'HTTP {e.code} from search-api.artlist.io: {e.read()[:300]!r}') from e

    if body.get('errors'):
        raise ArtlistError('; '.join(e.get('message', '?') for e in body['errors'])[:500])
    data = body.get('data') or {}
    if cache and not auth:
        with _db() as c:
            c.execute('REPLACE INTO cache (k, ts, body) VALUES (?,?,?)', (key, time.time(), json.dumps(data)))
    return data


def fetch_preview(url: str, dest: Path) -> Path:
    """Download a CDN preview file. Only *.artlist.io hosts — the SSRF guard."""
    host = urllib.parse.urlparse(url).hostname or ''
    if not (host == 'artlist.io' or host.endswith('.artlist.io')):
        raise ArtlistError(f'refusing non-artlist host: {host}')
    dest.parent.mkdir(parents=True, exist_ok=True)
    # The CDN 403s without a Referer, then 302s to the real file on the same host.
    req = urllib.request.Request(url, headers={'User-Agent': HEADERS['User-Agent'],
                                               'Referer': 'https://artlist.io/'})
    with urllib.request.urlopen(req, timeout=60) as r, open(dest, 'wb') as f:
        while chunk := r.read(1 << 16):
            f.write(chunk)
    return dest
