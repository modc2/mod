"""spotify — the adapter: OAuth 2.0 + the Spotify Web API, on the stdlib.

One class, `Spotify`, that owns three things the rest of the module never has
to think about:

  * **credentials** — a Spotify app's client_id/secret, from the environment or
    ~/.mod/spotify/keys.json (0600, off-tree, never committed).
  * **tokens** — Authorization Code + PKCE for user-scoped calls, client
    credentials for the public catalog. Refreshed automatically, one retry on a
    401, persisted to ~/.mod/spotify/auth.json.
  * **shape** — every track/artist/album/playlist/device comes back flattened
    (`name`, `artists` as a string, `uri`, `url`, `duration`), so a model reads
    one line instead of paging through Spotify's nested JSON.

Anything the normalized surface does not cover is reachable with `raw()`.

Nothing here imports `mod`: this file sits next to a mod.py that would shadow
the protocol package for anything importing it after us.
"""

import base64
import hashlib
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request

API = 'https://api.spotify.com/v1'
ACCOUNTS = 'https://accounts.spotify.com'
STATE_DIR = os.path.expanduser('~/.mod/spotify')
KEYS_PATH = os.path.join(STATE_DIR, 'keys.json')
AUTH_PATH = os.path.join(STATE_DIR, 'auth.json')
PKCE_PATH = os.path.join(STATE_DIR, 'pkce.json')

# Loopback literal IP, not "localhost" — Spotify stopped accepting the latter.
# Must match a redirect URI registered on the app in the developer dashboard.
DEFAULT_REDIRECT = 'http://127.0.0.1:8899/callback'

# What the player/library verbs need. Ask once, at login.
SCOPES = [
    'user-read-private',
    'user-read-playback-state',
    'user-modify-playback-state',
    'user-read-currently-playing',
    'user-read-recently-played',
    'user-top-read',
    'user-library-read',
    'user-library-modify',
    'playlist-read-private',
    'playlist-read-collaborative',
    'playlist-modify-private',
    'playlist-modify-public',
]

TIMEOUT = float(os.environ.get('SPOTIFY_TIMEOUT', 20))


class SpotifyError(Exception):
    """A Spotify (or credential) failure, carrying enough to act on it."""

    def __init__(self, message, status=400, hint=None, retry_after=None):
        super().__init__(message)
        self.message = str(message)
        self.status = int(status or 400)
        self.hint = hint
        self.retry_after = retry_after

    def dict(self):
        d = {'error': self.message, 'status': self.status}
        if self.hint:
            d['hint'] = self.hint
        if self.retry_after is not None:
            d['retry_after'] = self.retry_after
        return d


# ── small helpers ────────────────────────────────────────────────

def _read_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {} if default is None else default


