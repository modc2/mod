"""Who may spend the operator's Polaris credits.

Reading the catalog is a public act: `gpus`, `pricing`, `templates` and
`models` cost nothing, reveal nothing about the operator, and are the reason
anyone would mount this module in the first place. They stay open.

Everything past that is the operator's account. `instances` says what they are
already paying for, `account` carries their email, `rent` spends their money,
and `raw` is the whole upstream API. When this module is published at
modc2.com/polaris those must not be one URL away from the world.

Three tiers, applied in `guard`:

    open    info, health, gpus, pricing, templates, models, quote, packs, tools
    byok    everything account-shaped, when the caller brought their own key —
            then it is their account being read, not the operator's
    owner   rent, stop, raw, set_key, token

A request is the owner's if it carries this module's secret as a bearer token,
or if it arrived from this machine without passing through the gateway. Caddy
stamps X-Forwarded-For on everything it proxies, so a public request can never
look loopback, while `curl localhost:50870/instances` just works.
"""

import hmac
import os
import secrets

from client import PolarisError

STATE = os.path.expanduser('~/.mod/polaris')
SECRET_FILE = os.path.join(STATE, 'server.secret')

OPEN = {'', '/', '/health', '/gpus', '/pricing', '/templates', '/template',
        '/models', '/quote', '/packs', '/tools', '/console', '/index.html'}

# Reading an account is fine when it is the caller's own account, which is
# exactly what "the caller sent a key" means.
BYOK = {'/instances', '/instance', '/ssh', '/deployments', '/deployment',
        '/logs', '/activity', '/account', '/credits', '/balance', '/history',
        '/usage', '/stats', '/keys', '/status'}

OPEN_TOOLS = {'polaris_gpus', 'polaris_pricing', 'polaris_templates',
              'polaris_models', 'polaris_quote'}
BYOK_TOOLS = {'polaris_instances', 'polaris_ssh', 'polaris_deployments',
              'polaris_logs', 'polaris_account', 'polaris_credits',
              'polaris_usage', 'polaris_status'}


class Denied(PolarisError):
    """Not the owner, and this is the owner's to do."""


def secret(create=True):
    """This module's own secret, 0600 and off-tree. Minted on first need."""
    try:
        with open(SECRET_FILE) as f:
            got = f.read().strip()
        if got:
            return got
    except OSError:
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
    """The token on a request: Authorization: Bearer …, or x-polaris-token."""
    raw = headers.get('authorization') or ''
    if raw.lower().startswith('bearer '):
        return raw[7:].strip()
    return (headers.get('x-polaris-token') or '').strip()


def caller_key(headers):
    """The caller's own Polaris key, if they brought one. BYOK, per request:
    it lives for the length of the call and is never written down here."""
    return (headers.get('x-polaris-key') or '').strip() or None


def is_local(client_addr, headers):
    """Loopback and unproxied. The gateway's X-Forwarded-For gives it away."""
    if (client_addr or '') not in ('127.0.0.1', '::1', 'localhost'):
        return False
    return not any(headers.get(h) for h in
                   ('x-forwarded-for', 'x-forwarded-host', 'x-real-ip'))


def authed(headers, client_addr=None):
    token = presented(headers)
    if token and hmac.compare_digest(token, secret()):
        return True
    return is_local(client_addr, headers)


def guard(path, key=None, owner=False):
    """Raise unless this request may touch this path."""
    path = (path or '/').rstrip('/') or '/'
    if owner:
        return True
    if path in OPEN:
        return True
    if path in BYOK and key:
        return True                    # their key, their account, their answer
    raise Denied(_why(path), status=401,
                 hint='send Authorization: Bearer `m polaris/token`, or bring '
                      'your own account with x-polaris-key: pi_sk_…')


def guard_tool(name, args=None):
    """The same three tiers, for a tool arriving over MCP."""
    if name in OPEN_TOOLS:
        return True
    if name in BYOK_TOOLS and (args or {}).get('key'):
        return True
    raise Denied(f'{name} is owner-only on a published server', status=401,
                 hint='pass key=pi_sk_… to spend your own account, or run the '
                      'MCP server over stdio, where you are the owner')


def _why(path):
    if path in ('/rent', '/stop'):
        return f'{path} spends the operator\'s Polaris credits — owner only'
    if path in ('/raw', '/set_key', '/token'):
        return f'{path} is the operator\'s own credentials — owner only'
    if path in BYOK:
        return (f'{path} would read the operator\'s own Polaris account — '
                f'bring your own key, or prove you are the owner')
    return f'{path} is not open — owner only'


def state():
    """What the console needs, without ever shipping the secret."""
    return {'token_required': True,
            'secret_file': SECRET_FILE,
            'minted': os.path.exists(SECRET_FILE),
            'open': sorted(OPEN),
            'byok': sorted(BYOK),
            'how': 'm polaris/token → paste into the console, or call from '
                   'localhost where the owner already is'}
