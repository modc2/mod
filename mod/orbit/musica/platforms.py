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
