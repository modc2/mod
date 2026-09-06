"""platforms — Bandcamp and SoundCloud, without an API key.

Neither platform hands out API keys any more, so this file talks to the same
endpoints their own web players use. Everything here is best-effort and says
so when it fails; the decks and the studio never depend on it.

Bandcamp
  * search:    POST bandcamp.com/api/bcsearch_public_api/1/autocomplete_elastic
  * discover:  POST bandcamp.com/api/discover/1/discover_web
  * pages:     every album/track page carries a ``data-tralbum`` JSON blob with
               a 128k MP3 stream URL per track — that is the stream the site's
               own player uses, signed and short-lived
  * the catch: bandcamp.com fronts datacenter IPs with a JavaScript challenge.
               A plain request gets a 3KB "enable JavaScript" page. If
               Playwright's Chromium is installed, :func:`bc_warm` clears it
               once in a headless tab and the cookies are reused afterwards.
               Without a browser the error explains that, rather than
               pretending Bandcamp has no results.
  * streams have no CORS header, so serve.py proxies them for the deck.

SoundCloud
  * api-v2.soundcloud.com, with the public client_id the web player embeds in
    its own JavaScript — scraped once and cached in ``~/.mod/musica``. Set
    ``SOUNDCLOUD_CLIENT_ID`` (or ``soundcloud_client_id`` in keys.json) to
    skip the scrape.
  * search tracks / playlists / users, resolve any soundcloud.com URL, and
    turn a track into its progressive MP3 — a signed CDN URL that does carry
    CORS, so the browser can fetch that one directly.

Every result is flattened to one row shape, whatever the source::

    {source, kind, id, name, artists, album, duration_ms, art, url,
     embed, streamable, ...}

so the console renders Bandcamp, SoundCloud and Spotify rows with one
function. ``streamable`` is the honest bit: Spotify rows are metadata only.
"""

import html
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, quote

import requests

STATE_DIR = Path.home() / '.mod' / 'musica'
KEYS_FILE = STATE_DIR / 'keys.json'
BC_COOKIES = STATE_DIR / 'bandcamp_cookies.json'
SC_CACHE = STATE_DIR / 'soundcloud.json'

UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36')
TIMEOUT = 20


class PlatformError(RuntimeError):
    pass


def _keys() -> dict:
    try:
        return json.loads(KEYS_FILE.read_text()) or {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write(path: Path, data) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(data, indent=2))
    tmp.chmod(0o600)
    tmp.replace(path)


def _ms(seconds) -> Optional[int]:
    try:
        return int(round(float(seconds) * 1000))
    except (TypeError, ValueError):
        return None


# ═══════════════════════════════════════════════════════════════════════════
# Bandcamp
# ═══════════════════════════════════════════════════════════════════════════

BC = 'https://bandcamp.com'
BC_SEARCH = BC + '/api/bcsearch_public_api/1/autocomplete_elastic'
BC_DISCOVER = BC + '/api/discover/1/discover_web'
BC_KINDS = {'all': '', 'track': 't', 'album': 'a', 'artist': 'b', 'playlist': 'a'}

_bc = {'session': None, 'warmed_at': 0.0, 'lock': threading.Lock(),
       'last_error': None}


def _bc_session() -> requests.Session:
    if _bc['session'] is None:
        s = requests.Session()
        s.headers.update({'User-Agent': UA, 'Accept-Language': 'en-US,en;q=0.9'})
        try:
            for c in json.loads(BC_COOKIES.read_text()):
                s.cookies.set(c['name'], c['value'],
                              domain=c.get('domain', '.bandcamp.com').lstrip('.'),
                              path=c.get('path', '/'))
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            pass
        _bc['session'] = s
    return _bc['session']


def _is_challenge(text: str) -> bool:
    head = text[:4000]
    return '_fs-ch-' in head or 'Please enable JavaScript to proceed' in head


def browser_available() -> bool:
    try:
        import playwright.sync_api  # noqa: F401
        return True
    except ImportError:
        return False


def bc_warm(force=False) -> dict:
    """Clear Bandcamp's JavaScript challenge in a headless tab, once.

    The cookies it leaves are copied into the requests session and saved to
    ``~/.mod/musica/bandcamp_cookies.json``. Idempotent within ten minutes so a
    burst of failing requests does not launch a browser each.
    """
    with _bc['lock']:
        if not force and time.time() - _bc['warmed_at'] < 600:
            return {'ok': False, 'skipped': 'warmed recently',
                    'error': _bc['last_error']}
        _bc['warmed_at'] = time.time()
        if not browser_available():
            _bc['last_error'] = ('Bandcamp is challenging this server with a '
                                 'JavaScript check and no headless browser is '
                                 'installed to pass it (pip install playwright '
                                 '&& playwright install chromium)')
            return {'ok': False, 'error': _bc['last_error']}
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                b = p.chromium.launch(headless=True, args=['--no-sandbox'])
                ctx = b.new_context(user_agent=UA)
                pg = ctx.new_page()
                pg.goto(BC + '/search?q=music&item_type=t',
                        wait_until='domcontentloaded', timeout=45000)
                for _ in range(30):
                    if 'searchresult' in pg.content():
                        break
                    time.sleep(1)
                cookies = ctx.cookies()
                b.close()
        except Exception as e:                       # noqa: BLE001
            _bc['last_error'] = f'browser warm-up failed: {type(e).__name__}: {e}'
            return {'ok': False, 'error': _bc['last_error']}
        s = _bc_session()
        for c in cookies:
            s.cookies.set(c['name'], c['value'],
                          domain=c['domain'].lstrip('.'), path=c.get('path', '/'))
        _write(BC_COOKIES, cookies)
        _bc['last_error'] = None
        return {'ok': True, 'cookies': len(cookies)}


def bc_fetch(url: str, method='GET', **kw) -> requests.Response:
    """A Bandcamp request that clears the challenge page if it meets one."""
    s = _bc_session()
    kw.setdefault('timeout', TIMEOUT)
    r = s.request(method, url, **kw)
    ctype = r.headers.get('content-type', '')
    if 'text/html' in ctype and _is_challenge(r.text):
        warm = bc_warm()
        if not warm.get('ok') and warm.get('error'):
            raise PlatformError(warm['error'])
        r = s.request(method, url, **kw)
        if 'text/html' in r.headers.get('content-type', '') and _is_challenge(r.text):
            raise PlatformError('Bandcamp is challenging this server with a '
                                'JavaScript check that the warm-up did not clear')
    if r.status_code == 404:
        raise PlatformError(f'Bandcamp: {url} was not found')
    if r.status_code >= 400:
        raise PlatformError(f'Bandcamp answered {r.status_code} for {url}')
    return r


