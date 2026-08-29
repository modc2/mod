"""Nodes — a box you rented becomes a mod protocol container you can drive.

A market gives you an SSH line and a bill. That is not a machine you can use;
it is a machine you now have to set up. This file is the part that turns one
into the other and keeps holding the rope afterwards:

    deploy      rent (or adopt) a box, put a container on it, install mod
    ctl         ask that mod what modules it has, call one, read the answer
    sh          run anything, because sometimes you just need a shell
    push        send a module from this machine to that one
    tunnel      bring a port on that box back to this browser
    destroy     tear the container down, and the rental with it if you say so

Three ideas hold it together.

**One transport interface, four ways to reach a box.** A local docker daemon,
an SSH host, a container inside an SSH host, or a provider's own exec API all
answer `run(cmd)` and `put(path, bytes)`. Every step above is written once
against that interface, so bootstrapping a Vast rental and bootstrapping the
container on your desk are the same code path.

**The install ships from here, not from the internet.** The node gets a tar of
this checkout's `mod/core` streamed over the transport — no git on the box, no
clone from GitHub, no drift between the module you are running and the module
you deployed. The `lite` profile installs nine small wheels and nothing else,
because a rented GPU-hour spent downloading torch is a rented GPU-hour wasted.

**Control is a file, not a port.** `modctl.py` sits on the node and speaks
JSON over the same transport that installed it. Nothing has to listen on a
public interface for the browser to list modules and call functions, so a node
is exactly as reachable as your SSH key and no more.
"""

import base64
import io
import json
import os
import shlex
import socket
import subprocess
import tarfile
import threading
import time
import uuid

from providers.base import ProviderError

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(os.path.dirname(HERE))          # …/mod
LIB = os.path.dirname(PKG)                            # …/  (repo root)

STATE = os.path.expanduser('~/.mod/compute-fork')
NODES_FILE = os.path.join(STATE, 'nodes.json')
SSH_KEY = os.path.join(STATE, 'id_ed25519')

REMOTE_MOD = '/root/mod'
REMOTE_CTL = '/root/.modctl.py'
DEFAULT_IMAGE = 'python:3.12-slim'

# Everything `import mod` touches on the way up, and nothing else. Measured by
# installing them one at a time into an empty python:3.12-slim until the import
# stopped raising — not copied from requirements.txt, which pulls torch.
LITE_DEPS = ('pyyaml', 'requests', 'munch', 'rich', 'loguru', 'psutil',
             'python-dotenv', 'xxhash', 'netaddr')

BEGIN, END = '<<<MODCTL', 'MODCTL>>>'

_LOCK = threading.Lock()


class NodeError(ProviderError):
    """Something about a node the caller should read and act on."""


# ── transports ───────────────────────────────────────────────────────────

class Transport:
    """Move a command or a file to somewhere that is not here."""

    kind = 'none'
    streams = True          # can accept a file on stdin (tar, key, script)
    label = ''

    def prefix(self):
        """argv that puts us on the far side, ready for a command."""
        raise NotImplementedError

    def _wrap(self, cmd):
        """`cmd` is one shell string. SSH re-joins argv, so quote for it."""
        return self.prefix() + ['bash', '-lc', cmd]

    def run(self, cmd, timeout=120, stdin=None):
        argv = self._wrap(cmd)
        try:
            r = subprocess.run(argv, input=stdin, capture_output=True,
                               timeout=timeout)
        except subprocess.TimeoutExpired:
            return {'ok': False, 'code': 124, 'out': '',
                    'err': f'timed out after {timeout}s', 'cmd': cmd}
        except FileNotFoundError as e:
            raise NodeError(f'{self.kind}: {e}')
        dec = (lambda b: (b or b'').decode('utf-8', 'replace'))
        return {'ok': r.returncode == 0, 'code': r.returncode,
                'out': dec(r.stdout), 'err': dec(r.stderr), 'cmd': cmd}

    def put(self, path, data):
        """Write bytes to a file on the far side."""
        if isinstance(data, str):
            data = data.encode()
        q = shlex.quote(path)
        r = self.run(f'mkdir -p "$(dirname {q})" && cat > {q}', stdin=data, timeout=300)
        if not r['ok']:
            raise NodeError(f'{self.kind}: could not write {path}: '
                            f'{r["err"][:200] or r["out"][:200]}')
        return {'path': path, 'bytes': len(data)}

    def put_tar(self, blob, dest):
        """Stream a .tar.gz into a directory on the far side."""
        q = shlex.quote(dest)
        r = self.run(f'mkdir -p {q} && tar xzf - -C {q}', stdin=blob, timeout=600)
        if not r['ok']:
            raise NodeError(f'{self.kind}: could not unpack into {dest}: '
                            f'{r["err"][:300]}')
        return {'dest': dest, 'bytes': len(blob)}


