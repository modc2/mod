"""Spotify client — the metadata half of the booth.

Credentials never live in the repo: they are read from ``~/.mod/musica/keys.json``
(mode 0600) or from ``SPOTIFY_CLIENT_ID`` / ``SPOTIFY_CLIENT_SECRET``.

This uses the *client credentials* grant, so it can read anything public —
search, tracks, albums, artists, public playlists — without a user ever logging
in. Anything scoped to a person (your library, your playlists, playback) needs
the authorization-code flow, which this module does not do yet.

Two things Spotify itself will not give you, which is why the mixer analyses
audio locally instead:

* ``/audio-features`` and ``/audio-analysis`` (tempo, key, beat grid) return 403
  for any app registered after 2024-11-27. :func:`features` still asks, and says
  so plainly when it is refused.
* Streamed audio is DRM-protected. It cannot be routed through Web Audio, so a
  Spotify track can be *planned* here but not EQ'd, looped or scratched.
"""

import base64
import json
import os
import time
from pathlib import Path
from typing import Optional

import requests

KEYS_DIR = Path.home() / '.mod' / 'musica'
KEYS_FILE = KEYS_DIR / 'keys.json'
# orbit/spotify keeps its own app keys and (after a login) user tokens here.
# musica reads both, so one Spotify app serves the whole fleet — nothing is
# written back to the other module's files.
SIBLING_KEYS = Path.home() / '.mod' / 'spotify' / 'keys.json'
SIBLING_AUTH = Path.home() / '.mod' / 'spotify' / 'auth.json'

TOKEN_URL = 'https://accounts.spotify.com/api/token'
API = 'https://api.spotify.com/v1'
TIMEOUT = 15

# Endpoints Spotify closed to apps registered after this date. Kept here so the
# error message can name the reason instead of echoing a bare 403.
DEPRECATED_ON = '2024-11-27'
DEPRECATED = ('audio-features', 'audio-analysis', 'recommendations',
              'related-artists')


class SpotifyError(RuntimeError):
    pass


def _read_json(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text()) or {}
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _read_keys() -> dict:
    return _read_json(KEYS_FILE)


def credentials() -> tuple:
    """``(client_id, client_secret, source)`` — musica's file, then the
    environment, then orbit/spotify's keys."""
    keys = _read_keys()
    cid = keys.get('client_id') or os.environ.get('SPOTIFY_CLIENT_ID') or ''
    sec = keys.get('client_secret') or os.environ.get('SPOTIFY_CLIENT_SECRET') or ''
    source = 'file' if keys.get('client_id') else ('env' if cid else None)
    if not cid:
        sib = _read_json(SIBLING_KEYS)
        cid = sib.get('client_id') or ''
        sec = sib.get('client_secret') or ''
        source = 'orbit/spotify' if cid else None
    return cid.strip(), sec.strip(), source


def user_token() -> Optional[str]:
    """An access token for the operator's own Spotify account, if orbit/spotify
    has logged one in. Refreshed in memory when stale; never written back."""
    auth = _read_json(SIBLING_AUTH)
    if not auth.get('access_token'):
        return None
    if auth.get('expires_at', 0) - 60 > time.time():
        return auth['access_token']
    cached = _USER.get('value')
    if cached and time.time() < _USER.get('expires', 0):
        return cached
    rt = auth.get('refresh_token')
    cid = _read_json(SIBLING_KEYS).get('client_id') or credentials()[0]
    if not (rt and cid):
        return None
    r = requests.post(TOKEN_URL, data={'grant_type': 'refresh_token',
                                       'refresh_token': rt, 'client_id': cid},
                      timeout=TIMEOUT)
    if r.status_code != 200:
        return None
    body = r.json()
    _USER['value'] = body.get('access_token')
    _USER['expires'] = time.time() + int(body.get('expires_in', 3600)) - 60
    return _USER['value']


_USER = {'value': None, 'expires': 0.0}


def save_key(client_id: str, client_secret: str) -> dict:
    """Write credentials to ``~/.mod/musica/keys.json``, owner-readable only."""
    KEYS_DIR.mkdir(parents=True, exist_ok=True)
    keys = _read_keys()
    keys.update({'client_id': str(client_id).strip(),
                 'client_secret': str(client_secret).strip()})
    KEYS_FILE.write_text(json.dumps(keys, indent=2))
    KEYS_FILE.chmod(0o600)
    _TOKEN['value'] = None
    return status()


def mask(value: str) -> str:
    if not value:
        return ''
    return value[:4] + '…' + value[-4:] if len(value) > 10 else '…'


