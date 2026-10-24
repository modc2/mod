"""Who may make this box generate.

Reading is open: `/`, `/health`, `/agents`, `/models` describe the module and
cost nothing. `GET /agents` in particular MUST stay open — it is the probe
orbit/build sends before it will mount this module as an agent backend, and a
probe that has to be authenticated is a module nobody can mount.

Running is not. A run here holds this machine's CPU for minutes and, outside a
sandboxed run, calls tools that write files and execute shell commands. Two
kinds of caller are allowed to do that:

    owner    a request carrying this module's own secret as a bearer token, or
             one that arrived from this machine without passing through the
             gateway (Caddy stamps X-Forwarded-For on everything it proxies, so
             a public request can never look loopback)
    signed   a mod-protocol token in the body's `key`, verified through the
             auth module — the fleet's one identity. This is the credential
             orbit/build's job dispatcher already sends to an agent module.

`sandbox: true` in the body drops the write tools; it does not drop the gate.
Read-only still means minutes of this box's compute.
"""
import hmac
import os
import secrets

STATE = os.path.expanduser('~/.mod/hermes')
SECRET_FILE = os.path.join(STATE, 'server.secret')

OPEN = {'', '/', '/health', '/agents', '/models', '/info'}


class Denied(Exception):
    def __init__(self, why, hint=None):
        super().__init__(why)
        self.why = why
        self.hint = hint


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
    raw = headers.get('authorization') or ''
    if raw.lower().startswith('bearer '):
        return raw[7:].strip()
    return (headers.get('x-hermes-token') or '').strip()


def is_local(client_addr, headers):
    """Loopback and unproxied."""
    if (client_addr or '') not in ('127.0.0.1', '::1', 'localhost'):
        return False
    return not any(headers.get(h) for h in
                   ('x-forwarded-for', 'x-forwarded-host', 'x-real-ip'))


def signer(key):
    """The address behind a mod-protocol token, or None.

    Verification is the auth module's job — it is the fleet's one identity, so
    a token minted for any module verifies here. A missing auth module is a
    refusal, never a pass: failing open on the identity layer would make every
    published hermes an open shell.
    """
    if not key:
        return None
    try:
        import mod as m
        return m.mod('auth')().verify(key)['key']
    except Exception:
        return None


def guard(path, headers=None, client_addr=None, key=None):
    """Raise Denied unless this request may run. Returns who it decided was
    asking, which is what the run is logged against."""
    headers = headers or {}
    clean = (path or '/').rstrip('/') or '/'
    if clean in OPEN:
        return 'public'
    token = presented(headers)
    if token and hmac.compare_digest(token, secret()):
        return 'owner'
    if is_local(client_addr, headers):
        return 'local'
    who = signer(key)
    if who:
        return who
    raise Denied(
        f'{clean} runs a model and its tools on this machine — that is not open',
        hint='send Authorization: Bearer `cat ~/.mod/hermes/server.secret`, a '
             'mod-protocol token as `key` in the body, or call from localhost')


def state():
    return {'token_required': True, 'secret_file': SECRET_FILE,
            'minted': os.path.exists(SECRET_FILE), 'open': sorted(OPEN),
            'signed': 'a mod-protocol token in the body `key`, verified via the auth module'}