class Here(Transport):
    """This machine. Mostly for tests and for driving your own box."""

    kind = 'here'

    def __init__(self):
        self.label = 'localhost'

    def prefix(self):
        return []

    def _wrap(self, cmd):
        return ['bash', '-lc', cmd]


class Ssh(Transport):
    """A box with an SSH door — what nearly every GPU market hands you."""

    kind = 'ssh'

    def __init__(self, host, user='root', port=22, key=None):
        self.host, self.user, self.port = host, user, int(port or 22)
        self.keyfile = key or SSH_KEY
        self.label = f'{self.user}@{self.host}:{self.port}'

    def prefix(self):
        argv = ['ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=accept-new',
                '-o', 'UserKnownHostsFile=' + os.path.join(STATE, 'known_hosts'),
                '-o', 'ConnectTimeout=15', '-o', 'ServerAliveInterval=15']
        if self.keyfile and os.path.exists(self.keyfile):
            argv += ['-i', self.keyfile]
        return argv + ['-p', str(self.port), f'{self.user}@{self.host}']

    def _wrap(self, cmd):
        return self.prefix() + ['bash', '-lc', shlex.quote(cmd)]


class Docker(Transport):
    """A container — here, or inside a box we reach some other way."""

    kind = 'docker'

    def __init__(self, container, host=None):
        self.container, self.host = container, host
        self.label = f'{container}@{host.label}' if host else f'{container}@localhost'

    def prefix(self):
        base = self.host.prefix() if self.host else []
        return base + ['docker', 'exec', '-i', self.container]

    def _wrap(self, cmd):
        if self.host:                       # the far side re-parses the string
            return self.prefix() + ['bash', '-lc', shlex.quote(cmd)]
        return self.prefix() + ['bash', '-lc', cmd]

    def host_run(self, cmd, **kw):
        """Run on the docker host rather than inside the container."""
        return (self.host or Here()).run(cmd, **kw)


class ProviderExec(Transport):
    """Markets with no SSH but their own exec endpoint (Targon workloads)."""

    kind = 'provider'
    streams = False

    def __init__(self, instance_id, keys=None):
        self.instance_id, self.keys = instance_id, keys or {}
        self.label = instance_id

    def prefix(self):
        return []

    def run(self, cmd, timeout=120, stdin=None):
        from hub import Hub
        if stdin is not None:
            return {'ok': False, 'code': 1, 'out': '', 'cmd': cmd,
                    'err': 'this provider forwards commands but not stdin'}
        try:
            out = Hub(keys=self.keys).exec(self.instance_id, cmd)
        except ProviderError as e:
            return {'ok': False, 'code': 1, 'out': '', 'err': str(e), 'cmd': cmd}
        text = out.get('output') if isinstance(out, dict) else out
        return {'ok': True, 'code': 0, 'out': str(text or ''), 'err': '', 'cmd': cmd}

    def put(self, path, data):
        """No stdin, so the file rides in on the command line, base64'd."""
        if isinstance(data, str):
            data = data.encode()
        if len(data) > 256_000:
            raise NodeError(f'{self.instance_id}: {len(data)} bytes is too big to '
                            f'push through an exec API — this node needs SSH')
        b64 = base64.b64encode(data).decode()
        r = self.run(f'mkdir -p "$(dirname {shlex.quote(path)})" && '
                     f'echo {b64} | base64 -d > {shlex.quote(path)}', timeout=300)
        if not r['ok']:
            raise NodeError(f'could not write {path}: {r["err"][:200]}')
        return {'path': path, 'bytes': len(data)}

    def put_tar(self, blob, dest):
        raise NodeError('this node has no stdin — bootstrap it over SSH, or use '
                        'profile=git so the box clones mod itself')


