"""ytmod.auth — Google OAuth 2.0 for YouTube, stdlib only.

One Google Cloud OAuth client (type "Web application" or "Desktop") signs in
any number of Google accounts. Each account's refresh token lives in
~/.mod/youtube/accounts.json and is turned into a short-lived access token
on demand; `token_for()` is the one function the API layer calls.

Flow (authorization code, loopback or gateway redirect):

    start(redirect_uri)  -> {url, state}     send the browser there
    finish(code, state)  -> account record    the callback exchanges the code,
                                              asks YouTube who the channel is,
                                              stores and activates the account

Scopes asked by default cover the user side (youtube.force-ssl: rate,
subscribe, comment, playlists), the creator side (youtube.upload) and
Analytics (yt-analytics.readonly), plus openid/email so the account list
can show which Google account a channel belongs to.
"""
from __future__ import annotations

import json
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import store

AUTH_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
TOKEN_URL = 'https://oauth2.googleapis.com/token'
REVOKE_URL = 'https://oauth2.googleapis.com/revoke'
USERINFO_URL = 'https://openidconnect.googleapis.com/v1/userinfo'

SCOPES = {
    'youtube': 'https://www.googleapis.com/auth/youtube.force-ssl',
    'upload': 'https://www.googleapis.com/auth/youtube.upload',
    'analytics': 'https://www.googleapis.com/auth/yt-analytics.readonly',
    'readonly': 'https://www.googleapis.com/auth/youtube.readonly',
}
DEFAULT_SCOPES = ['openid', 'email', SCOPES['youtube'], SCOPES['upload'], SCOPES['analytics']]

_pending: dict[str, dict] = {}       # state -> {redirect_uri, scopes, ts}
_lock = threading.Lock()
PENDING_TTL = 900


class AuthError(Exception):
    pass


class NeedAccount(AuthError):
    """No Google account is signed in (or the one asked for is unknown)."""


def _post_form(url: str, data: dict) -> dict:
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, method='POST',
                                 headers={'Content-Type': 'application/x-www-form-urlencoded'})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read() or b'{}')
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read())
        except Exception:
            err = {'error': f'http {e.code}'}
        raise AuthError(f"{err.get('error')}: {err.get('error_description', '')}".strip(': '))


def _client() -> tuple[str, str]:
    c = store.client()
    cid, sec = c.get('client_id'), c.get('client_secret')
    if not (cid and sec):
        raise AuthError('no Google OAuth client yet: m youtube/set_client client_id=... client_secret=... '
                        '(Google Cloud console -> APIs & Services -> Credentials; enable YouTube Data API v3)')
    return cid, sec


def start(redirect_uri: str, scopes: list[str] | None = None, login_hint: str = '') -> dict:
    """Build the consent URL. `prompt=consent select_account` so every sign-in
    offers an account picker and returns a refresh token."""
    cid, _ = _client()
    scopes = scopes or DEFAULT_SCOPES
    state = secrets.token_urlsafe(24)
    with _lock:
        now = time.time()
        for k in [k for k, v in _pending.items() if now - v['ts'] > PENDING_TTL]:
            _pending.pop(k, None)
        _pending[state] = {'redirect_uri': redirect_uri, 'scopes': scopes, 'ts': now}
    params = {
        'client_id': cid, 'redirect_uri': redirect_uri, 'response_type': 'code',
        'scope': ' '.join(scopes), 'access_type': 'offline', 'include_granted_scopes': 'true',
        'prompt': 'consent select_account', 'state': state,
    }
    if login_hint:
        params['login_hint'] = login_hint
    return {'url': AUTH_URL + '?' + urllib.parse.urlencode(params), 'state': state,
            'redirect_uri': redirect_uri}


