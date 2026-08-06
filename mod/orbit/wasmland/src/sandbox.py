"""
Sandbox — start the node runner for one job and bring the result back.

The runtime (src/runtime/) makes a run *deterministic*. This file makes it
*contained*, which is a different job and needs the operating system rather
than JavaScript. Strongest first, each degrading gracefully so the module still
works on a laptop without root:

    network   `unshare -n` gives the child an empty network namespace, so a
              module cannot phone home, fetch a different answer on Tuesday,
              or reach anything else on this box. Needs CAP_SYS_ADMIN.
    user      the child drops to `nobody` when the API runs as root.
    memory    node's --max-old-space-size caps the heap; RLIMIT_AS is a far
              looser backstop against runaway mmap (see DEFAULT_LIMITS for why
              it cannot be tight). A module that allocates without end dies
              instead of taking the box with it.
    cpu       RLIMIT_CPU, plus a wall-clock kill here. Wasm cannot be
              interrupted from the outside, so killing the process it runs in
              is the only real timeout there is. The browser venue does the
              same thing by terminating the Worker.
    files     RLIMIT_FSIZE 0 — nothing it runs can write anything.
    cwd       /tmp, with a scrubbed environment: no NODE_PATH, no repo.

`capabilities()` reports what is actually in force. A module that claims a
sandbox it doesn't have is worse than one that admits it runs unconfined, so
the API publishes this and the receipt records it.
"""
import hashlib
import json
import os
import resource
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

RUNNER = str(Path(__file__).resolve().parent / 'runtime' / 'run.mjs')

DEFAULT_LIMITS = {
    'timeout': 30,          # wall-clock seconds before the child is killed
    'cpu_seconds': 20,
    'memory_mb': 512,       # JS heap ceiling — node's --max-old-space-size
    # RLIMIT_AS, and deliberately enormous. V8 *reserves* around 10GB of
    # guard region for every wasm memory it creates; the pages are never
    # touched, but the reservation is address space and RLIMIT_AS counts it.
    # Set this to anything a human would call a memory limit and every wasm
    # module on the box fails to instantiate with "Out of memory: wasm memory",
    # which reads like a broken module and isn't. It is a runaway-mmap backstop
    # and nothing finer; memory_mb and the clock are the real limits.
    'address_space_gb': 16,
    'output_bytes': 1 << 20,
}

_UNSHARE: Optional[bool] = None


class RunFailed(Exception):
    """The module refused to run, blew a limit, or returned garbage.

    Not an internal error: user code failing is the expected outcome of
    running user code, and the caller wants the module's own message.
    """


def _can_unshare() -> bool:
    if not shutil.which('unshare'):
        return False
    try:
        r = subprocess.run(['unshare', '-n', 'true'], capture_output=True, timeout=10)
        return r.returncode == 0
    except Exception:
        return False


def network_isolated() -> bool:
    global _UNSHARE
    if _UNSHARE is None:
        _UNSHARE = _can_unshare()
    return _UNSHARE


def node_version() -> Optional[str]:
    node = shutil.which('node')
    if not node:
        return None
    try:
        r = subprocess.run([node, '--version'], capture_output=True, text=True, timeout=10)
        return r.stdout.strip() or None
    except Exception:
        return None


def capabilities(user: str = 'nobody') -> Dict[str, Any]:
    """What this box actually enforces — asked of the box, not assumed."""
    import pwd
    try:
        pwd.getpwnam(user)
        user_known = True
    except KeyError:
        user_known = False
    node = node_version()
    return {
        'venue': 'server',
        'node': node,
        'ok': bool(node),
        'network_isolated': network_isolated(),
        'drops_privileges': os.geteuid() == 0 and user_known,
        'runs_as_root': os.geteuid() == 0 and not user_known,
        'sandbox_user': user if user_known else None,
        'rlimits': ['cpu', 'address_space', 'file_size=0', 'core=0'],
        'heap_cap': 'node --max-old-space-size',
        'note': (None if network_isolated() else
                 'no CAP_SYS_ADMIN — the child shares this box\'s network. '
                 'Determinism still holds (the runtime offers no sockets), '
                 'but containment is weaker than it reads.'),
    }


