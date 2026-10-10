#!/usr/bin/env python3
"""postquant ↔ liquidai — the chat brain's model, stdlib only.

The liquidai module (the fleet's one interface to Liquid AI's LFM models)
runs on this box with a model resident in its server runtime. This bridge is
everything postquant needs from it:

    ready()    is there a liquidai with a working server runtime right now?
    stream()   POST /chat and yield its SSE events (start, token, done, error)

AUTH, WITHOUT A WALLET
    liquidai gates /chat behind a session token. Its own rule for a local
    shell is "reading ~/.mod/liquidai/server.secret already means being the
    operator", expressed as auth.mint_local(). This module borrows exactly
    that: it loads liquidai's auth.py BY FILE PATH and mints a short-lived
    local token. File path on purpose — `from api import auth` inside this
    process would resolve to postquant's own api.py (liquidai's mod.py dodges
    the identical shadow the identical way). No key is copied, nothing is
    stored: if the secret or the file is gone, ready() is simply False and
    the agent falls back to its rules brain.

    POSTQUANT_LIQUIDAI            base url (default http://127.0.0.1:50460,
                                  set empty to disable the chat brain)
    POSTQUANT_LIQUIDAI_AUTH       path to liquidai's auth.py (defaults to the
                                  sibling module in this orbit)
    POSTQUANT_CHAT_MODEL          model to ask for (default LFM2.5-1.2B-Instruct)
    POSTQUANT_CHAT_MAX_TOKENS     answer budget (default 280 — the model is
                                  local and unhurried; short answers stream well)
"""

import importlib.util
import json
import os
import threading
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
URL = os.environ.get('POSTQUANT_LIQUIDAI', 'http://127.0.0.1:50460').rstrip('/')
AUTH_FILE = os.environ.get('POSTQUANT_LIQUIDAI_AUTH', os.path.join(
    os.path.dirname(HERE), 'liquidai', 'src', 'api', 'auth.py'))
MODEL = os.environ.get('POSTQUANT_CHAT_MODEL', 'LiquidAI/LFM2.5-1.2B-Instruct')
MAX_TOKENS = int(os.environ.get('POSTQUANT_CHAT_MAX_TOKENS', 280))
TIMEOUT = float(os.environ.get('POSTQUANT_CHAT_TIMEOUT', 180))

_lock = threading.Lock()
_token = {'value': None, 'exp': 0.0}
_health = {'at': 0.0, 'out': None}


def enabled():
    return bool(URL)


def _auth_module():
    spec = importlib.util.spec_from_file_location('liquidai_auth', AUTH_FILE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def token():
    """A liquidai session token for this box, minted lazily and reused."""
    now = time.time()
    with _lock:
        if _token['value'] and _token['exp'] > now + 120:
            return _token['value']
    t = _auth_module().mint_local()
    with _lock:
        _token.update(value=t, exp=now + 3600)
    return t


def health(max_age=20):
    """GET /health, cached — brains() is asked on every GET /agents."""
    now = time.time()
    with _lock:
        if now - _health['at'] < max_age:
            return _health['out']
    out = None
    if URL:
        try:
            with urllib.request.urlopen(URL + '/health', timeout=2) as r:
                out = json.loads(r.read())
        except Exception:                               # noqa: BLE001
            out = None
    with _lock:
        _health.update(at=now, out=out)
    return out


def ready():
    if not enabled() or not os.path.exists(AUTH_FILE):
        return False
    h = health()
    return bool(h and h.get('ok') and h.get('server_runtime'))


def stream(messages, model=None, max_tokens=None, temperature=0.3):
    """POST /chat and yield each SSE event as a dict.

    The caller sees exactly liquidai's event shapes: {'type': 'start'|'token'
    |'done'|'error', ...}. Errors before the stream opens raise."""
    body = json.dumps({'model': model or MODEL, 'runtime': 'server',
                       'messages': messages,
                       'max_tokens': max_tokens or MAX_TOKENS,
                       'temperature': temperature}).encode()
    req = urllib.request.Request(URL + '/chat', body, {
        'content-type': 'application/json',
        'authorization': 'Bearer ' + token()})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        for raw in r:
            line = raw.strip()
            if not line.startswith(b'data: '):
                continue
            try:
                yield json.loads(line[6:])
            except json.JSONDecodeError:
                continue


if __name__ == '__main__':
    import sys
    print('ready:', ready(), '·', URL, '·', MODEL)
    q = ' '.join(sys.argv[1:])
    if q:
        for ev in stream([{'role': 'user', 'content': q}]):
            if ev.get('type') == 'token':
                print(ev['text'], end='', flush=True)
        print()
