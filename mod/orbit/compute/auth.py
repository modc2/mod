"""Who may spend your money, and who may run commands on your boxes.

Searching every market is a public act — it reads catalogs, spends nothing and
reveals nothing about you, so it stays open. Everything past that point is
not: renting spends the operator's credits, `instances` reveals what they are
already paying for, and a node route runs a shell on a machine they own. When
this module is mounted at modc2.com/compute, those must not be a URL away.

Three tiers, applied in `guard`:

    open    info, health, providers, search, offer, quote, mods, tools, and
            the /oracle prediction game (a player's own key signs each call)
    byok    instances, status, logs, balance, keys — allowed if the caller
            brought their own provider key, because then it is their account
            being read, not the operator's
    owner   rent, stop, exec, raw, set_key, and every /node route

A request counts as the owner's if it carries the module's secret as a bearer
token, or if it came from this machine and did not pass through the gateway —
Caddy stamps X-Forwarded-For on everything it proxies, so a public request can
never look loopback, while `curl localhost:50510` still just works.

It also counts if the bearer is the deployment owner's own sign-in: the build
console's session token (it embeds this console and hands its session down),
or a wallet-signed mod-protocol token. Either way build decides who the owner
is — its `whoami` for a session token, its `/auth/role` for a wallet address —
so signing in to the console once is signing in here.
"""

import contextlib
import hashlib
import hmac
import io
import json
import os
import secrets
import sys
import time
import urllib.parse
import urllib.request

from providers.base import ProviderError

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.expanduser('~/.mod/compute')
BUILD = (os.environ.get('COMPUTE_BUILD_URL') or 'http://127.0.0.1:8890').rstrip('/')
SECRET_FILE = os.path.join(STATE, 'server.secret')

OPEN = {'', '/', '/health', '/providers', '/search', '/map', '/offer', '/quote',
        '/mods', '/tools', '/console', '/index.html', '/identity',
        # whether the chat agent exists — the chat itself is owner-only
        '/chat/health',
        # the prediction game: a call spends nothing and carries its own player key
        '/oracle', '/oracle/series', '/oracle/board', '/oracle/calls',
        '/oracle/join', '/oracle/predict'}
BYOK = {'/instances', '/status', '/logs', '/balance', '/keys'}

# Tool name → tier, for the MCP endpoint, which is one URL for everything.
OPEN_TOOLS = {'compute_providers', 'compute_search', 'compute_map',
              'compute_show_map', 'compute_show_offers', 'compute_offer',
              'compute_quote', 'compute_mods', 'compute_oracle',
              'compute_predict'}
BYOK_TOOLS = {'compute_instances', 'compute_status', 'compute_logs',
              'compute_balance'}


class Denied(ProviderError):
    """The caller is not the owner and this is the owner's to do."""


def secret(create=True):
    """The module's own secret, 0600 and off-tree. Minted on first need."""
    try:
        with open(SECRET_FILE) as f:
            got = f.read().strip()
        if got:
            return got
    except FileNotFoundError:
        pass
    if not create:
        return ''
    os.makedirs(STATE, exist_ok=True)
    token = secrets.token_hex(32)
    fd = os.open(SECRET_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write(token)
    os.chmod(SECRET_FILE, 0o600)
    return token


def presented(headers):
    """The token on a request: Authorization: Bearer …, or x-compute-token."""
    raw = headers.get('authorization') or ''
    if raw.lower().startswith('bearer '):
        return raw[7:].strip()
    return (headers.get('x-compute-token') or '').strip()


def is_local(client_addr, headers):
    """Loopback and unproxied. The gateway's X-Forwarded-For gives it away."""
    host = (client_addr or '')
    if host not in ('127.0.0.1', '::1', 'localhost'):
        return False
    return not any(headers.get(h) for h in
                   ('x-forwarded-for', 'x-forwarded-host', 'x-real-ip'))


def authed(headers, client_addr=None):
    token = presented(headers)
    if token and hmac.compare_digest(token, secret()):
        return True
    if token and is_owner_session(token):
        return True
    return is_local(client_addr, headers)


# ── the owner's own sign-in ──────────────────────────────────────────

_SEEN = {}            # sha256(token) -> (until, is_owner)
_TTL_YES, _TTL_NO = 120, 15
_AUTH = None


def is_owner_session(token):
    """Is this bearer the deployment owner signed in? Cached briefly by hash."""
    h = hashlib.sha256(token.encode()).hexdigest()
    now = time.time()
    hit = _SEEN.get(h)
    if hit and hit[0] > now:
        return hit[1]
    try:
        ok = _build_session_owner(token) if _is_build_session(token) \
            else _role_is_owner(_wallet_address(token))
    except Exception:
        ok = False
    if len(_SEEN) > 512:
        _SEEN.clear()
    _SEEN[h] = (now + (_TTL_YES if ok else _TTL_NO), ok)
    return ok


def _is_build_session(token):
    # build mints address:timestamp:hmac (or address:timestamp:ho:hmac)
    parts = token.split(':')
    return len(parts) in (3, 4) and parts[1].isdigit()


def _build_session_owner(token):
    body = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                       'params': {'name': 'whoami', 'arguments': {}}}).encode()
    req = urllib.request.Request(BUILD + '/mcp', data=body, headers={
        'content-type': 'application/json', 'authorization': 'Bearer ' + token})
    with urllib.request.urlopen(req, timeout=4) as r:
        got = json.load(r)
    who = ((got.get('result') or {}).get('structuredContent')) or {}
    return bool(who.get('address')) and who.get('is_owner') is True


