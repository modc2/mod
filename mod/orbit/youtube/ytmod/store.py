"""ytmod.store — the module's private state, all under ~/.mod/youtube/ (0600).

    client.json     {client_id, client_secret, api_key?, redirect_uri?}   Google Cloud OAuth client
    accounts.json   {active, accounts: {id: {...tokens, channel...}}}      every signed-in Google account
    home.json       [channel_id, ...]                                     the keyless home feed's channels

Nothing here goes in config.json: these tokens can act as the user on YouTube.
YOUTUBE_HOME moves the directory (tests point it at a tmp dir).
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

_lock = threading.RLock()


def home() -> Path:
    return Path(os.environ.get('YOUTUBE_HOME', '~/.mod/youtube')).expanduser()


def _read(name: str, default):
    p = home() / name
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return default


def _write(name: str, data) -> None:
    home().mkdir(parents=True, exist_ok=True)
    p = home() / name
    tmp = p.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    os.chmod(tmp, 0o600)
    tmp.replace(p)


# ── OAuth client (the app's own identity at Google) ──

def client() -> dict:
    return _read('client.json', {})


def set_client(client_id: str = '', client_secret: str = '', api_key: str = '', redirect_uri: str = '') -> dict:
    with _lock:
        c = client()
        if client_id:
            c['client_id'] = client_id.strip()
        if client_secret:
            c['client_secret'] = client_secret.strip()
        if api_key:
            c['api_key'] = api_key.strip()
        if redirect_uri:
            c['redirect_uri'] = redirect_uri.strip()
        _write('client.json', c)
        return client_status()


def client_status() -> dict:
    c = client()
    return {'oauth_client': bool(c.get('client_id') and c.get('client_secret')),
            'client_id': (c.get('client_id') or '')[:12] + ('…' if c.get('client_id') else ''),
            'api_key': bool(c.get('api_key')),
            'redirect_uri': c.get('redirect_uri') or None,
            'path': str(home() / 'client.json')}


def api_key() -> str | None:
    return client().get('api_key') or None


# ── accounts ──

def _accounts_doc() -> dict:
    d = _read('accounts.json', {})
    d.setdefault('active', None)
    d.setdefault('accounts', {})
    return d


def accounts() -> list[dict]:
    """Public view: no tokens."""
    d = _accounts_doc()
    out = []
    for aid, a in d['accounts'].items():
        out.append({k: a.get(k) for k in ('id', 'email', 'channel_id', 'title', 'handle', 'thumbnail',
                                          'scopes', 'added', 'expires_at')}
                   | {'active': aid == d['active']})
    out.sort(key=lambda a: (not a['active'], a.get('added') or 0))
    return out


def get(account_id: str | None = None) -> dict | None:
    """The full record (tokens included). None id = the active account."""
    d = _accounts_doc()
    aid = account_id or d['active']
    if not aid:
        return None
    a = d['accounts'].get(aid)
    if a is None:
        # allow lookup by email / handle / title
        for rec in d['accounts'].values():
            if account_id in (rec.get('email'), rec.get('handle'), rec.get('title')):
                return rec
    return a


def active_id() -> str | None:
    return _accounts_doc()['active']


def set_active(account_id: str) -> dict:
    with _lock:
        d = _accounts_doc()
        rec = get(account_id)
        if rec is None:
            raise KeyError(f'no account {account_id!r}')
        d['active'] = rec['id']
        _write('accounts.json', d)
        return {'active': rec['id'], 'title': rec.get('title'), 'email': rec.get('email')}


def upsert(rec: dict, make_active: bool = True) -> dict:
    with _lock:
        d = _accounts_doc()
        aid = rec['id']
        old = d['accounts'].get(aid, {})
        # a re-consent without a new refresh token keeps the old one
        if not rec.get('refresh_token') and old.get('refresh_token'):
            rec['refresh_token'] = old['refresh_token']
        rec.setdefault('added', old.get('added') or int(time.time()))
        d['accounts'][aid] = {**old, **rec}
        if make_active or not d['active']:
            d['active'] = aid
        _write('accounts.json', d)
        return d['accounts'][aid]


def remove(account_id: str) -> dict:
    with _lock:
        d = _accounts_doc()
        rec = get(account_id)
        if rec is None:
            raise KeyError(f'no account {account_id!r}')
        d['accounts'].pop(rec['id'], None)
        if d['active'] == rec['id']:
            d['active'] = next(iter(d['accounts']), None)
        _write('accounts.json', d)
        return {'removed': rec['id'], 'active': d['active']}


# ── keyless home feed channels ──

DEFAULT_HOME = [
    'UCX6OQ3DkcsbYNE6H8uQQuVA',   # MrBeast
    'UCsXVk37bltHxD1rDPwtNM8Q',   # Kurzgesagt
    'UCHnyfMqiRRG1u-2MsSQLbXA',   # Veritasium
    'UCLA_DiR1FfKNvjuUpBHmylQ',   # NASA
    'UCAuUUnT6oDeKwE6v1NGQxug',   # TED
    'UCBJycsmduvYEL83R_U4JriQ',   # MKBHD
    'UCXuqSBlHAE6Xw-yeJA0Tunw',   # Linus Tech Tips
    'UCYO_jab_esuFRV4b17AJtAw',   # 3Blue1Brown
]


def home_channels() -> list[str]:
    v = _read('home.json', None)
    return v if isinstance(v, list) and v else list(DEFAULT_HOME)


def set_home_channels(ids: list[str]) -> list[str]:
    _write('home.json', [str(i) for i in ids])
    return home_channels()