def transport(node):
    """The way to reach one node, rebuilt from its record every time."""
    t = node.get('target') or {}
    kind = t.get('kind')
    if kind == 'here':
        return Here()
    if kind == 'ssh':
        return Ssh(t['host'], user=t.get('user', 'root'), port=t.get('port', 22),
                   key=t.get('key'))
    if kind == 'docker':
        host = None
        if t.get('host'):
            h = t['host']
            host = Ssh(h['host'], user=h.get('user', 'root'), port=h.get('port', 22),
                       key=h.get('key'))
        return Docker(t['container'], host=host)
    if kind == 'provider':
        return ProviderExec(t['instance'], keys=t.get('keys'))
    raise NodeError(f'node {node.get("id")}: unknown target {t!r}')


# ── registry (off-tree, next to the keys) ────────────────────────────────

def _read():
    try:
        with open(NODES_FILE) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _write(all_nodes):
    os.makedirs(STATE, exist_ok=True)
    tmp = NODES_FILE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(all_nodes, f, indent=2, default=str)
    os.replace(tmp, NODES_FILE)


def save(node):
    with _LOCK:
        all_nodes = _read()
        all_nodes[node['id']] = node
        _write(all_nodes)
    return node


def get(nid):
    node = _read().get(str(nid or '').strip())
    if not node:
        known = ', '.join(_read()) or 'none yet'
        raise NodeError(f'no node "{nid}" — have {known}')
    return node


def nodes(state=None):
    rows = sorted(_read().values(), key=lambda n: n.get('created') or 0, reverse=True)
    if state:
        rows = [n for n in rows if n.get('state') == state]
    burn = sum(n.get('usd_hr') or 0 for n in rows if n.get('state') != 'gone')
    return {'nodes': [_public(n) for n in rows], 'count': len(rows),
            'burn_usd_hr': round(burn, 4)}


def forget(nid):
    with _LOCK:
        all_nodes = _read()
        gone = all_nodes.pop(str(nid), None)
        _write(all_nodes)
    if not gone:
        raise NodeError(f'no node "{nid}"')
    return {'forgot': nid}


def _public(node):
    """A node record with the bootstrap trail trimmed for a list view."""
    out = {k: v for k, v in node.items() if k != 'log'}
    out['steps'] = len(node.get('log') or [])
    last = (node.get('log') or [])[-1:]
    out['last_step'] = last[0] if last else None
    return out


def _log(node, step, ok, ms, out='', err=''):
    entry = {'step': step, 'ok': bool(ok), 'ms': round(ms), 'at': time.time(),
             'out': (out or '')[-1200:], 'err': (err or '')[-1200:]}
    node.setdefault('log', []).append(entry)
    node['log'] = node['log'][-60:]
    save(node)
    return entry


# ── ssh identity ─────────────────────────────────────────────────────────

def keypair(create=True):
    """This module's own ed25519 key. Markets get the public half at rent time."""
    pub = SSH_KEY + '.pub'
    if not os.path.exists(SSH_KEY):
        if not create:
            return {'exists': False}
        os.makedirs(STATE, exist_ok=True)
        r = subprocess.run(['ssh-keygen', '-t', 'ed25519', '-N', '', '-C',
                            'mod-compute-fork', '-f', SSH_KEY],
                           capture_output=True, text=True)
        if r.returncode != 0 and not os.path.exists(SSH_KEY):
            raise NodeError(f'ssh-keygen failed: {r.stderr[:300]}')
        os.chmod(SSH_KEY, 0o600)
    with open(pub) as f:
        return {'exists': True, 'private': SSH_KEY, 'public': f.read().strip()}