def _write_json(path, data):
    """0600, off-tree: these files hold tokens."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(data, f, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return path


def _mmss(ms):
    try:
        s = int(ms) // 1000
    except (TypeError, ValueError):
        return None
    return f'{s // 60}:{s % 60:02d}'


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip('=')


ID_TYPES = ('track', 'album', 'artist', 'playlist', 'show', 'episode', 'user')


def parse_uri(value, kind='track'):
    """`spotify:track:ID`, an open.spotify.com URL or a bare ID → (type, id).

    Free text returns (None, None) — the caller decides whether to search.
    """
    s = str(value or '').strip()
    if not s:
        return None, None
    if s.startswith('spotify:'):
        parts = s.split(':')
        if len(parts) >= 3 and parts[1] in ID_TYPES:
            return parts[1], parts[-1]
        return None, None
    if 'open.spotify.com' in s:
        path = urllib.parse.urlparse(s).path.strip('/').split('/')
        # /intl-de/track/ID → drop any locale segment
        path = [p for p in path if p and not p.startswith('intl-')]
        if len(path) >= 2 and path[0] in ID_TYPES:
            return path[0], path[1].split('?')[0]
        return None, None
    # A bare base62 id is 22 chars; anything else is a search phrase.
    if len(s) == 22 and s.isalnum():
        return kind, s
    return None, None


def to_uri(value, kind='track'):
    """Best-effort `spotify:<type>:<id>` for a URI / URL / bare id, else None."""
    t, i = parse_uri(value, kind)
    return f'spotify:{t}:{i}' if t and i else None


# ── normalizers: Spotify's nested JSON → one flat line each ───────

def track(t):
    if not t:
        return None
    t = t.get('track') or t if isinstance(t, dict) and 'track' in t else t
    album = t.get('album') or {}
    return {
        'name': t.get('name'),
        'artists': ', '.join(a.get('name', '') for a in (t.get('artists') or [])),
        'album': album.get('name'),
        'duration': _mmss(t.get('duration_ms')),
        'duration_ms': t.get('duration_ms'),
        'uri': t.get('uri'),
        'id': t.get('id'),
        'url': (t.get('external_urls') or {}).get('spotify'),
        'popularity': t.get('popularity'),
        'explicit': t.get('explicit'),
        'type': t.get('type', 'track'),
    }


def artist(a):
    if not a:
        return None
    return {
        'name': a.get('name'),
        'genres': a.get('genres') or [],
        'followers': (a.get('followers') or {}).get('total'),
        'popularity': a.get('popularity'),
        'uri': a.get('uri'),
        'id': a.get('id'),
        'url': (a.get('external_urls') or {}).get('spotify'),
        'type': 'artist',
    }


def album(a):
    if not a:
        return None
    return {
        'name': a.get('name'),
        'artists': ', '.join(x.get('name', '') for x in (a.get('artists') or [])),
        'released': a.get('release_date'),
        'tracks': (a.get('tracks') or {}).get('total', a.get('total_tracks')),
        'uri': a.get('uri'),
        'id': a.get('id'),
        'url': (a.get('external_urls') or {}).get('spotify'),
        'type': 'album',
    }


def playlist(p):
    if not p:
        return None
    return {
        'name': p.get('name'),
        'owner': (p.get('owner') or {}).get('display_name'),
        'tracks': (p.get('tracks') or {}).get('total'),
        'public': p.get('public'),
        'collaborative': p.get('collaborative'),
        'description': p.get('description') or None,
        'uri': p.get('uri'),
        'id': p.get('id'),
        'url': (p.get('external_urls') or {}).get('spotify'),
        'type': 'playlist',
    }


def episode(e):
    if not e:
        return None
    return {
        'name': e.get('name'),
        'show': ((e.get('show') or {}).get('name')),
        'duration': _mmss(e.get('duration_ms')),
        'released': e.get('release_date'),
        'uri': e.get('uri'),
        'id': e.get('id'),
        'url': (e.get('external_urls') or {}).get('spotify'),
        'type': 'episode',
    }


def show(s):
    if not s:
        return None
    return {
        'name': s.get('name'),
        'publisher': s.get('publisher'),
        'episodes': s.get('total_episodes'),
        'uri': s.get('uri'),
        'id': s.get('id'),
        'url': (s.get('external_urls') or {}).get('spotify'),
        'type': 'show',
    }


def device(d):
    if not d:
        return None
    return {
        'name': d.get('name'),
        'kind': d.get('type'),
        'id': d.get('id'),
        'active': d.get('is_active'),
        'volume': d.get('volume_percent'),
        'restricted': d.get('is_restricted'),
    }


ITEM = {'track': track, 'artist': artist, 'album': album, 'playlist': playlist,
        'show': show, 'episode': episode}


def item(obj):
    """Normalize whatever kind of object Spotify handed back."""
    if not isinstance(obj, dict):
        return obj
    return ITEM.get(obj.get('type'), lambda x: x)(obj)


class Spotify:
    """The adapter. Every verb returns plain JSON-able data or raises SpotifyError."""

    def __init__(self, client_id=None, client_secret=None, redirect_uri=None,
                 token=None, keys_path=KEYS_PATH, auth_path=AUTH_PATH):
        self.keys_path, self.auth_path = keys_path, auth_path
        stored = _read_json(keys_path)
        self.client_id = (client_id or os.environ.get('SPOTIFY_CLIENT_ID')
                          or stored.get('client_id') or '')
        self.client_secret = (client_secret or os.environ.get('SPOTIFY_CLIENT_SECRET')
                              or stored.get('client_secret') or '')
        self.redirect_uri = (redirect_uri or os.environ.get('SPOTIFY_REDIRECT_URI')
                             or stored.get('redirect_uri') or DEFAULT_REDIRECT)
        # A caller-supplied bearer (per-request BYOK) is never persisted.
        self._token = token or os.environ.get('SPOTIFY_ACCESS_TOKEN') or None
        self._app_token = None  # client-credentials token, memory only

    # ── credentials ──────────────────────────────────────────────

    def set_key(self, client_id=None, client_secret=None, redirect_uri=None):
        """Store the app credentials in ~/.mod/spotify/keys.json (0600)."""
        keys = _read_json(self.keys_path)
        for k, v in (('client_id', client_id), ('client_secret', client_secret),
                     ('redirect_uri', redirect_uri)):
            if v:
                keys[k] = str(v).strip()
                setattr(self, k, keys[k])
        if not keys:
            raise SpotifyError('nothing to set — pass client_id and/or client_secret')
        _write_json(self.keys_path, keys)
        return self.status()

    def _need_client(self):
        if not self.client_id:
            raise SpotifyError(
                'no Spotify client_id configured',
                hint='create an app at https://developer.spotify.com/dashboard, then '
                     '`m spotify/set_key client_id=… client_secret=…` (or set '
                     '$SPOTIFY_CLIENT_ID / $SPOTIFY_CLIENT_SECRET)')
        return self.client_id

    def status(self):
        """Auth state — what is configured, who is logged in, never a secret."""
        auth = _read_json(self.auth_path)
        expires_at = auth.get('expires_at')
        out = {
            'client_id': (self.client_id[:6] + '…') if self.client_id else None,
            'client_secret': 'set' if self.client_secret else 'missing',
            'redirect_uri': self.redirect_uri,
            'logged_in': bool(auth.get('refresh_token') or self._token),
            'token_expires_in': int(expires_at - time.time()) if expires_at else None,
            'scopes': (auth.get('scope') or '').split() or None,
            'keystore': self.keys_path,
            'catalog_only': not auth.get('refresh_token'),
        }
        if out['logged_in']:
            try:
                me = self.me()
                out['user'] = {'name': me.get('name'), 'id': me.get('id'),
                               'product': me.get('product')}
            except SpotifyError as e:
                out['user_error'] = e.message
        return out

    # ── OAuth: authorization code + PKCE ─────────────────────────

    def authorize_url(self, scopes=None, redirect_uri=None, show_dialog=False):
        """Step 1: the URL to open. Stashes the PKCE verifier for `exchange`."""
        self._need_client()
        verifier = secrets.token_urlsafe(64)
        state = secrets.token_urlsafe(16)
        redirect = redirect_uri or self.redirect_uri
        scope = ' '.join(scopes.split() if isinstance(scopes, str) else (scopes or SCOPES))
        _write_json(PKCE_PATH, {'verifier': verifier, 'state': state,
                                'redirect_uri': redirect, 'scope': scope,
                                'created_at': int(time.time())})
        q = {
            'client_id': self.client_id,
            'response_type': 'code',
            'redirect_uri': redirect,
            'scope': scope,
            'state': state,
            'code_challenge_method': 'S256',
            'code_challenge': _b64url(hashlib.sha256(verifier.encode()).digest()),
        }
        if show_dialog:
            q['show_dialog'] = 'true'
        return {'url': f'{ACCOUNTS}/authorize?' + urllib.parse.urlencode(q),
                'redirect_uri': redirect,
                'scope': scope,
                'next': 'open the url, approve, then `m spotify/exchange code=<code>` '
                        '(the code is the ?code= param on the redirect)'}

    def exchange(self, code, state=None, redirect_uri=None):
        """Step 2: authorization code → tokens, persisted to auth.json."""
        self._need_client()
        pkce = _read_json(PKCE_PATH)
        if state and pkce.get('state') and state != pkce['state']:
            raise SpotifyError('state mismatch — restart with `m spotify/login`', 400)
        code = str(code or '').strip()
        if not code:
            raise SpotifyError('code is required')
        if 'code=' in code:  # someone pasted the whole redirect URL
            code = urllib.parse.parse_qs(urllib.parse.urlparse(code).query)['code'][0]
        body = {
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': redirect_uri or pkce.get('redirect_uri') or self.redirect_uri,
            'client_id': self.client_id,
        }
        if pkce.get('verifier'):
            body['code_verifier'] = pkce['verifier']
        tok = self._token_request(body)
        self._save_token(tok)
        try:
            os.remove(PKCE_PATH)
        except OSError:
            pass
        return self.status()

    def login(self, timeout=180, open_browser=False, scopes=None):
        """Both steps at once: serve the redirect on loopback and wait for it.

        Headless boxes should use `authorize_url` + `exchange` instead — this
        one only works where the redirect can reach this process.
        """
        from http.server import BaseHTTPRequestHandler, HTTPServer

        started = self.authorize_url(scopes=scopes)
        parsed = urllib.parse.urlparse(started['redirect_uri'])
        if parsed.hostname not in ('127.0.0.1', 'localhost', '::1'):
            raise SpotifyError(
                f"redirect_uri {started['redirect_uri']} is not loopback — use "
                '`m spotify/authorize_url` and `m spotify/exchange` instead', 400)
        captured = {}

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                captured.update({k: v[0] for k, v in q.items()})
                ok = 'code' in captured
                msg = (b'<h2>spotify: authorized</h2><p>Close this tab.</p>' if ok else
                       b'<h2>spotify: no code</h2><p>Check the module logs.</p>')
                self.send_response(200 if ok else 400)
                self.send_header('content-type', 'text/html')
                self.send_header('content-length', str(len(msg)))
                self.end_headers()
                self.wfile.write(msg)

            def log_message(self, *a):
                pass

        srv = HTTPServer((parsed.hostname, parsed.port or 80), Handler)
        srv.timeout = float(timeout)
        if open_browser:
            import webbrowser
            webbrowser.open(started['url'])
        else:
            print(f"open: {started['url']}", flush=True)
        srv.handle_request()
        srv.server_close()
        if 'code' not in captured:
            raise SpotifyError(captured.get('error') or f'no redirect within {timeout}s',
                               408, hint=f"open {started['url']} and approve")
        return self.exchange(captured['code'], captured.get('state'))

    def logout(self):
        """Forget the user tokens (the app credentials stay)."""
        existed = os.path.exists(self.auth_path)
        for p in (self.auth_path, PKCE_PATH):
            try:
                os.remove(p)
            except OSError:
                pass
        self._token = None
        return {'logged_out': existed, 'auth_path': self.auth_path}

    # ── tokens ───────────────────────────────────────────────────

    def _token_request(self, body):
        data = urllib.parse.urlencode(body).encode()
        headers = {'content-type': 'application/x-www-form-urlencoded'}
        if self.client_secret:
            basic = base64.b64encode(
                f'{self.client_id}:{self.client_secret}'.encode()).decode()
            headers['authorization'] = f'Basic {basic}'
        req = urllib.request.Request(f'{ACCOUNTS}/api/token', data=data,
                                     headers=headers, method='POST')
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return json.loads(r.read() or b'{}')
        except urllib.error.HTTPError as e:
            raw = e.read().decode(errors='replace')
            try:
                j = json.loads(raw)
                msg = j.get('error_description') or j.get('error') or raw
            except Exception:
                msg = raw
            raise SpotifyError(f'token request failed: {msg}', e.code,
                               hint='check client_id/client_secret and that the '
                                    'redirect_uri matches the dashboard exactly')
        except urllib.error.URLError as e:
            raise SpotifyError(f'cannot reach accounts.spotify.com: {e.reason}', 503)

    def _save_token(self, tok):
        auth = _read_json(self.auth_path)
        auth.update({k: v for k, v in tok.items() if k != 'expires_in'})
        auth['expires_at'] = int(time.time()) + int(tok.get('expires_in') or 3600)
        # update(), not replace: a refresh that omits refresh_token keeps the old one.
        _write_json(self.auth_path, auth)
        return auth

    def refresh(self):
        """Trade the refresh token for a new access token."""
        auth = _read_json(self.auth_path)
        rt = auth.get('refresh_token')
        if not rt:
            raise SpotifyError('not logged in — run `m spotify/login`', 401)
        self._need_client()
        tok = self._token_request({'grant_type': 'refresh_token', 'refresh_token': rt,
                                   'client_id': self.client_id})
        tok.setdefault('refresh_token', rt)
        self._save_token(tok)
        return {'refreshed': True, 'expires_in': tok.get('expires_in')}

    def _user_token(self):
        """A valid user access token, refreshed if it is about to expire."""
        if self._token:
            return self._token
        auth = _read_json(self.auth_path)
        if not auth.get('access_token'):
            raise SpotifyError(
                'not logged in', 401,
                hint='`m spotify/login` (loopback) or `m spotify/authorize_url` + '
                     '`m spotify/exchange code=…` on a headless box')
        if auth.get('expires_at', 0) - 60 < time.time() and auth.get('refresh_token'):
            self.refresh()
            auth = _read_json(self.auth_path)
        return auth['access_token']

    def app_token(self):
        """Client-credentials token: the public catalog, no user, no scopes."""
        if self._app_token and self._app_token[1] - 60 > time.time():
            return self._app_token[0]
        self._need_client()
        if not self.client_secret:
            raise SpotifyError('client_secret required for catalog-only access', 401,
                               hint='`m spotify/set_key client_secret=…`, or log in')
        tok = self._token_request({'grant_type': 'client_credentials',
                                   'client_id': self.client_id})
        self._app_token = (tok['access_token'],
                           time.time() + int(tok.get('expires_in') or 3600))
        return self._app_token[0]

    # ── the HTTP layer ───────────────────────────────────────────

    def request(self, method, path, params=None, body=None, user=True, _retry=True):
        """One Spotify Web API call. 204 → {}. 401 → refresh once and retry."""
        url = path if path.startswith('http') else API + '/' + path.lstrip('/')
        params = {k: v for k, v in (params or {}).items() if v not in (None, '')}
        if params:
            url += ('&' if '?' in url else '?') + urllib.parse.urlencode(params)
        token = self._user_token() if user else self.app_token()
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method.upper(), headers={
            'authorization': f'Bearer {token}',
            'content-type': 'application/json',
            'accept': 'application/json',
        })
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                raw = r.read()
                return json.loads(raw) if raw.strip() else {}
        except urllib.error.HTTPError as e:
            raw = e.read().decode(errors='replace')
            try:
                err = (json.loads(raw).get('error') or {})
                msg = err.get('message') if isinstance(err, dict) else str(err)
                reason = err.get('reason') if isinstance(err, dict) else None
            except Exception:
                msg, reason = raw[:400], None
            if e.code == 401 and user and _retry and not self._token:
                self.refresh()
                return self.request(method, path, params, body, user, _retry=False)
            raise SpotifyError(msg or f'HTTP {e.code}', e.code,
                               hint=self._hint(e.code, reason),
                               retry_after=(int(e.headers.get('retry-after'))
                                            if e.code == 429 and
                                            e.headers.get('retry-after') else None))
        except urllib.error.URLError as e:
            raise SpotifyError(f'cannot reach api.spotify.com: {e.reason}', 503)

    @staticmethod
    def _hint(code, reason=None):
        if reason == 'NO_ACTIVE_DEVICE' or code == 404:
            return ('no active device — open Spotify somewhere, then '
                    '`m spotify/devices` and `m spotify/transfer device=<name>`')
        if reason == 'PREMIUM_REQUIRED':
            return 'playback control is a Spotify Premium feature'
        if code == 403:
            return 'the token lacks the scope for this call — `m spotify/login` again'
        if code == 429:
            return 'rate limited — back off for retry_after seconds'
        return None

    def get(self, path, **params):
        return self.request('GET', path, params=params)

    def raw(self, path, method='GET', body=None, params=None, user=True):
        """Escape hatch: any Web API endpoint, verbatim, with your token."""
        return self.request(method, path, params=params, body=body, user=user)

    # ── identity & catalog ───────────────────────────────────────

    def me(self):
        u = self.get('/me')
        return {'name': u.get('display_name'), 'id': u.get('id'),
                'product': u.get('product'), 'country': u.get('country'),
                'followers': (u.get('followers') or {}).get('total'),
                'uri': u.get('uri'), 'url': (u.get('external_urls') or {}).get('spotify')}

    def search(self, q, type='track', limit=10, market=None, offset=0):
        """Search the catalog. type: track,artist,album,playlist,show,episode."""
        if not str(q or '').strip():
            raise SpotifyError('q (a search phrase) is required')
        # Spotify wants singular type names; callers say "tracks" half the time.
        types = ','.join(t.strip().rstrip('s') for t in str(type).split(',') if t.strip())
        params = {'q': q, 'type': types, 'limit': min(int(limit or 10), 50),
                  'offset': int(offset or 0), 'market': market}
        # Catalog search works with an app token too, so a logged-out module
        # that has a client_secret can still answer "what is this song".
        r = self.request('GET', '/search', params=params, user=self._can_user())
        out = {}
        for key, items in r.items():
            got = [item(x) for x in (items or {}).get('items') or [] if x]
            if got:
                out[key] = got
        return out or {'results': [], 'note': f'nothing matched {q!r}'}

    def _can_user(self):
        return bool(self._token or _read_json(self.auth_path).get('access_token'))

    def lookup(self, uri):
        """Any spotify: URI / open.spotify.com URL → the normalized object."""
        kind, id_ = parse_uri(uri)
        if not kind:
            raise SpotifyError(f'not a spotify uri/url/id: {uri!r}')
        plural = {'track': 'tracks', 'album': 'albums', 'artist': 'artists',
                  'playlist': 'playlists', 'show': 'shows', 'episode': 'episodes',
                  'user': 'users'}[kind]
        return item(self.request('GET', f'/{plural}/{id_}', user=self._can_user()))

    def resolve(self, value, kind='track'):
        """A URI, URL, id, or free text → a spotify URI (searching if needed)."""
        uri = to_uri(value, kind)
        if uri:
            return uri
        hits = self.search(value, type=kind, limit=1)
        for got in hits.values():
            if isinstance(got, list) and got and got[0].get('uri'):
                return got[0]['uri']
        raise SpotifyError(f'nothing on Spotify matched {value!r}', 404)

    # ── player ───────────────────────────────────────────────────

    def now_playing(self):
        """What is playing right now, or a quiet answer when nothing is."""
        st = self.get('/me/player')
        if not st:
            return {'playing': False, 'note': 'nothing is playing on any device'}
        cur = track(st.get('item')) if (st.get('item') or {}).get('type') == 'track' \
            else item(st.get('item'))
        return {
            'playing': bool(st.get('is_playing')),
            'item': cur,
            'progress': _mmss(st.get('progress_ms')),
            'progress_ms': st.get('progress_ms'),
            'device': device(st.get('device')),
            'shuffle': st.get('shuffle_state'),
            'repeat': st.get('repeat_state'),
            'context': (st.get('context') or {}).get('uri'),
        }

    def devices(self):
        ds = [device(d) for d in self.get('/me/player/devices').get('devices') or []]
        return {'devices': ds, 'active': next((d['name'] for d in ds if d['active']), None)}

    def _device_id(self, device_name=None):
        """Name, id, or None → a device_id the player endpoints accept."""
        if not device_name:
            return None
        want = str(device_name).strip()
        ds = self.devices()['devices']
        for d in ds:
            if d['id'] == want or (d['name'] or '').lower() == want.lower():
                return d['id']
        for d in ds:
            if want.lower() in (d['name'] or '').lower():
                return d['id']
        raise SpotifyError(f'no device matching {want!r}', 404,
                           hint='devices: ' + ', '.join(d['name'] for d in ds) or 'none')

    def play(self, query=None, uri=None, device=None, position_ms=None, shuffle=None):
        """Play a track/album/playlist/artist — by URI or by search phrase.

        No argument resumes whatever is loaded on the active device.
        """
        target = uri or query
        body = {}
        if target:
            resolved = to_uri(target) or self.resolve(target, 'track')
            kind = resolved.split(':')[1]
            if kind in ('album', 'playlist', 'artist', 'show'):
                body['context_uri'] = resolved
            else:
                body['uris'] = [resolved]
        if position_ms is not None:
            body['position_ms'] = int(position_ms)
        dev = self._device_id(device)
        if shuffle is not None:
            self.shuffle(shuffle, device=dev)
        self.request('PUT', '/me/player/play', params={'device_id': dev},
                     body=body or None)
        return self._after('play')

    def pause(self, device=None):
        self.request('PUT', '/me/player/pause',
                     params={'device_id': self._device_id(device)})
        return self._after('pause')

    def next(self, device=None):
        self.request('POST', '/me/player/next',
                     params={'device_id': self._device_id(device)})
        return self._after('next')

    def previous(self, device=None):
        self.request('POST', '/me/player/previous',
                     params={'device_id': self._device_id(device)})
        return self._after('previous')

    def seek(self, position_ms, device=None):
        self.request('PUT', '/me/player/seek',
                     params={'position_ms': int(position_ms),
                             'device_id': self._device_id(device)})
        return self._after('seek')

    def volume(self, percent, device=None):
        pct = max(0, min(100, int(percent)))
        self.request('PUT', '/me/player/volume',
                     params={'volume_percent': pct,
                             'device_id': self._device_id(device)})
        return self._after(f'volume {pct}%')

    def shuffle(self, state=True, device=None):
        on = str(state).lower() not in ('0', 'false', 'off', 'no')
        self.request('PUT', '/me/player/shuffle',
                     params={'state': str(on).lower(),
                             'device_id': self._device_id(device)})
        return {'ok': True, 'shuffle': on}

    def repeat(self, state='context', device=None):
        s = str(state).lower()
        if s in ('1', 'true', 'on'):
            s = 'context'
        if s in ('0', 'false'):
            s = 'off'
        if s not in ('track', 'context', 'off'):
            raise SpotifyError("repeat state must be track, context or off")
        self.request('PUT', '/me/player/repeat',
                     params={'state': s, 'device_id': self._device_id(device)})
        return {'ok': True, 'repeat': s}

    def transfer(self, device, play=True):
        """Move playback to another device (by name or id)."""
        dev = self._device_id(device)
        self.request('PUT', '/me/player', body={'device_ids': [dev], 'play': bool(play)})
        return self._after(f'transfer → {device}')

    def queue(self, query=None, uri=None, device=None):
        """Add one track/episode to the end of the queue."""
        target = uri or query
        if not target:
            raise SpotifyError('pass a uri or a search phrase to queue')
        resolved = to_uri(target) or self.resolve(target, 'track')
        self.request('POST', '/me/player/queue',
                     params={'uri': resolved, 'device_id': self._device_id(device)})
        return {'ok': True, 'queued': self.lookup(resolved)}

    def up_next(self, limit=10):
        """What is queued after the current item."""
        q = self.get('/me/player/queue')
        return {'now': item(q.get('currently_playing')),
                'next': [item(x) for x in (q.get('queue') or [])[:int(limit)]]}

    def _after(self, action):
        """Player writes return 204 — say what the state became, not just 'ok'."""
        try:
            state = self.now_playing()
        except SpotifyError:
            return {'ok': True, 'action': action}
        return {'ok': True, 'action': action, 'playing': state.get('playing'),
                'item': state.get('item'), 'device': (state.get('device') or {}).get('name')}

    # ── library ──────────────────────────────────────────────────

    def recent(self, limit=20):
        r = self.get('/me/player/recently-played', limit=min(int(limit or 20), 50))
        return {'recent': [{'played_at': x.get('played_at'), **(track(x.get('track')) or {})}
                           for x in r.get('items') or []]}

    def top(self, type='tracks', time_range='medium_term', limit=20):
        t = 'artists' if str(type).startswith('artist') else 'tracks'
        if time_range not in ('short_term', 'medium_term', 'long_term'):
            raise SpotifyError('time_range must be short_term (4 weeks), '
                               'medium_term (6 months) or long_term (years)')
        r = self.get(f'/me/top/{t}', time_range=time_range,
                     limit=min(int(limit or 20), 50))
        return {'type': t, 'time_range': time_range,
                'items': [item(x) for x in r.get('items') or []]}

    def saved(self, limit=20, offset=0):
        r = self.get('/me/tracks', limit=min(int(limit or 20), 50), offset=int(offset or 0))
        return {'total': r.get('total'),
                'tracks': [{'added_at': x.get('added_at'), **(track(x.get('track')) or {})}
                           for x in r.get('items') or []]}

    def save(self, query=None, uri=None, remove=False):
        """Save (or with remove=1, unsave) a track in Your Library."""
        resolved = to_uri(uri or query) or self.resolve(uri or query, 'track')
        id_ = resolved.split(':')[-1]
        self.request('DELETE' if remove else 'PUT', '/me/tracks', body={'ids': [id_]})
        return {'ok': True, 'saved': not remove, 'track': self.lookup(resolved)}

    # ── playlists ────────────────────────────────────────────────

    def playlists(self, limit=50, offset=0):
        r = self.get('/me/playlists', limit=min(int(limit or 50), 50),
                     offset=int(offset or 0))
        return {'total': r.get('total'),
                'playlists': [playlist(p) for p in r.get('items') or [] if p]}

    def playlist(self, id, limit=100, tracks=True):
        """One playlist, with its first `limit` tracks."""
        pid = (to_uri(id, 'playlist') or '').split(':')[-1] or str(id)
        p = playlist(self.request('GET', f'/playlists/{pid}', user=self._can_user()))
        if tracks:
            r = self.get(f'/playlists/{pid}/tracks', limit=min(int(limit or 100), 100))
            p['items'] = [item(x.get('track')) for x in r.get('items') or []
                          if x and x.get('track')]
        return p

    def playlist_create(self, name, public=False, description=None, uris=None):
        """Create a playlist on your account, optionally filled in one call."""
        if not str(name or '').strip():
            raise SpotifyError('name is required')
        uid = self.get('/me')['id']
        p = self.request('POST', f'/users/{uid}/playlists', body={
            'name': name, 'public': bool(public),
            'description': description or ''})
        out = playlist(p)
        if uris:
            out['added'] = self.playlist_add(p['id'], uris)['added']
        return out

    def playlist_add(self, id, uris, position=None):
        """Add tracks — each entry may be a URI, URL, id or a search phrase."""
        pid = (to_uri(id, 'playlist') or '').split(':')[-1] or str(id)
        items = uris if isinstance(uris, list) else \
            [u for u in str(uris).split(',') if u.strip()]
        resolved = [self.resolve(u.strip(), 'track') for u in items]
        if not resolved:
            raise SpotifyError('nothing to add')
        body = {'uris': resolved}
        if position is not None:
            body['position'] = int(position)
        # 100 per request is the API's cap.
        for i in range(0, len(resolved), 100):
            self.request('POST', f'/playlists/{pid}/tracks',
                         body={**body, 'uris': resolved[i:i + 100]})
        return {'ok': True, 'playlist': pid, 'added': resolved}

    def playlist_remove(self, id, uris):
        pid = (to_uri(id, 'playlist') or '').split(':')[-1] or str(id)
        items = uris if isinstance(uris, list) else \
            [u for u in str(uris).split(',') if u.strip()]
        resolved = [self.resolve(u.strip(), 'track') for u in items]
        self.request('DELETE', f'/playlists/{pid}/tracks',
                     body={'tracks': [{'uri': u} for u in resolved]})
        return {'ok': True, 'playlist': pid, 'removed': resolved}
