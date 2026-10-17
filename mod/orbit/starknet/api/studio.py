"""
starknet/api/studio.py — the contract studio: write, build and test Cairo
contracts from the starknet app, with an agent that edits them for you.

This module owns no toolchain. The work happens in the `cairo` mod
(orbit/cairo, :51291, scarb + snforge + the headless Claude CLI); this file
is a thin, auth-gated proxy onto its API so the explorer and the workbench
share one place to build and one place to look at the chain.

    reads  — open:   status templates contracts get tree read abi job jobs
    writes — owner:  new write build test vibe clean remove

Writes touch files on this box and `vibe` spends the host's Claude account,
and this app is publicly routed, so a write needs one of:

  * a direct loopback request (no proxy headers, Host localhost) — the CLI,
    the MCP stdio server, curl on the box;
  * a mod-protocol token (header `token:` or `Authorization: Bearer`) signed
    by an owner address: config.json `owner`, the build console's private
    flag owner, or STARKNET_STUDIO_OWNERS (comma list).

Long verbs (build, test, vibe) always run as cairo jobs; poll `job?id=`.
"""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAIRO = os.environ.get('STARKNET_CAIRO', 'http://127.0.0.1:51291/cairo/api').rstrip('/')
TOKEN_MAX_AGE = int(os.environ.get('STARKNET_STUDIO_TOKEN_AGE', 7 * 86400))

READ = ('status', 'templates', 'contracts', 'get', 'tree', 'read', 'abi', 'job', 'jobs')
WRITE = ('new', 'write', 'build', 'test', 'vibe', 'clean', 'remove')
LONG = ('build', 'test', 'vibe')


class StudioError(Exception):
    pass


class Denied(StudioError):
    pass


# ---- who may write ----------------------------------------------------------

def owners():
    """Every address allowed to write, lowercased."""
    out = set()
    try:
        cfg = json.load(open(os.path.join(ROOT, 'config.json')))
        if cfg.get('owner'):
            out.add(cfg['owner'])
    except Exception:  # noqa: BLE001
        pass
    flag = os.path.expanduser('~/.mod/build/private/starknet.json')
    try:
        if json.load(open(flag)).get('owner'):
            out.add(json.load(open(flag))['owner'])
    except Exception:  # noqa: BLE001
        pass
    out.update(a for a in os.environ.get('STARKNET_STUDIO_OWNERS', '').split(',') if a)
    return {a.strip().lower() for a in out}


_PROXY_HEADERS = ('x-forwarded-for', 'x-real-ip', 'cf-connecting-ip',
                  'forwarded', 'x-forwarded-host')


def is_local(client_ip, headers):
    """A request made on this box, not one relayed by caddy or the gateway."""
    if client_ip not in ('127.0.0.1', '::1', '::ffff:127.0.0.1'):
        return False
    if any(headers.get(h) for h in _PROXY_HEADERS):
        return False
    host = (headers.get('host') or '').rsplit(':', 1)[0].strip('[]')
    return host in ('localhost', '127.0.0.1', '::1', '')


def token_of(headers):
    tok = headers.get('token') or ''
    auth = headers.get('authorization') or ''
    if not tok and auth.lower().startswith('bearer '):
        tok = auth[7:].strip()
    return tok or None


def verify(token):
    """The signer address of a mod-protocol token, or raise Denied."""
    try:
        import mod as m  # the protocol's shared Auth — see auth_standardization
        auth = m.mod('auth')(max_age=TOKEN_MAX_AGE)
        return str(auth.verify(token)['key']).lower()
    except ImportError:
        raise Denied('token auth needs the mod package on this server')
    except Exception as e:  # noqa: BLE001
        raise Denied(f'bad token: {e}'.split('{')[0].strip())