# ── the install payload ──────────────────────────────────────────────────

def core_tar():
    """This checkout's mod core, as a tar.gz, built in memory.

    Core only: the whole tree is a gigabyte because orbit modules carry their
    own node_modules. Push the ones you want afterwards with `push`.
    """
    buf = io.BytesIO()
    skip = ('__pycache__', 'node_modules', '.git', '.next', 'target', '.venv')

    def keep(info):
        parts = info.name.split('/')
        if any(p in skip or p.endswith('.egg-info') for p in parts):
            return None
        if parts[-1].endswith(('.pyc', '.pyo', '.log')):
            return None
        return info

    with tarfile.open(fileobj=buf, mode='w:gz') as tar:
        tar.add(os.path.join(PKG, 'core'), arcname='mod/core', filter=keep)
        tar.add(os.path.join(PKG, '__init__.py'), arcname='mod/__init__.py')
        for name in ('config.json', 'requirements.txt', 'pyproject.toml', 'README.md'):
            path = os.path.join(LIB, name)
            if os.path.exists(path):
                tar.add(path, arcname=name)
    return buf.getvalue()


def mod_tar(name):
    """One local module directory, as a tar.gz, for `push`."""
    for root, arc in (('orbit', 'mod/orbit'), ('core', 'mod/core')):
        path = os.path.join(PKG, root, name)
        if os.path.isdir(path):
            buf = io.BytesIO()
            skip = ('__pycache__', 'node_modules', '.git', '.next', 'target')

            def keep(info):
                parts = info.name.split('/')
                return None if any(p in skip for p in parts) else info

            with tarfile.open(fileobj=buf, mode='w:gz') as tar:
                tar.add(path, arcname=f'{arc}/{name}', filter=keep)
            return buf.getvalue(), f'{arc}/{name}'
    raise NodeError(f'no local module "{name}" under mod/orbit or mod/core')


SHIM = f"""#!/bin/sh
# the mod CLI, without pip install — cwd and PYTHONPATH are all it wanted
cd {REMOTE_MOD} && PYTHONPATH={REMOTE_MOD} exec python3 -c 'import mod; mod.main()' "$@"
"""


# ── bootstrap ────────────────────────────────────────────────────────────

def _step(node, name, t, cmd, timeout=180, critical=True):
    t0 = time.time()
    r = t.run(cmd, timeout=timeout)
    _log(node, name, r['ok'], (time.time() - t0) * 1000, r['out'], r['err'])
    if critical and not r['ok']:
        node['state'] = 'error'
        node['error'] = f'{name}: {(r["err"] or r["out"])[-300:]}'
        save(node)
        raise NodeError(f'node {node["id"]}: {name} failed — '
                        f'{(r["err"] or r["out"])[-300:]}')
    return r


def bootstrap(nid, profile='lite', force=False, wait=True):
    """Install the mod protocol on a node. Idempotent; safe to re-run."""
    node = get(nid)
    if not wait:
        threading.Thread(target=lambda: _bootstrap_guarded(nid, profile, force),
                         daemon=True).start()
        node['state'] = 'booting'
        save(node)
        return {'id': node['id'], 'node': node['id'], 'name': node.get('name'),
                'state': 'booting', 'target': node.get('target'),
                'watch': f'm compute-fork/node id={node["id"]}'}
    return _bootstrap(node, profile=profile, force=force)


def _bootstrap_guarded(nid, profile, force):
    try:
        _bootstrap(get(nid), profile=profile, force=force)
    except Exception as e:                      # a thread has nobody to raise to
        try:
            node = get(nid)
            node['state'] = 'error'
            node['error'] = f'{type(e).__name__}: {e}'
            save(node)
        except Exception:
            pass