def _bc_art(art_id, size=7) -> Optional[str]:
    """Album/track art: the ``a`` prefix is required; _7 is 160px, _16 is 700px.
    (The search endpoint's own ``img`` field omits the prefix and 404s.)"""
    return f'https://f4.bcbits.com/img/a{art_id}_{size}.jpg' if art_id else None


def _bc_embed(album_id=None, track_id=None) -> Optional[str]:
    if not (album_id or track_id):
        return None
    parts = ['https://bandcamp.com/EmbeddedPlayer']
    if album_id:
        parts.append(f'album={album_id}')
    parts += ['size=large', 'bgcol=111214', 'linkcol=c8ff2e', 'artwork=small',
              'transparent=true']
    if track_id:
        parts.append(f'track={track_id}')
    if not album_id:
        parts.append('tracklist=false')
    return '/'.join(parts) + '/'


def _bc_row_from_search(x: dict) -> dict:
    t = x.get('type')
    kind = {'t': 'track', 'a': 'album', 'b': 'artist', 'f': 'artist'}.get(t, 'album')
    url = x.get('item_url_path') or x.get('item_url_root')
    row = {
        'source': 'bandcamp', 'kind': kind, 'id': url, 'bc_id': x.get('id'),
        'name': x.get('name'), 'artists': x.get('band_name') or '',
        'album': x.get('album_name'), 'art': _bc_art(x.get('art_id')) or x.get('img'),
        'url': url, 'streamable': kind in ('track', 'album'),
        'tags': x.get('tag_names') or [], 'location': x.get('location'),
    }
    if kind == 'artist':
        row['artists'] = x.get('name')
        row['name'] = x.get('name')
        row['streamable'] = False
    elif kind == 'track':
        row['embed'] = _bc_embed(track_id=x.get('id'))
    else:
        row['embed'] = _bc_embed(album_id=x.get('id'))
    return row


def bc_search(q: str, kind='all', limit=20) -> dict:
    """Search Bandcamp's autocomplete index — bands, albums and tracks."""
    kind = str(kind or 'all').lower()
    if kind not in BC_KINDS:
        raise PlatformError(f'kind must be one of {", ".join(BC_KINDS)}')
    body = {'search_text': q, 'search_filter': BC_KINDS[kind],
            'full_page': True, 'fan_id': None}
    r = bc_fetch(BC_SEARCH, 'POST', json=body)
    try:
        results = r.json().get('auto', {}).get('results') or []
    except ValueError:
        raise PlatformError('Bandcamp search did not answer with JSON')
    rows = [_bc_row_from_search(x) for x in results][:max(1, min(int(limit), 60))]
    return {'source': 'bandcamp', 'kind': kind, 'query': q,
            'count': len(rows), 'items': rows}


def bc_discover(tag='electronic', slice_='top', size=24) -> dict:
    """Bandcamp's discover feed for one tag: top, new or rec."""
    body = {'tag_norm_names': [str(tag).lower().replace(' ', '-')],
            'geoname_id': 0, 'slice': slice_, 'time_facet_id': None,
            'include_result_types': ['a', 's'],
            'size': max(1, min(int(size), 60)), 'cursor': '*'}
    r = bc_fetch(BC_DISCOVER, 'POST', json=body)
    rows = []
    for x in r.json().get('results') or []:
        img = (x.get('primary_image') or {}).get('image_id')
        band = x.get('band_name') or (x.get('band') or {}).get('name') or ''
        rows.append({
            'source': 'bandcamp', 'kind': 'album', 'id': x.get('item_url'),
            'bc_id': x.get('item_id'), 'name': x.get('title'), 'artists': band,
            'art': _bc_art(img) if img else None, 'url': x.get('item_url'),
            'embed': _bc_embed(album_id=x.get('item_id')), 'streamable': True,
            'release': x.get('release_date'), 'genre': x.get('genre'),
        })
    return {'source': 'bandcamp', 'tag': tag, 'slice': slice_,
            'count': len(rows), 'items': rows}


def _tralbum(url: str) -> dict:
    r = bc_fetch(url)
    m = re.search(r'data-tralbum="([^"]*)"', r.text)
    if not m:
        raise PlatformError(f'{url} is not a Bandcamp album or track page')
    d = json.loads(html.unescape(m.group(1)))
    b = re.search(r'data-band="([^"]*)"', r.text)
    d['_band'] = json.loads(html.unescape(b.group(1))) if b else {}
    return d


def bc_page(url: str) -> dict:
    """One album or track page, with every track and whether it streams."""
    d = _tralbum(url)
    cur = d.get('current') or {}
    kind = 'album' if d.get('item_type') == 'album' else 'track'
    album_id = d.get('id') if kind == 'album' else cur.get('album_id')
    art = _bc_art(d.get('art_id'))
    base = urlparse(d.get('url') or url)
    root = f'{base.scheme}://{base.netloc}'
    tracks = []
    for t in d.get('trackinfo') or []:
        f = t.get('file') or {}
        link = t.get('title_link')
        turl = (root + link) if link else (d.get('url') or url)
        tracks.append({
            'source': 'bandcamp', 'kind': 'track', 'id': turl,
            'bc_id': t.get('track_id'), 'album_id': album_id,
            'name': t.get('title'), 'artists': t.get('artist') or d.get('artist'),
            'album': cur.get('title') if kind == 'album' else cur.get('album_title'),
            'num': t.get('track_num'), 'duration_ms': _ms(t.get('duration')),
            'art': art, 'url': turl, 'streamable': bool(f.get('mp3-128')),
            'embed': _bc_embed(album_id=album_id, track_id=t.get('track_id')),
        })
    return {
        'source': 'bandcamp', 'kind': kind, 'id': d.get('url') or url,
        'bc_id': d.get('id'), 'name': cur.get('title'), 'artists': d.get('artist'),
        'label': (d.get('_band') or {}).get('name'), 'art': art,
        'release': cur.get('release_date'), 'url': d.get('url') or url,
        'embed': _bc_embed(album_id=album_id, track_id=d.get('id') if kind == 'track' else None),
        'streamable': any(t['streamable'] for t in tracks),
        'count': len(tracks), 'items': tracks,
    }


