"""Who may spend your money, and who may run commands on your boxes.

Searching every market is a public act — it reads catalogs, spends nothing and
reveals nothing about you, so it stays open. Everything past that point is
not: renting spends the operator's credits, `instances` reveals what they are
already paying for, and a node route runs a shell on a machine they own. When
this module is mounted at modc2.com/compute-fork, those must not be a URL away.

Three tiers, applied in `guard`:

    open    info, health, providers, search, offer, quote, mods, tools
    byok    instances, status, logs, balance, keys — allowed if the caller
            brought their own provider key, because then it is their account
            being read, not the operator's
    owner   rent, stop, exec, raw, set_key, and every /node route

A request counts as the owner's if it carries the module's secret as a bearer
token, or if it came from this machine and did not pass through the gateway —
Caddy stamps X-Forwarded-For on everything it proxies, so a public request can
never look loopback, while `curl localhost:50511` still just works.
"""

import hmac
import os
import secrets

from providers.base import ProviderError

STATE = os.path.expanduser('~/.mod/compute-fork')
SECRET_FILE = os.path.join(STATE, 'server.secret')

OPEN = {'', '/', '/health', '/providers', '/search', '/offer', '/quote',
        '/mods', '/tools', '/console', '/index.html', '/identity'}
BYOK = {'/instances', '/status', '/logs', '/balance', '/keys'}

# Tool name → tier, for the MCP endpoint, which is one URL for everything.
OPEN_TOOLS = {'computefork_providers', 'computefork_search', 'computefork_offer',
              'computefork_quote', 'computefork_mods'}
BYOK_TOOLS = {'computefork_instances', 'computefork_status', 'computefork_logs',
              'computefork_balance'}


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
    """The token on a request: Authorization: Bearer …, or x-computefork-token."""
    raw = headers.get('authorization') or ''
    if raw.lower().startswith('bearer '):
        return raw[7:].strip()
    return (headers.get('x-computefork-token') or '').strip()


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
    return is_local(client_addr, headers)


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
                 hint='send Authorization: Bearer <token from `m compute-fork/token`>, '
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
            'how': 'm compute-fork/token  →  paste into the console, or call from '
                   'localhost where the owner already is'}