def finish(code: str, state: str) -> dict:
    """Callback half: code -> tokens -> who is this -> stored, active."""
    with _lock:
        pend = _pending.pop(state, None)
    if pend is None:
        raise AuthError('unknown or expired OAuth state; start the sign-in again')
    cid, sec = _client()
    tok = _post_form(TOKEN_URL, {'code': code, 'client_id': cid, 'client_secret': sec,
                                 'redirect_uri': pend['redirect_uri'], 'grant_type': 'authorization_code'})
    access = tok.get('access_token')
    if not access:
        raise AuthError('token exchange returned no access_token')
    rec = {'access_token': access, 'refresh_token': tok.get('refresh_token'),
           'expires_at': time.time() + float(tok.get('expires_in', 3600)) - 60,
           'scopes': (tok.get('scope') or ' '.join(pend['scopes'])).split()}
    rec.update(_identify(access))
    store.upsert(rec, make_active=True)
    return {k: rec.get(k) for k in ('id', 'email', 'channel_id', 'title', 'handle', 'thumbnail', 'scopes')}


def _identify(access: str) -> dict:
    """email from OpenID, channel from YouTube. The account id is the channel
    id when there is one (a Google account may own several brand channels and
    each signs in as its own account here), else the OpenID subject."""
    out: dict = {}
    hdr = {'Authorization': f'Bearer {access}'}
    try:
        with urllib.request.urlopen(urllib.request.Request(USERINFO_URL, headers=hdr), timeout=15) as r:
            ui = json.loads(r.read())
        out['email'] = ui.get('email')
        out['sub'] = ui.get('sub')
    except Exception:
        pass
    q = urllib.parse.urlencode({'part': 'snippet,contentDetails,statistics', 'mine': 'true'})
    try:
        with urllib.request.urlopen(urllib.request.Request(
                'https://www.googleapis.com/youtube/v3/channels?' + q, headers=hdr), timeout=15) as r:
            ch = json.loads(r.read())
        items = ch.get('items') or []
        if items:
            c = items[0]
            sn = c.get('snippet', {})
            out.update({'channel_id': c['id'], 'title': sn.get('title'),
                        'handle': sn.get('customUrl'),
                        'thumbnail': ((sn.get('thumbnails') or {}).get('default') or {}).get('url'),
                        'uploads': ((c.get('contentDetails') or {}).get('relatedPlaylists') or {}).get('uploads')})
    except urllib.error.HTTPError as e:
        out['channel_error'] = f'http {e.code}: {e.read()[:200].decode(errors="replace")}'
    except Exception as e:
        out['channel_error'] = str(e)
    out['id'] = out.get('channel_id') or out.get('sub') or secrets.token_hex(6)
    if not out.get('title'):
        out['title'] = out.get('email') or out['id']
    return out


def refresh(rec: dict) -> dict:
    cid, sec = _client()
    rt = rec.get('refresh_token')
    if not rt:
        raise NeedAccount(f"account {rec.get('title') or rec.get('id')} has no refresh token; sign it in again")
    tok = _post_form(TOKEN_URL, {'refresh_token': rt, 'client_id': cid, 'client_secret': sec,
                                 'grant_type': 'refresh_token'})
    rec['access_token'] = tok['access_token']
    rec['expires_at'] = time.time() + float(tok.get('expires_in', 3600)) - 60
    if tok.get('scope'):
        rec['scopes'] = tok['scope'].split()
    store.upsert(rec, make_active=False)
    return rec


def token_for(account_id: str | None = None) -> tuple[str, dict]:
    """(access_token, account record) for the account (default: active), refreshed if stale."""
    rec = store.get(account_id)
    if rec is None:
        if account_id:
            raise NeedAccount(f'no account {account_id!r}; yt_accounts lists the signed-in ones')
        raise NeedAccount('no Google account is signed in: open the app and press SIGN IN, '
                          'or m youtube/login')
    if not rec.get('access_token') or float(rec.get('expires_at') or 0) < time.time():
        rec = refresh(rec)
    return rec['access_token'], rec


def revoke(account_id: str) -> dict:
    rec = store.get(account_id)
    if rec is None:
        raise NeedAccount(f'no account {account_id!r}')
    tok = rec.get('refresh_token') or rec.get('access_token')
    revoked = False
    if tok:
        try:
            _post_form(REVOKE_URL, {'token': tok})
            revoked = True
        except AuthError:
            pass
    out = store.remove(rec['id'])
    out['revoked_at_google'] = revoked
    return out