def bc_stream(url: str, track_id=None) -> dict:
    """The MP3 behind one Bandcamp track — a fresh signed URL each time.

    ``url`` is a track page, or an album page plus ``track_id``. The URL is
    good for a few hours and has no CORS header, so the console pulls it
    through serve.py's ``/stream`` proxy rather than fetching it itself.
    """
    d = _tralbum(url)
    tracks = d.get('trackinfo') or []
    pick = None
    if track_id:
        pick = next((t for t in tracks if str(t.get('track_id')) == str(track_id)), None)
    elif d.get('item_type') == 'track':
        pick = tracks[0] if tracks else None
    else:
        pick = next((t for t in tracks if (t.get('file') or {}).get('mp3-128')), None)
    if not pick:
        raise PlatformError('that track is not on this page')
    mp3 = (pick.get('file') or {}).get('mp3-128')
    if not mp3:
        raise PlatformError(f'"{pick.get("title")}" is not streamable on Bandcamp')
    return {
        'source': 'bandcamp', 'id': url, 'bc_id': pick.get('track_id'),
        'name': pick.get('title'), 'artists': pick.get('artist') or d.get('artist'),
        'duration_ms': _ms(pick.get('duration')), 'art': _bc_art(d.get('art_id')),
        'url': mp3, 'direct': False, 'format': 'mp3-128',
        'referer': d.get('url') or url,
    }


def bc_status() -> dict:
    return {
        'source': 'bandcamp', 'configured': True, 'auth': 'none needed',
        'browser': browser_available(),
        'cookies': BC_COOKIES.exists(),
        'last_error': _bc['last_error'],
        'streams': 'mp3-128, the site player\'s own stream, proxied by the module',
    }


# ═══════════════════════════════════════════════════════════════════════════
# SoundCloud
# ═══════════════════════════════════════════════════════════════════════════

SC = 'https://api-v2.soundcloud.com'
SC_KINDS = {'track': 'tracks', 'playlist': 'playlists', 'artist': 'users',
            'album': 'albums', 'all': ''}

_sc = {'client_id': None, 'scraped_at': 0.0, 'lock': threading.Lock()}
_sc_http = requests.Session()
_sc_http.headers.update({'User-Agent': UA})


def _sc_scrape_client_id() -> Optional[str]:
    """Pull the web player's client_id out of its own asset bundles."""
    r = _sc_http.get('https://soundcloud.com/', timeout=TIMEOUT)
    assets = re.findall(r'https://a-v2\.sndcdn\.com/assets/[^"\']+\.js', r.text)
    for url in reversed(assets):                 # the id lives in the last few
        try:
            js = _sc_http.get(url, timeout=TIMEOUT).text
        except requests.RequestException:
            continue
        m = re.search(r'client_id\s*:\s*"([A-Za-z0-9]{32})"', js)
        if m:
            return m.group(1)
    return None


def sc_client_id(force=False) -> str:
    """Env, keys.json, the cache file, then the scrape — in that order."""
    cid = os.environ.get('SOUNDCLOUD_CLIENT_ID') or _keys().get('soundcloud_client_id')
    if cid:
        return cid.strip()
    with _sc['lock']:
        if _sc['client_id'] and not force:
            return _sc['client_id']
        if not force:
            try:
                cached = json.loads(SC_CACHE.read_text())
                if cached.get('client_id'):
                    _sc['client_id'] = cached['client_id']
                    return _sc['client_id']
            except (OSError, json.JSONDecodeError):
                pass
        cid = _sc_scrape_client_id()
        if not cid:
            raise PlatformError('could not find SoundCloud\'s web client_id — set '
                                'SOUNDCLOUD_CLIENT_ID or soundcloud_client_id in '
                                f'{KEYS_FILE}')
        _sc['client_id'] = cid
        _sc['scraped_at'] = time.time()
        _write(SC_CACHE, {'client_id': cid, 'scraped_at': int(time.time())})
        return cid


def sc_get(path: str, _retry=True, **params) -> dict:
    url = path if path.startswith('http') else f'{SC}/{path.lstrip("/")}'
    params = {k: v for k, v in params.items() if v is not None}
    params['client_id'] = sc_client_id()
    r = _sc_http.get(url, params=params, timeout=TIMEOUT)
    if r.status_code in (401, 403) and _retry:
        # A rotated client_id looks exactly like a bad one; re-scrape once.
        sc_client_id(force=True)
        return sc_get(path, _retry=False, **params)
    if r.status_code == 404:
        raise PlatformError('SoundCloud: not found')
    if r.status_code == 429:
        raise PlatformError('SoundCloud rate-limited this server — try again in a minute')
    if r.status_code >= 400:
        raise PlatformError(f'SoundCloud answered {r.status_code}: {r.text[:160]}')
    try:
        return r.json()
    except ValueError:
        raise PlatformError('SoundCloud did not answer with JSON')


def _sc_art(url: Optional[str], size='t300x300') -> Optional[str]:
    return url.replace('-large.', f'-{size}.') if url else None


def _sc_embed(permalink: str, kind='track') -> Optional[str]:
    if not permalink:
        return None
    return ('https://w.soundcloud.com/player/?url=' + quote(permalink, safe='')
            + '&color=%23c8ff2e&auto_play=false&show_comments=false'
            + '&show_user=true&visual=false&hide_related=true')


def _sc_track(t: dict) -> dict:
    media = (t.get('media') or {}).get('transcodings') or []
    progressive = any(m.get('format', {}).get('protocol') == 'progressive' for m in media)
    return {
        'source': 'soundcloud', 'kind': 'track', 'id': t.get('id'),
        'name': t.get('title'), 'artists': (t.get('user') or {}).get('username') or '',
        'album': None, 'duration_ms': t.get('duration'),
        'art': _sc_art(t.get('artwork_url') or (t.get('user') or {}).get('avatar_url')),
        'url': t.get('permalink_url'), 'embed': _sc_embed(t.get('permalink_url')),
        'streamable': bool(t.get('streamable', True)) and (progressive or not media),
        'genre': t.get('genre'), 'bpm': t.get('bpm'), 'plays': t.get('playback_count'),
        'likes': t.get('likes_count'), 'release': (t.get('created_at') or '')[:10],
        'downloadable': bool(t.get('downloadable')),
    }


def _sc_playlist(p: dict) -> dict:
    return {
        'source': 'soundcloud', 'kind': 'album' if p.get('is_album') else 'playlist',
        'id': p.get('id'), 'name': p.get('title'),
        'artists': (p.get('user') or {}).get('username') or '',
        'tracks': p.get('track_count'), 'duration_ms': p.get('duration'),
        'art': _sc_art(p.get('artwork_url') or (p.get('user') or {}).get('avatar_url')),
        'url': p.get('permalink_url'), 'embed': _sc_embed(p.get('permalink_url')),
        'streamable': True, 'genre': p.get('genre'),
    }