def _stage_runtime() -> str:
    """Put the runtime somewhere the sandboxed child can actually read it.

    The child runs as `nobody`, and the repo lives under /root — so the module
    that runs everything is the one thing the runner cannot open, and node
    reports it as MODULE_NOT_FOUND three frames from anything meaningful.
    The runtime is therefore staged under /tmp, in a directory named for the
    hash of its own contents: no staleness to track, and a rebuild is just a
    new name.

    /tmp is shared, so the staged copy is checked rather than assumed. It must
    be ours, unwritable by anyone else, and byte-identical to the source —
    otherwise a stranger could swap the code that decides whether two runs
    agree, which is the one thing in this module nobody else may touch.
    """
    source = Path(RUNNER).parent
    files = sorted(p for p in source.glob('*.mjs'))
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    staged = Path('/tmp') / f'wasmland-runtime-{digest.hexdigest()[:16]}'

    def usable(target: Path) -> bool:
        try:
            info = target.stat()
        except FileNotFoundError:
            return False
        if info.st_uid != os.geteuid() or info.st_mode & 0o022:
            return False        # somebody else owns or can write it
        return all((target / p.name).is_file()
                   and (target / p.name).read_bytes() == p.read_bytes()
                   for p in files)

    if not usable(staged):
        import tempfile
        try:
            staged.mkdir(mode=0o755, exist_ok=True)
            if not (staged.stat().st_uid == os.geteuid()
                    and not staged.stat().st_mode & 0o022):
                raise PermissionError(staged)
        except (OSError, PermissionError):
            # Someone got there first with a directory we don't control. Take
            # a fresh private one rather than trusting theirs.
            staged = Path(tempfile.mkdtemp(prefix='wasmland-runtime-'))
            staged.chmod(0o755)
        for path in files:
            target = staged / path.name
            target.write_bytes(path.read_bytes())
            target.chmod(0o644)
    return str(staged / 'run.mjs')


def _preexec(limits: Dict[str, Any]):
    """Runs in the child, between fork and exec.

    Limits only — the privilege drop deliberately isn't here. Dropping to
    `nobody` before exec means `unshare -n` runs as nobody, which has no
    CAP_SYS_ADMIN, and network isolation fails with "Operation not permitted".
    So the order is unshare first, then setpriv drops privileges, then node.
    rlimits survive both execs, which is why they can be set this early.
    """
    def apply():
        resource.setrlimit(resource.RLIMIT_CPU,
                           (limits['cpu_seconds'], limits['cpu_seconds']))
        space = int(limits['address_space_gb']) * 1024 ** 3
        try:
            resource.setrlimit(resource.RLIMIT_AS, (space, space))
        except (ValueError, OSError):
            # Losing the backstop is worth more than losing the runner; the
            # wall clock and RLIMIT_CPU still bound the damage.
            pass
        resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        os.setsid()
    return apply


def _drops_to(user: str) -> Optional[List[str]]:
    """The setpriv prefix that runs the child as `user`, if that's possible."""
    import grp
    import pwd
    if os.geteuid() != 0 or not shutil.which('setpriv'):
        return None
    try:
        pw = pwd.getpwnam(user)
    except KeyError:
        return None
    group = grp.getgrgid(pw.pw_gid).gr_name
    return ['setpriv', f'--reuid={user}', f'--regid={group}', '--clear-groups']


def run(job: Dict[str, Any], limits: Optional[Dict[str, Any]] = None,
        user: str = 'nobody') -> Dict[str, Any]:
    """Execute one job in a child process. Returns the runner's result dict."""
    if not shutil.which('node'):
        raise RunFailed('node is not on PATH — the server venue needs it '
                        '(the browser venue does not)')
    lim = {**DEFAULT_LIMITS, **(limits or {})}
    payload = json.dumps({**job, 'limits': {'output_bytes': lim['output_bytes']}})

    # unshare (needs root) → setpriv (spends it) → node (has neither).
    cmd = (['unshare', '-n'] if network_isolated() else []) \
        + (_drops_to(user) or []) \
        + ['node', '--no-warnings',
           f'--max-old-space-size={int(lim["memory_mb"])}', _stage_runtime()]
    env = {'PATH': '/usr/bin:/bin:/usr/local/bin', 'HOME': '/tmp',
           'LANG': 'C.UTF-8', 'TMPDIR': '/tmp', 'NODE_OPTIONS': ''}

    try:
        proc = subprocess.run(cmd, input=payload, capture_output=True, text=True,
                              timeout=lim['timeout'], env=env, cwd='/tmp',
                              preexec_fn=_preexec(lim))
    except subprocess.TimeoutExpired:
        raise RunFailed(f'timed out after {lim["timeout"]}s — killed')

    if not proc.stdout.strip():
        killed = -proc.returncode if proc.returncode < 0 else None
        detail = (f'killed by signal {killed}' if killed
                  else (proc.stderr or '').strip()[-400:])
        raise RunFailed(f'no result ({detail or "unknown failure"})')
    try:
        out = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise RunFailed(f'runner wrote non-JSON: {proc.stdout[:200]!r}')
    if not out.get('ok'):
        raise RunFailed(out.get('error', 'run failed'))
    return out