def _bootstrap(node, profile='lite', force=False):
    t = transport(node)
    node['state'] = 'booting'
    node['profile'] = profile
    node['error'] = None
    save(node)
    started = time.time()

    if not force:
        try:
            info = _ctl(node, t, 'info', timeout=60)
            if info.get('ok') and info.get('mod'):
                node.update(state='ready', mod=info, bootstrapped=time.time())
                _log(node, 'verify', True, 0, 'already installed')
                return _public(save(node))
        except Exception:
            pass

    _step(node, 'probe', t,
          'uname -sm; python3 -V 2>&1 || echo "no python3"; '
          'command -v pip3 pip git docker nvidia-smi 2>/dev/null; id -un', timeout=90)

    _step(node, 'python', t,
          'command -v python3 >/dev/null || '
          '(apt-get update -qq && apt-get install -y -qq python3 python3-pip) || '
          '(apk add --no-cache python3 py3-pip) || true; python3 -V', timeout=600)

    deps = ' '.join(LITE_DEPS)
    _step(node, 'deps', t,
          f'python3 -m ensurepip --default-pip >/dev/null 2>&1 || true; '
          f'python3 -m pip install --no-cache-dir -q {deps} || '
          f'python3 -m pip install --no-cache-dir -q --break-system-packages {deps}',
          timeout=900)

    t0 = time.time()
    blob = core_tar()
    t.put_tar(blob, REMOTE_MOD)
    _log(node, 'core', True, (time.time() - t0) * 1000,
         f'{len(blob) // 1024} KiB of mod/core -> {REMOTE_MOD}')

    t0 = time.time()
    with open(os.path.join(HERE, 'modctl.py')) as f:
        t.put(REMOTE_CTL, f.read())
    t.put('/usr/local/bin/m', SHIM)
    t.run('chmod +x /usr/local/bin/m', timeout=60)
    _log(node, 'ctl', True, (time.time() - t0) * 1000, f'{REMOTE_CTL} + m')

    if profile == 'full':
        _step(node, 'deps-full', t,
              f'python3 -m pip install --no-cache-dir -q -r {REMOTE_MOD}/requirements.txt '
              f'|| python3 -m pip install --no-cache-dir -q --break-system-packages '
              f'-r {REMOTE_MOD}/requirements.txt', timeout=3600, critical=False)

    info = _ctl(node, t, 'info', timeout=180)
    ok = bool(info.get('ok') and info.get('mod'))
    _log(node, 'verify', ok, 0, json.dumps(info)[:600])
    if not ok:
        node['state'] = 'error'
        node['error'] = info.get('error') or 'mod did not import after install'
        save(node)
        raise NodeError(f'node {node["id"]}: {node["error"]}')

    node.update(state='ready', mod=info, bootstrapped=time.time(),
                boot_ms=round((time.time() - started) * 1000))
    return _public(save(node))


# ── control ──────────────────────────────────────────────────────────────

def _ctl(node, t, op, timeout=120, **kw):
    payload = base64.b64encode(json.dumps({'op': op, **kw}).encode()).decode()
    r = t.run(f'MOD_DIR={REMOTE_MOD} python3 {REMOTE_CTL} {payload}', timeout=timeout)
    text = (r['out'] or '') + (r['err'] or '')
    if BEGIN not in text:
        raise NodeError(f'node {node["id"]}: no answer from modctl — '
                        f'{(text or "silence")[-300:]}',
                        hint='run `m compute-fork/bootstrap node=%s` first' % node['id'])
    blob = text.split(BEGIN, 1)[1].split(END, 1)[0]
    try:
        return json.loads(blob)
    except Exception:
        raise NodeError(f'node {node["id"]}: modctl answered nonsense: {blob[:200]}')


def ctl(nid, op='info', timeout=120, **kw):
    node = get(nid)
    return _ctl(node, transport(node), op, timeout=timeout, **kw)


