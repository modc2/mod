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


def _read_keys() -> dict:
    if KEYS_FILE.exists():
        try:
            return json.loads(KEYS_FILE.read_text()) or {}
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def credentials() -> tuple:
    """``(client_id, client_secret)`` — file first, then environment."""
    keys = _read_keys()
    cid = keys.get('client_id') or os.environ.get('SPOTIFY_CLIENT_ID') or ''
    sec = keys.get('client_secret') or os.environ.get('SPOTIFY_CLIENT_SECRET') or ''
    return cid.strip(), sec.strip()


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
    cid, sec = credentials()
    return {
        'configured': bool(cid and sec),
        'client_id': mask(cid),
        'client_secret': '••••' if sec else '',
        'source': 'file' if KEYS_FILE.exists() else ('env' if cid else None),
        'keys_file': str(KEYS_FILE),
        'grant': 'client_credentials',
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

    cid, sec = credentials()
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


def get(path: str, **params) -> dict:
    """GET a Spotify API path, with the 403s explained rather than raw."""
    r = requests.get(f'{API}/{path.lstrip("/")}',
                     headers={'Authorization': f'Bearer {token()}'},
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


def track_row(item: dict) -> dict:
    """One track, flattened to what a crate row needs."""
    return {
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
        rows = [{'id': i.get('id'), 'name': i.get('name'), 'artists': _artists(i),
                 'tracks': i.get('total_tracks'), 'release': i.get('release_date'),
                 'art': _art(i), 'url': (i.get('external_urls') or {}).get('spotify')}
                for i in items]
    elif kind == 'artist':
        rows = [{'id': i.get('id'), 'name': i.get('name'),
                 'genres': i.get('genres') or [], 'popularity': i.get('popularity'),
                 'followers': (i.get('followers') or {}).get('total'),
                 'art': _art(i), 'url': (i.get('external_urls') or {}).get('spotify')}
                for i in items]
    else:
        rows = [{'id': i.get('id'), 'name': i.get('name'),
                 'owner': (i.get('owner') or {}).get('display_name'),
                 'tracks': (i.get('tracks') or {}).get('total'),
                 'art': _art(i), 'url': (i.get('external_urls') or {}).get('spotify')}
                for i in items]
    return {'kind': kind, 'query': q, 'count': len(rows), 'items': rows}


def track(track_id: str) -> dict:
    return track_row(get(f'tracks/{track_id}'))


def playlist(playlist_id: str, limit: int = 50) -> dict:
    body = get(f'playlists/{playlist_id}/tracks',
               limit=max(1, min(int(limit), 100)))
    rows = [track_row(i['track']) for i in body.get('items') or []
            if i and i.get('track')]
    return {'id': playlist_id, 'count': len(rows), 'items': rows}


def features(track_id: str) -> dict:
    """Tempo/key from Spotify — usually refused now. Kept so the error is honest."""
    return get(f'audio-features/{track_id}')