def _sc_user(u: dict) -> dict:
    return {
        'source': 'soundcloud', 'kind': 'artist', 'id': u.get('id'),
        'name': u.get('username'), 'artists': u.get('username'),
        'followers': u.get('followers_count'), 'tracks': u.get('track_count'),
        'art': _sc_art(u.get('avatar_url')), 'url': u.get('permalink_url'),
        'city': u.get('city'), 'country': u.get('country_code'),
        'streamable': False,
    }


def _sc_row(x: dict) -> Optional[dict]:
    k = x.get('kind')
    if k == 'track':
        return _sc_track(x)
    if k == 'playlist':
        return _sc_playlist(x)
    if k == 'user':
        return _sc_user(x)
    return None


def sc_search(q: str, kind='track', limit=20) -> dict:
    kind = str(kind or 'track').lower()
    if kind not in SC_KINDS:
        raise PlatformError(f'kind must be one of {", ".join(SC_KINDS)}')
    path = 'search' if kind == 'all' else f'search/{SC_KINDS[kind]}'
    body = sc_get(path, q=q, limit=max(1, min(int(limit), 50)))
    rows = [r for r in (_sc_row(x) for x in body.get('collection') or []) if r]
    return {'source': 'soundcloud', 'kind': kind, 'query': q,
            'total': body.get('total_results'), 'count': len(rows), 'items': rows}


def sc_resolve(url: str) -> dict:
    """Any soundcloud.com URL → the track, playlist or user it names."""
    x = sc_get('resolve', url=url)
    row = _sc_row(x)
    if not row:
        raise PlatformError(f'SoundCloud: {url} is a {x.get("kind")}, not something musica can use')
    if row['kind'] in ('playlist', 'album'):
        return sc_playlist(row['id'], _prefetched=x)
    return row


def sc_track(track_id) -> dict:
    return _sc_track(sc_get(f'tracks/{int(track_id)}'))


def sc_playlist(playlist_id, limit=200, _prefetched=None) -> dict:
    """A playlist with its tracks filled in.

    The playlist body carries full objects for the first few tracks and bare
    ``{id}`` stubs for the rest; the stubs are hydrated in batches of 50.
    """
    p = _prefetched or sc_get(f'playlists/{int(playlist_id)}')
    tracks = (p.get('tracks') or [])[:limit]
    stubs = [t['id'] for t in tracks if not t.get('title')]
    full = {t['id']: t for t in tracks if t.get('title')}
    for i in range(0, len(stubs), 50):
        batch = stubs[i:i + 50]
        for t in sc_get('tracks', ids=','.join(str(s) for s in batch)):
            full[t['id']] = t
    rows = [_sc_track(full[t['id']]) for t in tracks if t['id'] in full]
    out = _sc_playlist(p)
    out.update({'count': len(rows), 'items': rows})
    return out


def sc_user_tracks(user_id, limit=50) -> dict:
    body = sc_get(f'users/{int(user_id)}/tracks', limit=max(1, min(int(limit), 200)))
    rows = [_sc_track(t) for t in body.get('collection') or []]
    return {'source': 'soundcloud', 'kind': 'track', 'count': len(rows), 'items': rows}


def sc_stream(track_id) -> dict:
    """A track's progressive MP3 — a signed CDN URL the browser can fetch.

    The CDN answers with ``Access-Control-Allow-Origin: *``, so the console
    downloads it straight into Web Audio; the proxy is only the fallback.
    """
    t = sc_get(f'tracks/{int(track_id)}')
    media = (t.get('media') or {}).get('transcodings') or []
    prog = next((m for m in media if m.get('format', {}).get('protocol') == 'progressive'), None)
    if not prog:
        raise PlatformError(f'"{t.get("title")}" has no progressive stream on SoundCloud '
                            '(HLS-only tracks cannot be decoded in one piece)')
    body = sc_get(prog['url'])
    url = body.get('url')
    if not url:
        raise PlatformError('SoundCloud did not hand back a stream URL')
    return {
        'source': 'soundcloud', 'id': t.get('id'), 'name': t.get('title'),
        'artists': (t.get('user') or {}).get('username'),
        'duration_ms': t.get('duration'), 'art': _sc_art(t.get('artwork_url')),
        'url': url, 'direct': True, 'format': prog.get('preset') or 'mp3',
    }


def sc_status() -> dict:
    keyed = bool(os.environ.get('SOUNDCLOUD_CLIENT_ID') or _keys().get('soundcloud_client_id'))
    cached = None
    try:
        cached = json.loads(SC_CACHE.read_text()).get('client_id')
    except (OSError, json.JSONDecodeError):
        pass
    cid = _sc['client_id'] or cached
    return {
        'source': 'soundcloud', 'configured': True,
        'auth': 'your own client_id' if keyed else 'web player client_id, scraped and cached',
        'client_id': (cid[:4] + '…' + cid[-4:]) if cid else None,
        'streams': 'progressive MP3, fetched by the browser (CORS open)',
    }


# ═══════════════════════════════════════════════════════════════════════════
# YouTube
# ═══════════════════════════════════════════════════════════════════════════
#
# yt-dlp does the work: it is the only thing that keeps up with YouTube's
# player, and it is an ordinary import here rather than a subprocess so the
# extracted metadata comes back as objects. Audio comes out as one progressive
# format — m4a first because every browser decodes AAC, opus/webm after — and
# googlevideo sends NO CORS header, so the URL is handed to serve.py's proxy
# exactly the way Bandcamp's is. A stream URL is signed and expires (the
# ``expire`` query parameter says when), so resolved URLs are cached until a
# minute before that and re-extracted after.

YT_STATE = {'lock': threading.Lock(), 'streams': {}, 'last_error': None}
YT_COOKIES = STATE_DIR / 'youtube_cookies.txt'
YT_KINDS = {'all': 'video', 'track': 'video', 'video': 'video',
            'album': 'playlist', 'playlist': 'playlist', 'artist': 'channel',
            'channel': 'channel'}
# The result-page filters YouTube itself uses for "playlists" and "channels";
# ytsearch: only ever returns videos.
YT_FILTERS = {'playlist': 'EgIQAw%3D%3D', 'channel': 'EgIQAg%3D%3D'}


def yt_available() -> bool:
    try:
        import yt_dlp  # noqa: F401
        return True
    except ImportError:
        return False