def _wallet_address(token):
    """Signer of a mod-protocol token, lowercased, or ''."""
    global _AUTH
    if _AUTH is None:
        # this directory holds mod.py, which shadows the protocol package
        saved, ours = list(sys.path), sys.modules.get('mod')
        if ours is not None and os.path.dirname(
                os.path.realpath(getattr(ours, '__file__', '') or '')) == HERE:
            del sys.modules['mod']
        sys.path[:] = [p for p in sys.path
                       if os.path.realpath(p or os.getcwd()) != HERE]
        try:
            from mod.core.server.auth.auth.auth import Auth
            _AUTH = Auth(max_age=int(os.environ.get('COMPUTE_TOKEN_MAX_AGE')
                                     or 7 * 24 * 3600))
        finally:
            sys.path[:] = saved
            if ours is not None:
                sys.modules.setdefault('mod', ours)
    with contextlib.redirect_stdout(io.StringIO()):      # Auth.verify is chatty
        headers = _AUTH.verify(token.strip())
    return str((headers or {}).get('key') or '').strip().lower()


def _role_is_owner(address):
    if not address:
        return False
    try:
        url = BUILD + '/auth/role?' + urllib.parse.urlencode({'address': address})
        with urllib.request.urlopen(url, timeout=4) as r:
            return json.load(r).get('is_owner') is True
    except Exception:
        # build down: fall back to the owner it declares on disk
        try:
            with open(os.path.join(os.path.dirname(HERE), 'build', 'config.json')) as f:
                return str(json.load(f).get('owner') or '').lower() == address
        except Exception:
            return False


def guard(path, keys=None, owner=False):
    """Raise unless this request is allowed to touch this path."""
    path = (path or '/').rstrip('/') or '/'
    if owner:
        return True
    if path in OPEN:
        return True
    if path in BYOK and keys:
        return True                     # their key, their account, their answer
    raise Denied(_why(path), status=401,
                 hint='sign in as the owner (wallet or the build console session), '
                      'send Authorization: Bearer <token from `m compute/token`>, '
                      'or bring your own provider keys with x-<provider>-key')


def guard_tool(name, args=None):
    """The same three tiers, for a tool call arriving over MCP."""
    if name in OPEN_TOOLS:
        return True
    if name in BYOK_TOOLS and (args or {}).get('keys'):
        return True
    raise Denied(f'{name} is owner-only on a published server', status=401,
                 hint='pass keys={provider: key} to spend your own account, or '
                      'run the MCP server over stdio, where you are the owner')


def _why(path):
    if path.startswith('/node') or path in ('/nodes', '/deploy'):
        return (f'{path} runs commands on machines this module manages — '
                f'owner only')
    if path.startswith('/oracle'):
        return (f'{path} reads every market with the operator\'s keys — owner only; '
                f'the game ticks on its own every 20 minutes')
    if path in BYOK:
        return f'{path} would read the operator\'s own provider accounts — owner only'
    return f'{path} spends the operator\'s credits — owner only'


def state():
    """What the console needs to know, without ever shipping the secret."""
    return {'token_required': True,
            'secret_file': SECRET_FILE,
            'minted': os.path.exists(SECRET_FILE),
            'open': sorted(OPEN),
            'byok': sorted(BYOK),
            'how': 'sign in with the owner wallet (or open it inside the build '
                   'console, which hands its session down); `m compute/token` '
                   'still works as a bearer, and localhost is already the owner'}