def probe(nid):
    """Live state: is it reachable, is mod there, what is it running."""
    node = get(nid)
    t0 = time.time()
    try:
        info = _ctl(node, transport(node), 'info', timeout=60)
        node['state'] = 'ready' if info.get('mod') else 'reachable'
        node['mod'] = info
        node['error'] = None if info.get('mod') else info.get('error')
    except NodeError as e:
        node['state'] = 'unreachable'
        node['error'] = str(e)
    node['probed'] = time.time()
    node['probe_ms'] = round((time.time() - t0) * 1000)
    return _public(save(node))


def sh(nid, cmd, timeout=120):
    """A shell on the node. Same transport everything else rides on."""
    node = get(nid)
    r = transport(node).run(cmd, timeout=timeout)
    return {'node': nid, 'cmd': cmd, 'ok': r['ok'], 'code': r['code'],
            'out': r['out'], 'err': r['err'], 'via': node['target'].get('kind')}


def call(nid, mod, fn='forward', args=None, kwargs=None, init=None, timeout=300):
    """Call a module function on the node and bring the answer back."""
    r = ctl(nid, 'call', mod=mod, fn=fn, args=args or [], kwargs=kwargs or {},
            init=init or {}, timeout=timeout)
    if not r.get('ok'):
        raise NodeError(f'node {nid}: {mod}/{fn} — {r.get("error")}')
    return r


def push(nid, mod, restart=False):
    """Send one local module to the node, so it can run what you just wrote."""
    node = get(nid)
    t = transport(node)
    blob, arc = mod_tar(mod)
    t0 = time.time()
    t.put_tar(blob, REMOTE_MOD)
    # mod caches its module tree under ~/.mod/tree; a directory that appears
    # after that cache was written is a module the node cannot see.
    t.run('rm -rf ~/.mod/tree', timeout=60)
    _log(node, f'push:{mod}', True, (time.time() - t0) * 1000,
         f'{len(blob) // 1024} KiB -> {REMOTE_MOD}/{arc}')
    out = {'node': nid, 'mod': mod, 'path': f'{REMOTE_MOD}/{arc}',
           'bytes': len(blob)}
    if restart:
        out['restart'] = t.run(f'cd {REMOTE_MOD} && m {mod}/serve', timeout=300)
    return out


# ── tunnels ──────────────────────────────────────────────────────────────

def free_port(start=51000, end=51999, taken=()):
    """A port nothing is listening on and nothing in `taken` has claimed.

    Binding to test does not reserve it, so a caller allocating several ports
    in a row has to tell us what it already took or it gets the same one twice.
    """
    claimed = {int(p) for p in taken}
    used = {int(p) for n in _read().values() for p in (n.get('ports') or {})}
    for port in range(start, end):
        if port in claimed or port in used:
            continue
        with socket.socket() as s:
            try:
                s.bind(('127.0.0.1', port))
                return port
            except OSError:
                continue
    raise NodeError(f'no free local port in {start}-{end}')


def tunnel(nid, port, local_port=None):
    """Bring a port on the node back to localhost, so a browser can open it."""
    node = get(nid)
    t = transport(node)
    local_port = int(local_port or free_port())
    if isinstance(t, Docker) and not t.host:
        pub = node.get('ports') or {}
        for host_port, cport in pub.items():
            if int(cport) == int(port):
                return {'node': nid, 'url': f'http://localhost:{host_port}',
                        'note': 'already published by docker — no tunnel needed'}
        raise NodeError(f'container port {port} was not published at deploy time — '
                        f'redeploy with ports={port}')
    ssh_t = t.host if isinstance(t, Docker) else t
    if not isinstance(ssh_t, Ssh):
        raise NodeError('only SSH-reachable nodes can be tunnelled')
    argv = ssh_t.prefix()
    argv = argv[:1] + ['-N', '-f', '-L', f'{local_port}:127.0.0.1:{port}'] + argv[1:]
    r = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        raise NodeError(f'tunnel failed: {r.stderr[:300]}')
    node.setdefault('tunnels', {})[str(port)] = local_port
    save(node)
    return {'node': nid, 'remote_port': port, 'local_port': local_port,
            'url': f'http://localhost:{local_port}'}


