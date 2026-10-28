"""nanovm — micro-VMs out of nothing but the Linux kernel. Open source, Go, zero deps.

Like boxd reads Letterboxd with no key, this boots sandboxes with no Docker:
a nanovm is a process behind its own PID/UTS/mount/IPC (and optionally net)
namespaces, its own root filesystem via pivot_root, and a cgroup v2 slice for
memory/cpu/pid caps. The whole runtime is one static MIT-licensed Go binary
built from src/ — stdlib only, nothing downloaded, ~1000 lines you can read.

    m nanovm                                  # null call → info()
    m nanovm/build                            # compile src/ → src/bin/nanovm
    m nanovm/run name=demo cmd='sleep 300' mem=64M
    m nanovm/vms                              # every vm, liveness refreshed
    m nanovm/exec name=demo cmd='ps aux'      # run inside (nsenter)
    m nanovm/logs name=demo                   # tail a detached vm
    m nanovm/stop name=demo                   # SIGTERM then SIGKILL
    m nanovm/rm name=demo                     # stop and forget
    m nanovm/rootfs dir=/tmp/root             # minimal busybox rootfs helper
    m nanovm/serve                            # HTTP API + console on :51230
    m nanovm/test                             # offline tests
    m nanovm/kill                             # stop the server

This is the anchor file: the orbit loader imports it by path and instantiates
``Mod``. Everything the module exposes to the CLI, the gateway and other
modules is a public method on this class — each one is a thin shell onto the
Go binary, so the CLI, the API and the library never disagree.
"""

import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BIN = os.path.join(HERE, 'src', 'bin', 'nanovm')
GO = shutil.which('go') or '/usr/local/go/bin/go'