def status() -> dict:
    """What is configured, without ever echoing the secret back."""
    cid, sec, source = credentials()
    logged_in = bool(_read_json(SIBLING_AUTH).get('refresh_token'))
    return {
        'source': 'spotify',
        'configured': bool(cid and sec),
        'client_id': mask(cid),
        'client_secret': '••••' if sec else '',
        'keys_source': source,
        'keys_file': str(KEYS_FILE),
        'grant': 'client_credentials' + (' + orbit/spotify user token' if logged_in else ''),
        'logged_in': logged_in,
        'embeds': 'open.spotify.com/embed — previews play in the crate without keys',
        'can_read': ['search', 'tracks', 'albums', 'artists', 'public playlists'],
        'cannot_read': [f'{e} (403 for apps created after {DEPRECATED_ON})'
                        for e in DEPRECATED] + ['anything user-scoped (needs OAuth login)'],
        'audio': 'metadata only — streamed audio is DRM-protected and cannot be '
                 'mixed through Web Audio; load a local file to actually mix it',
    }


_TOKEN = {'value': None, 'expires': 0.0}


def token() -> str:
    """A cached app token, refreshed a minute before it lapses."""
    if _TOKEN['value'] and time.time() < _TOKEN['expires']:
        return _TOKEN['value']

    cid, sec, _ = credentials()
    if not (cid and sec):
        raise SpotifyError(
            'no Spotify credentials — create an app at '
            'https://developer.spotify.com/dashboard then run: '
            "m musica/set_key client_id=… client_secret=…")

    basic = base64.b64encode(f'{cid}:{sec}'.encode()).decode()
    r = requests.post(TOKEN_URL,
                      data={'grant_type': 'client_credentials'},
                      headers={'Authorization': f'Basic {basic}'},
                      timeout=TIMEOUT)
    if r.status_code != 200:
        raise SpotifyError(f'token request failed ({r.status_code}): {r.text[:200]}')
    body = r.json()
    _TOKEN['value'] = body['access_token']
    _TOKEN['expires'] = time.time() + int(body.get('expires_in', 3600)) - 60
    return _TOKEN['value']


def get(path: str, _user=False, **params) -> dict:
    """GET a Spotify API path, with the 403s explained rather than raw.

    ``_user=True`` sends the operator's own token (from orbit/spotify) instead
    of the app token — the only way to read a private library.
    """
    tok = user_token() if _user else None
    if _user and not tok:
        raise SpotifyError('not logged in to Spotify — run `m spotify/login` in '
                           'orbit/spotify, then musica can read your library')
    r = requests.get(f'{API}/{path.lstrip("/")}',
                     headers={'Authorization': f'Bearer {tok or token()}'},
                     params={k: v for k, v in params.items() if v is not None},
                     timeout=TIMEOUT)
    if r.status_code == 403 and any(d in path for d in DEPRECATED):
        raise SpotifyError(
            f'/{path.lstrip("/")} is deprecated: Spotify closed it on '
            f'{DEPRECATED_ON} to apps registered after that date. musica '
            'analyses tempo and key locally instead — load the file on a deck.')
    if r.status_code == 429:
        raise SpotifyError(f'rate limited — retry after {r.headers.get("Retry-After", "?")}s')
    if r.status_code != 200:
        raise SpotifyError(f'{path} failed ({r.status_code}): {r.text[:200]}')
    return r.json()


# ── shapes the console actually renders ──────────────────────────────────

def _artists(item: dict) -> str:
    return ', '.join(a.get('name', '') for a in item.get('artists') or [])


def _art(item: dict) -> Optional[str]:
    imgs = (item.get('album') or item).get('images') or []
    return imgs[-1]['url'] if imgs else None


def embed(kind: str, item_id) -> Optional[str]:
    return f'https://open.spotify.com/embed/{kind}/{item_id}' if item_id else None


def track_row(item: dict) -> dict:
    """One track, flattened to what a crate row needs."""
    return {
        'source': 'spotify', 'kind': 'track', 'streamable': False,
        'embed': embed('track', item.get('id')),
        'id': item.get('id'),
        'name': item.get('name'),
        'artists': _artists(item),
        'album': (item.get('album') or {}).get('name'),
        'release': (item.get('album') or {}).get('release_date'),
        'duration_ms': item.get('duration_ms'),
        'explicit': item.get('explicit'),
        'popularity': item.get('popularity'),
        'art': _art(item),
        'url': (item.get('external_urls') or {}).get('spotify'),
        # 30-second previews were withdrawn alongside the audio endpoints; the
        # field is kept because older apps still receive it.
        'preview_url': item.get('preview_url'),
    }


