"""schemes - the token schemes the fleet accepts, and how each is verified.

This file IS the protocol's verification logic. The server and every client
fallback load it by path and re-load it when its mtime changes, so an edit
here (a new scheme, a tighter max age) is live on the next request across the
whole fleet — nothing restarts. Keep it stdlib + eth_account, no `mod` import
at module level: it must load in any process.

    verify(token, proto) -> {"address", "scheme", "time"}   raises ValueError

`proto` is the parsed protocol.json; a scheme that is missing or has
`enabled: false` there is refused.
"""
import base64
import importlib.util
import json
import os
import sys
import re
import time
import urllib.request

BUILD_RE = re.compile(r'^[^:\s]{3,}:\d{9,12}(:ho)?:[0-9a-f]{64}$')


def _b64url_json(token: str) -> dict:
    pad = '=' * (-len(token) % 4)
    return json.loads(base64.urlsafe_b64decode(token + pad))


def detect(token: str) -> str:
    """Which scheme a token claims to be, by shape alone."""
    token = (token or '').strip()
    if token.lower().startswith('bearer '):
        token = token[7:].strip()
    if BUILD_RE.match(token):
        return 'build'
    try:
        env = _b64url_json(token)
        if isinstance(env, dict) and {'time', 'key', 'signature'} <= set(env):
            return 'mod'
    except Exception:
        pass
    return ''


def _enabled(proto: dict, scheme: str) -> dict:
    cfg = (proto.get('schemes') or {}).get(scheme)
    if not cfg or not cfg.get('enabled', True):
        raise ValueError(f'scheme {scheme!r} is not accepted by this protocol')
    return cfg


def _framework():
    """The mod framework package — NOT whichever mod.py sits first on
    sys.path (every module ships one; passport's own server would import
    itself). None when the framework isn't installed."""
    m = sys.modules.get('mod')
    if m is not None:
        return m if hasattr(m, 'mod') else None
    # hide the shadowing dirs for the lookup; the framework is often an
    # editable install found by a meta-path finder, not by sys.path
    saved = list(sys.path)
    sys.path[:] = [p for p in saved if not os.path.isfile(os.path.join(p or '.', 'mod.py'))]
    try:
        spec = importlib.util.find_spec('mod')
    finally:
        sys.path[:] = saved
    if not spec or not spec.submodule_search_locations:
        return None
    m = importlib.util.module_from_spec(spec)
    sys.modules['mod'] = m
    try:
        spec.loader.exec_module(m)
    except Exception:
        sys.modules.pop('mod', None)
        return None
    return m if hasattr(m, 'mod') else None


# ── mod: wallet/key-signed token ─────────────────────────────────────

def _messages(env: dict):
    """The exact strings a signer may have signed. JS JSON.stringify keeps
    non-ASCII raw where Python's json.dumps escapes it, so try both."""
    body = {'data': env.get('data'), 'time': env['time']}
    seen = []
    for ascii_ in (False, True):
        s = json.dumps(body, separators=(',', ':'), ensure_ascii=ascii_)
        if s not in seen:
            seen.append(s)
    return seen


def _recover_eip191(msg: str, sig: str) -> str:
    from eth_account import Account
    from eth_account.messages import encode_defunct
    return Account.recover_message(encode_defunct(text=msg), signature=sig)


def verify_mod(token: str, cfg: dict) -> dict:
    env = _b64url_json(token)
    key = str(env.get('key') or '')
    sig = str(env.get('signature') or '')
    try:
        t = float(env['time'])
    except Exception:
        raise ValueError('token time is not a number')
    max_age = float(cfg.get('max_age', 86400))
    if abs(time.time() - t) > max_age:
        raise ValueError(f'token is stale (older than {int(max_age)}s)')
    if key.startswith('0x') and len(key) == 42:
        for msg in _messages(env):
            try:
                if _recover_eip191(msg, sig).lower() == key.lower():
                    return {'address': key.lower(), 'scheme': 'mod', 'time': t}
            except Exception:
                pass
    # non-EIP-191 signatures (a server key's raw .sign, sr25519, ed25519…)
    # are the core Auth's business — delegate rather than re-implement
    m = _framework()
    if m is None:
        raise ValueError('invalid signature')
    try:
        h = m.mod('auth')(max_age=max_age).verify(token)
        addr = str(h['key'])
        return {'address': addr.lower() if addr.startswith('0x') else addr,
                'scheme': 'mod', 'time': t}
    except Exception as e:
        raise ValueError(f'invalid signature ({str(e)[:80]})')


# ── build: build console session bearer ──────────────────────────────

def _post_json(url: str, body: dict, headers: dict, timeout: float = 4.0) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={'content-type': 'application/json', **headers})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b'{}')


def verify_build(token: str, cfg: dict) -> dict:
    """Only build holds the HMAC secret, so build is asked — via its MCP
    whoami, which validates the bearer exactly as every build route does
    (expiry, revocation, guest grants)."""
    url = cfg.get('verify_url') or 'http://127.0.0.1:8890/mcp'
    out = _post_json(url, {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                           'params': {'name': 'whoami', 'arguments': {}}},
                     {'Authorization': f'Bearer {token}'})
    sc = ((out.get('result') or {}).get('structuredContent')) or {}
    addr = sc.get('address')
    if not addr:
        raise ValueError(f"build refused the session ({sc.get('why') or 'no address'})")
    return {'address': str(addr).lower(), 'scheme': 'build',
            'time': float(token.split(':')[1]), 'build_role': sc.get('role')}


VERIFIERS = {'mod': verify_mod, 'build': verify_build}


def verify(token: str, proto: dict) -> dict:
    token = (token or '').strip()
    if token.lower().startswith('bearer '):
        token = token[7:].strip()
    if not token:
        raise ValueError('no token')
    scheme = detect(token)
    if not scheme:
        raise ValueError('unrecognised token format')
    return VERIFIERS[scheme](token, _enabled(proto, scheme))