class Mod:
    description = """
    nanovm — micro-VMs out of nothing but the Linux kernel: namespaces,
    pivot_root and cgroup v2, driven by one static Go binary with zero
    dependencies (MIT). Default boot is a read-only lens over the host with a
    private /proc and /tmp; point rootfs= at any directory (busybox helper,
    docker export, debootstrap) to pivot into it. Library + CLI + HTTP API.
    """

    def __init__(self, port=None, **kwargs):
        self.dir = HERE
        cfg = self.config()
        self.port = int(port or os.environ.get('PORT') or cfg.get('port', 51230))
        self.base = cfg.get('base_path', '/nanovm')

    # ── plumbing ─────────────────────────────────────────────────

    def config(self):
        try:
            with open(os.path.join(HERE, 'config.json')) as f:
                return json.load(f)
        except Exception:
            return {}

    def _bin(self, *args, timeout=60):
        """Every verb goes through the one binary. Build it if it is missing —
        a fresh checkout should answer `m nanovm/run`, not demand a ritual."""
        if not os.path.exists(BIN):
            built = self.build()
            if not built.get('ok'):
                return built
        r = subprocess.run([BIN, *args], capture_output=True, text=True, timeout=timeout)
        out = r.stdout.strip()
        if r.returncode != 0:
            return {'ok': False, 'error': (r.stderr or out).strip(), 'exit_code': r.returncode}
        try:
            return json.loads(out)
        except ValueError:
            return {'ok': True, 'output': out}

    def info(self):
        """Null call — what this module is, and every surface it has."""
        cfg = self.config()
        return {'name': 'nanovm', 'description': self.description.strip(),
                'version': cfg.get('version'), 'port': self.port,
                'app': f'http://localhost:{self.port}{self.base}/',
                'binary': BIN, 'built': os.path.exists(BIN),
                'license': 'MIT — pure Go, stdlib only, nothing downloaded',
                'endpoints': cfg.get('endpoints', {})}

    forward = info

    def health(self):
        """Liveness — answers whether the server is up or not."""
        import urllib.request
        built = os.path.exists(BIN)
        try:
            with urllib.request.urlopen(f'http://localhost:{self.port}/health', timeout=3) as r:
                srv = json.load(r)
        except Exception:
            srv = None
        return {'ok': built, 'built': built, 'port': self.port,
                'server': srv or 'not running — m nanovm/serve'}

    def readme(self):
        """The project README."""
        for name in ('README.md', 'skill.md'):
            p = os.path.join(HERE, name)
            if os.path.exists(p):
                with open(p) as f:
                    return f.read()
        return None

    def build(self):
        """Compile src/ into the one static binary. Needs a Go toolchain."""
        if not os.path.exists(GO):
            return {'ok': False, 'error': f'no go toolchain at {GO} — install Go 1.22+'}
        r = subprocess.run([GO, 'build', '-o', 'bin/nanovm', './cmd/nanovm'],
                           cwd=os.path.join(HERE, 'src'), capture_output=True, text=True)
        return {'ok': r.returncode == 0, 'binary': BIN,
                'output': (r.stdout + r.stderr).strip() or 'built'}

    # ── the machines ─────────────────────────────────────────────
    #
    # Each verb is one line onto the binary: serve.py-style duplication is
    # avoided by the Go server and this anchor dispatching into the same code.

    def run(self, name=None, cmd=None, rootfs=None, mem=None, cpu=None,
            pids=None, net=False, rw=None, env=None, dir=None):
        """Boot a vm, detached. cmd is a shell line; caps are optional."""
        if not name or not cmd:
            return {'ok': False, 'error': "name= and cmd= are required — m nanovm/run name=demo cmd='sleep 300'"}
        args = ['run', '-d']
        for flag, val in (('--rootfs', rootfs), ('--mem', mem), ('--cpu', cpu),
                          ('--pids', pids), ('--rw', rw), ('--env', env), ('--dir', dir)):
            if val:
                args += [flag, str(val)]
        if net and str(net).lower() not in ('false', '0', 'no'):
            args.append('--net')
        args += [str(name), 'sh', '-c', str(cmd)]
        return self._bin(*args)

    def vms(self):
        """Every vm this box knows, liveness refreshed against /proc."""
        return self._bin('ls', '--json')

    ls = vms

    def exec(self, name=None, cmd=None):
        """Run a command inside a running vm (util-linux nsenter does setns)."""
        if not name or not cmd:
            return {'ok': False, 'error': "name= and cmd= are required"}
        st = self._bin('exec', str(name), 'sh', '-c', str(cmd))
        return st

    def logs(self, name=None, tail=100):
        """The last lines of a detached vm's stdout+stderr."""
        if not name:
            return {'ok': False, 'error': 'name= is required'}
        return self._bin('logs', str(name), '-n', str(tail))

    def stop(self, name=None):
        """SIGTERM pid 1 of the namespace, SIGKILL after three seconds."""
        if not name:
            return {'ok': False, 'error': 'name= is required'}
        return self._bin('stop', str(name))

    def rm(self, name=None):
        """Stop and forget: state, log, cgroup slice, mount dir."""
        if not name:
            return {'ok': False, 'error': 'name= is required'}
        return self._bin('rm', str(name))

    def rootfs(self, dir=None):
        """Assemble a minimal busybox rootfs — enough to boot `sh` pivoted."""
        if not dir:
            return {'ok': False, 'error': 'dir= is required'}
        return self._bin('rootfs', str(dir))

    # ── surfaces ─────────────────────────────────────────────────

    def serve(self, port=None, background=True):
        """Run the HTTP API and the console on one port."""
        port = int(port or self.port)
        if not os.path.exists(BIN):
            built = self.build()
            if not built.get('ok'):
                return built
        if not background:
            os.execv(BIN, [BIN, 'serve', '--port', str(port)])
        proc = subprocess.Popen([BIN, 'serve', '--port', str(port)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                cwd=HERE)
        return {'pid': proc.pid, 'port': port,
                'app': f'http://localhost:{port}{self.base}/',
                'api': f'http://localhost:{port}/'}

    def kill(self, port=None):
        """Stop whatever holds the port. Targets the port, never a name —
        this box runs ~100 services and a pattern kill takes the fleet down."""
        port = int(port or self.port)
        out = subprocess.run(['bash', '-c', f'lsof -ti tcp:{port} || true'],
                             capture_output=True, text=True).stdout.split()
        for pid in out:
            subprocess.run(['kill', pid], capture_output=True)
        return {'port': port, 'killed': out}

    def test(self):
        """Run the module's tests (offline — no network, scratch data dir)."""
        r = subprocess.run([sys.executable, '-m', 'pytest', '-q', 'tests'],
                           cwd=HERE, capture_output=True, text=True)
        return {'ok': r.returncode == 0, 'output': (r.stdout + r.stderr)[-4000:]}