def _yt_dlp(**overrides):
    try:
        import yt_dlp
    except ImportError:
        raise PlatformError(
            'YouTube needs yt-dlp on this box — pip install yt-dlp '
            '(nothing else in musica depends on it)')
    opts = {
        'quiet': True, 'no_warnings': True, 'skip_download': True,
        'noplaylist': True, 'socket_timeout': 20, 'retries': 2,
        # only_download: a deleted video inside a playlist is skipped, but a
        # playlist that does not exist still says so instead of coming back
        # as an empty list.
        'ignoreerrors': 'only_download', 'cachedir': str(STATE_DIR / 'ytcache'),
        # YouTube's player is JavaScript; without a runtime some formats
        # silently disappear. deno is upstream's default, node is what is
        # actually installed here — offering both costs nothing.
        'js_runtimes': {'node': {}, 'deno': {}},
    }
    if YT_COOKIES.exists():                    # optional, for age-gated tracks
        opts['cookiefile'] = str(YT_COOKIES)
    opts.update(overrides)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    return yt_dlp.YoutubeDL(opts)


def _yt_extract(target: str, **overrides) -> dict:
    """One yt-dlp extraction, with its errors turned into PlatformError."""
    import yt_dlp
    try:
        with _yt_dlp(**overrides) as y:
            info = y.extract_info(target, download=False)
    except yt_dlp.utils.DownloadError as e:
        msg = str(e).replace('ERROR: ', '').strip()
        YT_STATE['last_error'] = msg
        if 'confirm you' in msg or 'bot' in msg.lower():
            raise PlatformError(
                'YouTube is asking this IP to prove it is not a bot. Export '
                f'browser cookies to {YT_COOKIES} (Netscape format) and they '
                'will be used automatically.')
        raise PlatformError(f'YouTube: {msg}')
    except Exception as e:                                      # noqa: BLE001
        YT_STATE['last_error'] = f'{type(e).__name__}: {e}'
        raise PlatformError(f'YouTube: {type(e).__name__}: {e}')
    if not info:
        raise PlatformError(f'YouTube returned nothing for {target}')
    YT_STATE['last_error'] = None
    return info


def _yt_art(vid, thumbs=None) -> Optional[str]:
    for t in sorted(thumbs or [], key=lambda t: -(t.get('preference') or 0)):
        if t.get('url') and 'hqdefault' in t['url']:
            return t['url']
    return f'https://i.ytimg.com/vi/{vid}/hqdefault.jpg' if vid else None


def _yt_embed(vid=None, playlist=None) -> Optional[str]:
    if playlist:
        return f'https://www.youtube.com/embed/videoseries?list={playlist}'
    return f'https://www.youtube.com/embed/{vid}?rel=0' if vid else None


def _yt_video_row(e: dict) -> dict:
    """One video, flat entry or full info — the crate's row shape either way."""
    vid = e.get('id')
    live = e.get('is_live') or e.get('live_status') in ('is_live', 'is_upcoming')
    return {
        'source': 'youtube', 'kind': 'track', 'id': vid,
        'name': e.get('title'),
        'artists': (e.get('artist') or e.get('creator') or e.get('uploader')
                    or e.get('channel') or ''),
        'album': e.get('album'),
        'duration_ms': _ms(e.get('duration')),
        'art': _yt_art(vid, e.get('thumbnails')),
        'url': e.get('webpage_url') or (f'https://www.youtube.com/watch?v={vid}'
                                        if vid else None),
        'embed': _yt_embed(vid=vid),
        # A live stream is HLS with no end; the deck decodes whole files only.
        'streamable': not live,
        'live': bool(live),
        'plays': e.get('view_count'),
        'likes': e.get('like_count'),
        'channel': e.get('channel') or e.get('uploader'),
        'channel_url': e.get('channel_url') or e.get('uploader_url'),
        'release': (e.get('upload_date') or '')[:8] or None,
        'genre': (e.get('categories') or [None])[0],
    }


def _yt_playlist_row(e: dict) -> dict:
    pid = e.get('id')
    return {
        'source': 'youtube', 'kind': 'playlist', 'id': pid,
        'name': e.get('title'), 'artists': e.get('channel') or e.get('uploader') or '',
        'tracks': e.get('playlist_count') or e.get('video_count'),
        'art': (e.get('thumbnails') or [{}])[-1].get('url'),
        'url': e.get('webpage_url') or (f'https://www.youtube.com/playlist?list={pid}'
                                        if pid else None),
        'embed': _yt_embed(playlist=pid), 'streamable': True,
    }


def _yt_channel_row(e: dict) -> dict:
    cid = e.get('id') or e.get('channel_id')
    return {
        'source': 'youtube', 'kind': 'artist', 'id': cid,
        'name': e.get('title') or e.get('channel') or e.get('uploader'),
        'artists': e.get('channel') or e.get('uploader'),
        'followers': e.get('channel_follower_count') or e.get('subscriber_count'),
        'tracks': e.get('playlist_count') or e.get('video_count'),
        'art': (e.get('thumbnails') or [{}])[-1].get('url'),
        'url': e.get('url') or e.get('channel_url') or e.get('webpage_url'),
        'streamable': False,
    }


def _yt_row(e: dict) -> Optional[dict]:
    if not isinstance(e, dict) or not e.get('id'):
        return None
    t = e.get('_type') or 'video'
    ie = (e.get('ie_key') or '').lower()
    if t == 'playlist' or ie.endswith('tab') or ie == 'youtubeplaylist':
        if e.get('channel_is_verified') is not None or ie.endswith('tab'):
            if str(e.get('id', '')).startswith('UC') or (e.get('url') or '').find('/channel/') > 0:
                return _yt_channel_row(e)
        return _yt_playlist_row(e)
    return _yt_video_row(e)


def yt_search(q: str, kind='track', limit=20) -> dict:
    """Search YouTube for videos, playlists or channels."""
    kind = str(kind or 'track').lower()
    if kind not in YT_KINDS:
        raise PlatformError(f'kind must be one of {", ".join(YT_KINDS)}')
    want = YT_KINDS[kind]
    limit = max(1, min(int(limit), 50))
    if want == 'video':
        target = f'ytsearch{limit}:{q}'
    else:
        target = ('https://www.youtube.com/results?search_query='
                  + quote(q) + '&sp=' + YT_FILTERS[want])
    info = _yt_extract(target, extract_flat='in_playlist', playlistend=limit,
                       noplaylist=False)
    rows = []
    for e in (info.get('entries') or [])[:limit]:
        row = _yt_row(e)
        if not row:
            continue
        if want == 'playlist' and row['kind'] != 'playlist':
            continue
        if want == 'channel':
            row = _yt_channel_row(e)
        rows.append(row)
    return {'source': 'youtube', 'kind': kind, 'query': q,
            'count': len(rows), 'items': rows}