# ── deploy ───────────────────────────────────────────────────────────────

def _new(name=None, target=None, **extra):
    nid = 'n-' + uuid.uuid4().hex[:6]
    node = {'id': nid, 'name': name or nid, 'target': target or {},
            'state': 'new', 'created': time.time(), 'log': [], **extra}
    return save(node)


def _ssh_from(text):
    """'ssh -p 41234 root@1.2.3.4' or 'root@1.2.3.4:41234' → target dict."""
    s = str(text or '').strip()
    if not s:
        raise NodeError('ssh: nothing to parse')
    port = 22
    if s.startswith('ssh '):
        parts = s.split()
        for i, p in enumerate(parts):
            if p in ('-p', '-P') and i + 1 < len(parts):
                port = int(parts[i + 1])
        s = [p for p in parts[1:] if '@' in p][-1]
    if s.count(':') == 1 and not s.endswith(':'):
        s, _, tail = s.rpartition(':')
        if tail.isdigit():
            port = int(tail)
    user, _, host = s.rpartition('@')
    return {'kind': 'ssh', 'host': host, 'user': user or 'root', 'port': port}


def deploy(id=None, ssh=None, instance=None, docker=None, name=None, hours=1,
           confirm=False, image=None, profile='lite', ports=None, gpus=None,
           bootstrap_now=True, wait=False, keys=None):
    """Get a mod container running somewhere, by whichever road is open.

    id=provider:ref   rent that offer, then install into it
    instance=…        adopt a rental you already have
    ssh=user@host:22  adopt any box you can already reach
    docker=1          a container on this machine (nothing is rented)
    """
    keys = keys or {}
    image = image or DEFAULT_IMAGE

    if docker:
        return _deploy_docker(name=name, image=(docker if isinstance(docker, str)
                                                and ':' in str(docker) else image),
                              ports=ports, gpus=gpus, profile=profile,
                              bootstrap_now=bootstrap_now, wait=wait)
    if ssh:
        node = _new(name=name, target=_ssh_from(ssh), source='adopted')
    elif instance or id:
        node = _from_market(id=id, instance=instance, name=name, hours=hours,
                            confirm=confirm, image=image, keys=keys)
        if isinstance(node, dict) and node.get('needs_confirm'):
            return node
    else:
        raise NodeError('deploy needs one of: id=provider:ref, instance=provider:ref, '
                        'ssh=user@host, docker=1')

    if bootstrap_now and node.get('state') != 'booting':
        try:
            return bootstrap(node['id'], profile=profile, wait=wait)
        except NodeError as e:
            return {**_public(get(node['id'])), 'error': str(e)}
    return _public(node)