def whoami(client_ip, headers):
    """{writable, via, address, owners} for the caller."""
    own = sorted(owners())
    if is_local(client_ip, headers):
        return {'writable': True, 'via': 'local', 'address': None, 'owners': own}
    tok = token_of(headers)
    if not tok:
        return {'writable': False, 'via': None, 'address': None, 'owners': own,
                'why': 'sign in with the owner wallet to build'}
    try:
        addr = verify(tok)
    except Denied as e:
        return {'writable': False, 'via': None, 'address': None, 'owners': own,
                'why': str(e)}
    ok = addr in own
    return {'writable': ok, 'via': 'token' if ok else None, 'address': addr,
            'owners': own, **({} if ok else {'why': f'{addr} is not an owner of this studio'})}


# ---- the cairo API ----------------------------------------------------------

def _cairo(fn, params=None, post=False, timeout=30):
    url = f'{CAIRO}/{fn}'
    data = None
    if post:
        data = json.dumps(params or {}).encode()
    elif params:
        url += '?' + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    req = urllib.request.Request(url, data=data, method='POST' if post else 'GET',
                                 headers={'Content-Type': 'application/json',
                                          'Accept': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            out = json.loads(r.read().decode() or '{}')
    except urllib.error.HTTPError as e:
        try:
            out = json.loads(e.read().decode() or '{}')
        except Exception:  # noqa: BLE001
            out = {}
        raise StudioError(out.get('error') or f'cairo {fn}: HTTP {e.code}')
    except (urllib.error.URLError, OSError) as e:
        raise StudioError(f'the cairo workbench is not running ({CAIRO}): '
                          f'start it with `m cairo/serve` — {getattr(e, "reason", e)}')
    if isinstance(out, dict) and out.get('error') and fn not in ('abi',):
        raise StudioError(out['error'])
    return out


def call(fn, p, writable=False):
    """Run one studio verb. `p` is the merged query + body."""
    if fn not in READ + WRITE:
        raise KeyError(fn)
    if fn in WRITE and not writable:
        raise Denied(f'{fn} is owner-only — sign in with the owner wallet')
    if fn == 'status':
        return status()
    if fn == 'templates':
        return _cairo('templates')
    if fn == 'contracts':
        return _cairo('list')
    if fn in ('get', 'tree', 'abi'):
        return _cairo(fn, {'name': p['name']})
    if fn == 'read':
        return _cairo('read', {'name': p['name'], 'path': p['path']})
    if fn == 'job':
        return _cairo('job', {'id': p['id']})
    if fn == 'jobs':
        name = p.get('name')
        jobs = _cairo('jobs').get('jobs', [])
        return {'jobs': [j for j in jobs if not name or j.get('name') == name][:20]}
    name = p['name']
    if fn == 'new':
        return _cairo('new', {'name': name, 'desc': p.get('desc') or None,
                              'template': p.get('template') or 'counter'}, post=True, timeout=60)
    if fn == 'write':
        return _cairo('write', {'name': name, 'path': p['path'],
                                'content': p.get('content', '')}, post=True)
    if fn in ('clean', 'remove'):
        return _cairo(fn, {'name': name}, post=True)
    kw = {'name': name, 'async': True}
    if fn == 'vibe':
        prompt = str(p.get('prompt') or '').strip()
        if not prompt:
            raise KeyError('prompt')
        kw.update(prompt=prompt[:8000], verify=p.get('verify', True) not in (False, 'false', '0'))
        if p.get('model'):
            kw['model'] = str(p['model'])
    return _cairo(fn, kw, post=True)


def status():
    """Is the workbench up, which toolchain, how many contracts."""
    t0 = time.time()
    try:
        tc = _cairo('toolchain', timeout=8)
        n = _cairo('list', timeout=8).get('count', 0)
        return {'ok': True, 'cairo': CAIRO, 'toolchain': tc, 'contracts': n,
                'ms': int((time.time() - t0) * 1000)}
    except StudioError as e:
        return {'ok': False, 'cairo': CAIRO, 'error': str(e)}