def yt_video(video: str) -> dict:
    """One video's full metadata — id or any watch/shorts/youtu.be URL."""
    info = _yt_extract(_yt_target(video))
    row = _yt_video_row(info)
    row['description'] = (info.get('description') or '')[:1200] or None
    row['formats'] = sorted({f.get('acodec') for f in (info.get('formats') or [])
                             if f.get('acodec') and f['acodec'] != 'none'})
    return row


def yt_playlist(playlist: str, limit=100) -> dict:
    """A playlist, mix or album with its videos listed."""
    pid = str(playlist).strip()
    target = pid if pid.startswith('http') else f'https://www.youtube.com/playlist?list={pid}'
    limit = max(1, min(int(limit), 200))
    info = _yt_extract(target, extract_flat='in_playlist', noplaylist=False,
                       playlistend=limit)
    rows = [r for r in (_yt_video_row(e) for e in (info.get('entries') or [])
                        if isinstance(e, dict) and e.get('id'))]
    return {
        'source': 'youtube', 'kind': 'playlist', 'id': info.get('id') or pid,
        'name': info.get('title'), 'artists': info.get('channel') or info.get('uploader'),
        'url': info.get('webpage_url') or target,
        'embed': _yt_embed(playlist=info.get('id') or pid),
        'art': rows[0]['art'] if rows else None,
        'streamable': True, 'count': len(rows), 'items': rows,
    }


def yt_channel(channel: str, limit=30) -> dict:
    """A channel's own uploads, newest first."""
    c = str(channel).strip()
    if c.startswith('http'):
        target = c.rstrip('/')
    elif c.startswith('@'):
        target = f'https://www.youtube.com/{c}'
    elif c.startswith('UC'):
        target = f'https://www.youtube.com/channel/{c}'
    else:
        target = f'https://www.youtube.com/@{c}'
    if not target.endswith('/videos'):
        target += '/videos'
    limit = max(1, min(int(limit), 100))
    info = _yt_extract(target, extract_flat='in_playlist', noplaylist=False,
                       playlistend=limit)
    entries = info.get('entries') or []
    # A channel tab can nest one level: entries[0] is itself a playlist of videos.
    if entries and isinstance(entries[0], dict) and entries[0].get('_type') == 'playlist':
        entries = entries[0].get('entries') or []
    rows = [_yt_video_row(e) for e in entries if isinstance(e, dict) and e.get('id')]
    return {
        'source': 'youtube', 'kind': 'artist',
        'id': info.get('channel_id') or info.get('id'),
        'name': info.get('channel') or info.get('title'),
        'artists': info.get('channel') or info.get('title'),
        'followers': info.get('channel_follower_count'),
        'url': info.get('channel_url') or target,
        'streamable': False, 'count': len(rows), 'items': rows,
    }


def _yt_target(video: str) -> str:
    v = str(video or '').strip()
    if not v:
        raise PlatformError('a YouTube video id or URL is required')
    return v if v.startswith('http') else f'https://www.youtube.com/watch?v={v}'


def _yt_pick(info: dict) -> dict:
    """The best single audio-only format to hand a browser.

    Preference is m4a (AAC) first because every browser's decodeAudioData
    reads it, then opus/webm, then anything with sound. Adaptive formats only:
    a progressive 18/22 stream carries video the deck would decode and throw
    away.
    """
    fmts = [f for f in (info.get('formats') or [])
            if f.get('url') and f.get('acodec') not in (None, 'none')
            and (f.get('protocol') or '').startswith('http')]
    audio_only = [f for f in fmts if f.get('vcodec') in (None, 'none')]

    def score(f):
        ext = f.get('ext') or ''
        return (0 if ext == 'm4a' else 1 if ext in ('webm', 'opus') else 2,
                -(f.get('abr') or f.get('tbr') or 0))
    for pool in (audio_only, fmts):
        if pool:
            return sorted(pool, key=score)[0]
    raise PlatformError(f'"{info.get("title")}" has no downloadable audio stream '
                        '(live streams and DRM videos do not)')


def _expiry(url: str) -> float:
    m = re.search(r'[?&]expire=(\d+)', url or '')
    return float(m.group(1)) if m else time.time() + 3600


def yt_stream(video: str) -> dict:
    """A video's audio track — a signed googlevideo URL, proxied not direct.

    googlevideo answers a browser fetch with no ``Access-Control-Allow-Origin``
    header at all, so this one goes through serve.py's ``/stream/youtube``
    proxy the same way Bandcamp's does. URLs are cached until a minute before
    they expire, which is what makes a second load of the same track instant.
    """
    target = _yt_target(video)
    with YT_STATE['lock']:
        hit = YT_STATE['streams'].get(target)
        if hit and hit['expires'] > time.time() + 60:
            return dict(hit['where'])
    info = _yt_extract(target)
    if info.get('is_live'):
        raise PlatformError(f'"{info.get("title")}" is a live stream — the deck '
                            'decodes whole files, not an open-ended one')
    f = _yt_pick(info)
    vid = info.get('id')
    where = {
        'source': 'youtube', 'id': vid, 'name': info.get('title'),
        'artists': info.get('artist') or info.get('uploader') or info.get('channel'),
        'duration_ms': _ms(info.get('duration')), 'art': _yt_art(vid, info.get('thumbnails')),
        'url': f['url'], 'direct': False,
        'format': f.get('ext') or 'm4a', 'codec': f.get('acodec'),
        'abr': f.get('abr') or f.get('tbr'), 'filesize': f.get('filesize')
        or f.get('filesize_approx'),
        'mime': 'audio/mp4' if (f.get('ext') == 'm4a') else 'audio/webm',
        'referer': info.get('webpage_url') or target,
    }
    with YT_STATE['lock']:
        YT_STATE['streams'][target] = {'where': dict(where),
                                       'expires': _expiry(f['url'])}
        if len(YT_STATE['streams']) > 128:      # oldest first, this is a cache
            for k in list(YT_STATE['streams'])[:32]:
                YT_STATE['streams'].pop(k, None)
    return where


def yt_status() -> dict:
    ok = yt_available()
    version = None
    if ok:
        import yt_dlp
        version = yt_dlp.version.__version__
    return {
        'source': 'youtube', 'configured': ok,
        'auth': 'none needed' + (', cookies file found' if YT_COOKIES.exists() else ''),
        'yt_dlp': version,
        'cookies': YT_COOKIES.exists(),
        'cached_streams': len(YT_STATE['streams']),
        'last_error': YT_STATE['last_error'],
        'streams': ('best audio-only format (m4a, else opus), proxied by the '
                    'module — googlevideo sends no CORS header')
        if ok else 'unavailable: pip install yt-dlp',
    }


