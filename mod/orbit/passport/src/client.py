"""client - how a module asks "who is this caller?" without owning the answer.

    import importlib.util, os
    spec = importlib.util.spec_from_file_location(
        'passport_client', os.path.expanduser('~/mod/mod/orbit/passport/src/client.py'))
    # …or simply: from passport.src.client import address  (with orbit on sys.path)

    address(token)            -> '0x…' | None
    verify(token)             -> {'address', 'scheme', 'time'}   raises ValueError
    role(token, mod='agent')  -> {'address', 'is_owner', …}

It asks the passport service first (one place to change the protocol), and
when the service is down it verifies in-process with the same hot-reloading
Passport — so a module never loses auth because passport restarted, and still
never carries a frozen copy of the rules.
"""
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
URL = os.environ.get('PASSPORT_URL', 'http://127.0.0.1:51390')
_cache = {}
_local = None
_down_until = 0.0


def _local_passport():
    global _local
    if _local is None:
        if HERE not in sys.path:
            sys.path.insert(0, HERE)
        from passport import Passport
        _local = Passport()
    return _local


def _ask(path: str, params: dict) -> dict:
    """GET the service. Raises ConnectionError when it is unreachable."""
    global _down_until
    if time.time() < _down_until:
        raise ConnectionError('passport down (backing off)')
    url = f'{URL}{path}?{urllib.parse.urlencode(params)}'
    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read())
        except Exception:
            return {'error': f'passport http {e.code}'}
    except Exception as e:
        _down_until = time.time() + 15
        raise ConnectionError(str(e))


def verify(token: str) -> dict:
    token = (token or '').strip()
    if not token:
        raise ValueError('no token')
    k = hashlib.sha256(token.encode()).hexdigest()
    hit = _cache.get(k)
    if hit and hit[0] > time.time():
        return dict(hit[1])
    try:
        out = _ask('/verify', {'token': token})
    except ConnectionError:
        return _local_passport().verify(token)
    if not out.get('address'):
        raise ValueError(out.get('error') or 'not verified')
    _cache[k] = (time.time() + 30, out)
    if len(_cache) > 2000:
        _cache.clear()
    return dict(out)


def address(token: str):
    try:
        return verify(token)['address']
    except Exception:
        return None


def role(token: str, mod: str = None) -> dict:
    try:
        return _ask('/role', {'token': token or '', 'mod': mod or ''})
    except ConnectionError:
        return _local_passport().role(token, mod)