def search(q: str, kind: str = 'track', limit: int = 20, offset: int = 0) -> dict:
    kind = str(kind).lower()
    if kind not in ('track', 'album', 'artist', 'playlist'):
        raise SpotifyError(f'kind must be track|album|artist|playlist, got {kind!r}')
    body = get('search', q=q, type=kind, limit=max(1, min(int(limit), 50)),
               offset=int(offset))
    items = [i for i in (body.get(kind + 's') or {}).get('items') or [] if i]
    if kind == 'track':
        rows = [track_row(i) for i in items]
    elif kind == 'album':
        rows = [{'source': 'spotify', 'kind': 'album', 'streamable': False,
                 'embed': embed('album', i.get('id')),
                 'id': i.get('id'), 'name': i.get('name'), 'artists': _artists(i),
                 'tracks': i.get('total_tracks'), 'release': i.get('release_date'),
                 'art': _art(i), 'url': (i.get('external_urls') or {}).get('spotify')}
                for i in items]
    elif kind == 'artist':
        rows = [{'source': 'spotify', 'kind': 'artist', 'streamable': False,
                 'embed': embed('artist', i.get('id')),
                 'id': i.get('id'), 'name': i.get('name'), 'artists': i.get('name'),
                 'genres': i.get('genres') or [], 'popularity': i.get('popularity'),
                 'followers': (i.get('followers') or {}).get('total'),
                 'art': _art(i), 'url': (i.get('external_urls') or {}).get('spotify')}
                for i in items]
    else:
        rows = [{'source': 'spotify', 'kind': 'playlist', 'streamable': False,
                 'embed': embed('playlist', i.get('id')),
                 'id': i.get('id'), 'name': i.get('name'),
                 'artists': (i.get('owner') or {}).get('display_name'),
                 'owner': (i.get('owner') or {}).get('display_name'),
                 'tracks': (i.get('tracks') or {}).get('total'),
                 'art': _art(i), 'url': (i.get('external_urls') or {}).get('spotify')}
                for i in items]
    return {'source': 'spotify', 'kind': kind, 'query': q, 'count': len(rows), 'items': rows}


def track(track_id: str) -> dict:
    return track_row(get(f'tracks/{track_id}'))


def playlist(playlist_id: str, limit: int = 50) -> dict:
    """A playlist's tracks. Public ones read with the app token; a private one
    of your own reads with the user token when orbit/spotify is logged in."""
    try:
        body = get(f'playlists/{playlist_id}/tracks',
                   limit=max(1, min(int(limit), 100)))
    except SpotifyError:
        if not user_token():
            raise
        body = get(f'playlists/{playlist_id}/tracks', _user=True,
                   limit=max(1, min(int(limit), 100)))
    rows = [track_row(i['track']) for i in body.get('items') or []
            if i and i.get('track')]
    return {'source': 'spotify', 'kind': 'playlist', 'id': playlist_id,
            'embed': embed('playlist', playlist_id), 'count': len(rows), 'items': rows}


def album(album_id: str) -> dict:
    a = get(f'albums/{album_id}')
    rows = []
    for t in (a.get('tracks') or {}).get('items') or []:
        t = dict(t, album=a)
        rows.append(track_row(t))
    return {'source': 'spotify', 'kind': 'album', 'id': album_id, 'name': a.get('name'),
            'artists': _artists(a), 'release': a.get('release_date'), 'art': _art(a),
            'url': (a.get('external_urls') or {}).get('spotify'),
            'embed': embed('album', album_id), 'count': len(rows), 'items': rows}


def artist_top(artist_id: str) -> dict:
    a = get(f'artists/{artist_id}')
    body = get(f'artists/{artist_id}/top-tracks', market='US')
    rows = [track_row(t) for t in body.get('tracks') or []]
    return {'source': 'spotify', 'kind': 'artist', 'id': artist_id, 'name': a.get('name'),
            'art': _art(a), 'url': (a.get('external_urls') or {}).get('spotify'),
            'embed': embed('artist', artist_id), 'count': len(rows), 'items': rows}


def my_playlists(limit: int = 50) -> dict:
    """The operator's own playlists — needs orbit/spotify's login."""
    body = get('me/playlists', _user=True, limit=max(1, min(int(limit), 50)))
    rows = [{'source': 'spotify', 'kind': 'playlist', 'streamable': False,
             'embed': embed('playlist', i.get('id')),
             'id': i.get('id'), 'name': i.get('name'),
             'artists': (i.get('owner') or {}).get('display_name'),
             'tracks': (i.get('tracks') or {}).get('total'),
             'art': _art(i), 'url': (i.get('external_urls') or {}).get('spotify')}
            for i in body.get('items') or [] if i]
    return {'source': 'spotify', 'kind': 'playlist', 'mine': True,
            'count': len(rows), 'items': rows}


def features(track_id: str) -> dict:
    """Tempo/key from Spotify — usually refused now. Kept so the error is honest."""
    return get(f'audio-features/{track_id}')