# ═══════════════════════════════════════════════════════════════════════════
# Internet Archive
# ═══════════════════════════════════════════════════════════════════════════
#
# The one source here that is unambiguously free to use: netlabels, Live Music
# Archive concerts, 78rpm transfers, radio. No key, no challenge, and the files
# are served with ``Access-Control-Allow-Origin: *``, so the browser fetches
# them itself and the module never touches the bytes. An item is an album; its
# audio files are the tracks, addressed as ``identifier/filename``.

IA = 'https://archive.org'
IA_SEARCH = IA + '/advancedsearch.php'
IA_FIELDS = ['identifier', 'title', 'creator', 'year', 'date', 'downloads',
             'collection', 'subject', 'mediatype', 'item_size']
IA_AUDIO_EXT = ('.mp3', '.ogg', '.oga', '.flac', '.m4a', '.wav', '.opus', '.aiff')
# Format label → how much the deck wants it. Lossy first: a 300MB WAV is a
# worse thing to hand a browser than the 8MB MP3 sitting next to it.
IA_FORMAT_RANK = {'VBR MP3': 0, '128Kbps MP3': 1, 'MP3': 1, '64Kbps MP3': 2,
                  'Ogg Vorbis': 3, 'Flac': 4, '24bit Flac': 5, 'WAVE': 6}

_ia = requests.Session()
_ia.headers.update({'User-Agent': UA, 'Accept': 'application/json'})


def _ia_get(url: str, **params) -> dict:
    try:
        r = _ia.get(url, params=params or None, timeout=TIMEOUT)
    except requests.RequestException as e:
        raise PlatformError(f'Internet Archive did not answer: {e}')
    if r.status_code >= 400:
        raise PlatformError(f'Internet Archive answered {r.status_code}')
    try:
        return r.json()
    except ValueError:
        raise PlatformError('Internet Archive did not answer with JSON')


def _ia_art(identifier: str) -> str:
    return f'{IA}/services/img/{identifier}'


def _ia_embed(identifier: str) -> str:
    return f'{IA}/embed/{identifier}'


def _ia_item_row(d: dict) -> dict:
    ident = d.get('identifier')
    creator = d.get('creator')
    if isinstance(creator, list):
        creator = ', '.join(creator)
    subject = d.get('subject')
    if isinstance(subject, str):
        subject = [subject]
    return {
        'source': 'archive', 'kind': 'album', 'id': ident, 'name': d.get('title'),
        'artists': creator or '', 'art': _ia_art(ident),
        'url': f'{IA}/details/{ident}', 'embed': _ia_embed(ident),
        'release': str(d.get('year') or (d.get('date') or ''))[:10] or None,
        'plays': d.get('downloads'), 'streamable': True,
        'genre': (subject or [None])[0],
        'collection': (d.get('collection') or [None])[0]
        if isinstance(d.get('collection'), list) else d.get('collection'),
    }


def ia_search(q: str, kind='album', limit=20, collection=None) -> dict:
    """Search the audio half of archive.org.

    Items are albums, concerts and radio shows — open one with
    :func:`ia_item` to get its tracks. ``collection`` narrows to one corner of
    the archive, e.g. ``etree`` (Live Music Archive) or ``netlabels``.
    """
    query = f'({q}) AND mediatype:(audio)'
    if collection:
        query += f' AND collection:({collection})'
    body = _ia_get(IA_SEARCH, **{
        'q': query, 'fl[]': IA_FIELDS, 'rows': max(1, min(int(limit), 100)),
        'page': 1, 'output': 'json'})
    docs = ((body.get('response') or {}).get('docs')) or []
    rows = [_ia_item_row(d) for d in docs if d.get('identifier')]
    return {'source': 'archive', 'kind': 'album', 'query': q,
            'total': (body.get('response') or {}).get('numFound'),
            'count': len(rows), 'items': rows}


def _ia_tracks(ident: str, meta: dict) -> list:
    """One audio track per recording, keeping the best format of each.

    An item usually holds the same music several times over — an MP3, an Ogg
    and a FLAC of every track. They are grouped by the original file each was
    derived from, and the friendliest format of each group wins.
    """
    files = meta.get('files') or []
    groups = {}
    for f in files:
        name = f.get('name') or ''
        if not name.lower().endswith(IA_AUDIO_EXT):
            continue
        origin = f.get('original') if f.get('source') == 'derivative' else name
        rank = IA_FORMAT_RANK.get(f.get('format'), 7)
        cur = groups.get(origin)
        if cur is None or rank < cur[0]:
            groups[origin] = (rank, f)
    md = meta.get('metadata') or {}
    creator = md.get('creator')
    if isinstance(creator, list):
        creator = ', '.join(creator)
    rows = []
    for _, f in groups.values():
        name = f['name']
        rows.append({
            'source': 'archive', 'kind': 'track', 'id': f'{ident}/{name}',
            'name': f.get('title') or name.rsplit('/', 1)[-1].rsplit('.', 1)[0],
            'artists': f.get('artist') or creator or '',
            'album': f.get('album') or md.get('title'),
            'num': int(str(f.get('track')).split('/')[0])
            if str(f.get('track') or '').split('/')[0].isdigit() else None,
            'duration_ms': _ms(f.get('length')) if str(f.get('length') or '')
            .replace('.', '', 1).isdigit() else _hms(f.get('length')),
            'art': _ia_art(ident), 'url': f'{IA}/details/{ident}',
            'file_url': f'{IA}/download/{ident}/{quote(name)}',
            'embed': _ia_embed(ident), 'streamable': True,
            'format': f.get('format'), 'size': int(f['size']) if str(f.get('size') or '')
            .isdigit() else None,
        })
    rows.sort(key=lambda r: (r['num'] is None, r['num'] or 0, r['name'] or ''))
    return rows


def _hms(text) -> Optional[int]:
    """``12:34`` or ``1:02:03`` → milliseconds; archive.org uses both."""
    parts = str(text or '').split(':')
    if len(parts) < 2:
        return None
    try:
        secs = 0.0
        for p in parts:
            secs = secs * 60 + float(p)
        return int(secs * 1000)
    except ValueError:
        return None