def _deploy_docker(name=None, image=DEFAULT_IMAGE, ports=None, gpus=None,
                   profile='lite', bootstrap_now=True, wait=False):
    """A container on this machine — the same node type, nothing rented."""
    nid_node = _new(name=name, target={'kind': 'docker', 'container': 'pending'},
                    source='docker', image=image, usd_hr=0.0)
    container = f'mod-{nid_node["id"]}'
    pub = {}
    for cport in _port_list(ports):
        pub[free_port(taken=pub)] = cport
    argv = ['docker', 'run', '-d', '--name', container, '--restart', 'unless-stopped']
    for host_port, cport in pub.items():
        argv += ['-p', f'127.0.0.1:{host_port}:{cport}']
    if gpus:
        argv += ['--gpus', str(gpus)]
    argv += [image, 'sleep', 'infinity']
    r = subprocess.run(argv, capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        nid_node.update(state='error', error=f'docker run: {r.stderr[:400]}')
        save(nid_node)
        raise NodeError(f'docker run failed: {r.stderr[:400]}')
    nid_node.update(target={'kind': 'docker', 'container': container},
                    container=container, ports=pub, state='new')
    save(nid_node)
    if bootstrap_now:
        return bootstrap(nid_node['id'], profile=profile, wait=wait)
    return _public(nid_node)


def _port_list(ports):
    if ports in (None, '', False):
        return [8000, 3000]
    if isinstance(ports, (int, float)):
        return [int(ports)]
    if isinstance(ports, str):
        return [int(p) for p in ports.replace(',', ' ').split() if p.strip().isdigit()]
    return [int(p) for p in ports]


def _from_market(id=None, instance=None, name=None, hours=1, confirm=False,
                 image=None, keys=None):
    """Rent (or look up) a box, then work out how to get inside it."""
    from hub import Hub
    h = Hub(keys=keys or {})
    if id and not instance:
        pub = keypair()['public']
        rented = h.rent(id, hours=hours, confirm=confirm, image=image,
                        ssh_key=pub, name=name or 'mod')
        if rented.get('needs_confirm'):
            return rented
        inst_id = rented.get('id') or id
    else:
        inst_id = instance
        rented = h.status(inst_id)

    node = _new(name=name or rented.get('name'), target={'kind': 'provider',
                                                         'instance': inst_id,
                                                         'keys': keys or {}},
                source='market', instance=inst_id,
                usd_hr=rented.get('usd_hr'), provider=inst_id.split(':')[0])
    ssh_line = rented.get('ssh')
    if not ssh_line:                    # young rentals hand out SSH late
        for _ in range(3):
            time.sleep(4)
            try:
                live = h.status(inst_id)
            except ProviderError:
                break
            if live.get('ssh'):
                ssh_line, rented = live['ssh'], live
                break
    if ssh_line:
        node['target'] = _ssh_from(ssh_line)
        node['ssh'] = ssh_line
    else:
        node['state'] = 'booting'
        node['note'] = ('no SSH line yet — the rental is still starting. '
                        f'`m compute-fork/node_sync id={node["id"]}` when it is up')
    node['instance_record'] = {k: rented.get(k) for k in
                               ('id', 'status', 'usd_hr', 'gpu', 'gpus', 'url')}
    return save(node)


def sync(nid, keys=None):
    """Re-ask the market for the SSH line a young rental had not issued yet."""
    from hub import Hub
    node = get(nid)
    inst = node.get('instance')
    if not inst:
        return probe(nid)
    live = Hub(keys=keys or {}).status(inst)
    if live.get('ssh'):
        node['target'] = _ssh_from(live['ssh'])
        node['ssh'] = live['ssh']
    node['instance_record'] = {k: live.get(k) for k in
                               ('id', 'status', 'usd_hr', 'gpu', 'gpus', 'url')}
    node['usd_hr'] = live.get('usd_hr') or node.get('usd_hr')
    save(node)
    return probe(nid) if node.get('ssh') else _public(node)


def destroy(nid, release=False, keys=None):
    """Stop the container. With release=1, stop the rental — that ends billing."""
    node = get(nid)
    out = {'node': nid, 'released': False}
    t = None
    try:
        t = transport(node)
    except NodeError:
        pass
    if isinstance(t, Docker) and not t.host:
        r = subprocess.run(['docker', 'rm', '-f', t.container],
                           capture_output=True, text=True, timeout=120)
        out['container'] = r.stdout.strip() or r.stderr.strip()
    elif isinstance(t, Docker):
        out['container'] = t.host_run(f'docker rm -f {t.container}', timeout=120)['out']
    if release and node.get('instance'):
        from hub import Hub
        try:
            out['release'] = Hub(keys=keys or {}).stop(node['instance'])
            out['released'] = True
        except ProviderError as e:
            out['release_error'] = e.dict()
    node['state'] = 'gone'
    node['destroyed'] = time.time()
    save(node)
    out['state'] = 'gone'
    out['hint'] = ('the rental is still billing — pass release=1 or '
                   f'`m compute-fork/stop id={node.get("instance")}`') \
        if node.get('instance') and not out['released'] else 'nothing is billing'
    return out