def ia_item(identifier: str) -> dict:
    """One archive.org item — an album, a concert, a show — with its tracks."""
    ident = str(identifier or '').strip().strip('/')
    if not ident:
        raise PlatformError('an archive.org identifier is required')
    ident = ident.split('/')[0]
    meta = _ia_get(f'{IA}/metadata/{ident}')
    if not meta or not meta.get('files'):
        raise PlatformError(f'archive.org has no item called {ident!r}')
    md = meta.get('metadata') or {}
    if md.get('mediatype') not in (None, 'audio', 'etree'):
        raise PlatformError(f'{ident} is {md.get("mediatype")}, not audio')
    tracks = _ia_tracks(ident, meta)
    row = _ia_item_row({**md, 'identifier': ident})
    row.update({'count': len(tracks), 'items': tracks,
                'label': md.get('publisher') or md.get('collection'),
                'streamable': bool(tracks),
                'license': md.get('licenseurl') or md.get('rights')})
    return row


def ia_stream(track_id: str) -> dict:
    """``identifier/filename`` → the file's URL, which the browser can fetch.

    archive.org sends ``Access-Control-Allow-Origin: *`` and honours Range, so
    this is the one platform where the deck downloads straight from the
    source; the proxy stays available as a fallback.
    """
    ref = str(track_id or '').strip().strip('/')
    if '/' not in ref:
        item = ia_item(ref)
        if not item['items']:
            raise PlatformError(f'{ref} has no playable audio')
        ref = item['items'][0]['id']
    ident, name = ref.split('/', 1)
    meta = _ia_get(f'{IA}/metadata/{ident}')
    f = next((x for x in meta.get('files') or [] if x.get('name') == name), None)
    if not f:
        raise PlatformError(f'{name} is not a file in {ident}')
    md = meta.get('metadata') or {}
    creator = md.get('creator')
    if isinstance(creator, list):
        creator = ', '.join(creator)
    return {
        'source': 'archive', 'id': f'{ident}/{name}',
        'name': f.get('title') or name.rsplit('/', 1)[-1],
        'artists': f.get('artist') or creator or '',
        'duration_ms': _hms(f.get('length')) or _ms(f.get('length')),
        'art': _ia_art(ident),
        'url': f'{IA}/download/{ident}/{quote(name)}',
        'direct': True, 'format': f.get('format'),
        'size': int(f['size']) if str(f.get('size') or '').isdigit() else None,
    }


def ia_status() -> dict:
    return {
        'source': 'archive', 'configured': True, 'auth': 'none needed',
        'scope': 'mediatype:audio — netlabels, Live Music Archive, 78rpm, radio',
        'streams': 'the original files, fetched by the browser (CORS open)',
    }


# ═══════════════════════════════════════════════════════════════════════════
# links
# ═══════════════════════════════════════════════════════════════════════════

def detect(text: str) -> Optional[dict]:
    """What a pasted link points at — ``{source, kind, id, url}`` or None."""
    s = (text or '').strip()
    m = re.match(r'^spotify:(track|album|artist|playlist):([A-Za-z0-9]+)$', s)
    if m:
        return {'source': 'spotify', 'kind': m.group(1), 'id': m.group(2), 'url': s}
    if not re.match(r'^https?://', s, re.I):
        return None
    u = urlparse(s)
    host = u.netloc.lower().split(':')[0]
    path = u.path.rstrip('/')
    if host.endswith('spotify.com'):
        m = re.search(r'/(track|album|artist|playlist)/([A-Za-z0-9]+)', path)
        if m:
            return {'source': 'spotify', 'kind': m.group(1), 'id': m.group(2), 'url': s}
        return None
    if host.endswith('youtube.com') or host.endswith('youtu.be'):
        return _detect_youtube(u, path, s)
    if host.endswith('archive.org'):
        m = re.search(r'/(?:details|download|embed|metadata)/([^/]+)(/.*)?$', path)
        if not m:
            return None
        ident, rest = m.group(1), (m.group(2) or '').lstrip('/')
        if rest and rest.lower().endswith(IA_AUDIO_EXT):
            from urllib.parse import unquote
            return {'source': 'archive', 'kind': 'track',
                    'id': f'{ident}/{unquote(rest)}', 'url': s}
        return {'source': 'archive', 'kind': 'album', 'id': ident,
                'url': f'{IA}/details/{ident}'}
    if host.endswith('bandcamp.com') or '/album/' in path or '/track/' in path:
        if host.endswith('bandcamp.com'):
            kind = 'track' if '/track/' in path else ('album' if '/album/' in path else 'artist')
            clean = f'{u.scheme}://{u.netloc}{path}'
            return {'source': 'bandcamp', 'kind': kind, 'id': clean, 'url': clean}
    if host in ('soundcloud.com', 'www.soundcloud.com', 'm.soundcloud.com', 'on.soundcloud.com'):
        parts = [p for p in path.split('/') if p]
        if not parts:
            return None
        if len(parts) >= 3 and parts[1] == 'sets':
            kind = 'playlist'
        elif len(parts) >= 2:
            kind = 'track'
        else:
            kind = 'artist'
        clean = f'https://soundcloud.com{path}'
        return {'source': 'soundcloud', 'kind': kind, 'id': clean, 'url': clean}
    return None


def _detect_youtube(u, path, raw: str) -> Optional[dict]:
    """A YouTube link, in any of the six shapes the site hands out.

    A watch URL that also carries ``list=`` is a video being played inside a
    playlist; the video is what was clicked, so that is what comes back.
    """
    from urllib.parse import parse_qs
    host = u.netloc.lower().split(':')[0]
    qs = parse_qs(u.query)
    if host.endswith('youtu.be'):
        vid = path.strip('/').split('/')[0]
        return {'source': 'youtube', 'kind': 'track', 'id': vid,
                'url': f'https://www.youtube.com/watch?v={vid}'} if vid else None
    if path in ('/watch', '/watch/') and qs.get('v'):
        vid = qs['v'][0]
        return {'source': 'youtube', 'kind': 'track', 'id': vid,
                'url': f'https://www.youtube.com/watch?v={vid}'}
    m = re.match(r'^/(?:shorts|embed|v|live)/([\w-]+)', path)
    if m:
        return {'source': 'youtube', 'kind': 'track', 'id': m.group(1),
                'url': f'https://www.youtube.com/watch?v={m.group(1)}'}
    if path.rstrip('/') in ('/playlist', '/watch_videos') and qs.get('list'):
        pid = qs['list'][0]
        return {'source': 'youtube', 'kind': 'playlist', 'id': pid,
                'url': f'https://www.youtube.com/playlist?list={pid}'}
    m = re.match(r'^/(channel/[\w-]+|@[^/]+|c/[^/]+|user/[^/]+)', path)
    if m:
        return {'source': 'youtube', 'kind': 'artist', 'id': m.group(1).split('/')[-1],
                'url': f'https://www.youtube.com/{m.group(1)}'}
    return None
