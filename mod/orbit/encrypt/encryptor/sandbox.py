"""
Sandbox — start runner.py for one circuit call and bring the bytes back.

Isolation, strongest first, each degrading gracefully so the module still works
on a laptop without root:

    network   `unshare -n` gives the child an empty network namespace, so a
              circuit cannot phone the key home. Needs CAP_SYS_ADMIN.
    user      the child drops to `nobody` when the API runs as root.
    cpu/mem   RLIMIT_CPU / RLIMIT_AS inside the child.
    files     RLIMIT_FSIZE 0 — the circuit cannot write anything.
    time      wall-clock timeout enforced here; the child is killed.
    imports   `python3 -I -B` and a scrubbed env: no PYTHONPATH, no mod repo.

`capabilities()` reports what is actually in force — /status shows it rather
than claiming a sandbox we don't have.
"""
import base64
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

RUNNER = str(Path(__file__).with_name('runner.py'))

DEFAULT_LIMITS = {'timeout': 15, 'cpu_seconds': 10, 'memory_mb': 512}


class CircuitError(Exception):
    """The circuit refused to run, blew a limit, or returned garbage."""


def _can_unshare() -> bool:
    if not shutil.which('unshare'):
        return False
    try:
        r = subprocess.run(['unshare', '-n', 'true'], capture_output=True, timeout=10)
        return r.returncode == 0
    except Exception:
        return False


_UNSHARE: Optional[bool] = None


def network_isolated() -> bool:
    global _UNSHARE
    if _UNSHARE is None:
        _UNSHARE = _can_unshare()
    return _UNSHARE


def capabilities(user: str = 'nobody') -> dict:
    import pwd
    try:
        pwd.getpwnam(user)
        user_known = True
    except KeyError:
        user_known = False
    return {
        'network_isolated': network_isolated(),
        'drops_privileges': os.geteuid() == 0 and user_known,
        'runs_as_root': os.geteuid() == 0 and not user_known,
        'sandbox_user': user if user_known else None,
        'rlimits': ['cpu', 'address_space', 'file_size=0', 'core=0'],
    }


def run(source: str, op: str, data: bytes = b'', key: bytes = b'',
        params: Optional[dict] = None, limits: Optional[dict] = None,
        user: str = 'nobody') -> dict:
    """Run one circuit operation. Returns the runner's payload dict.

    Raises CircuitError with the circuit's own message on failure — user code
    failing is an expected outcome here, not an exception to hide."""
    lim = {**DEFAULT_LIMITS, **(limits or {})}
    job = json.dumps({
        'source': source,
        'op': op,
        'data_b64': base64.b64encode(data).decode(),
        'key_b64': base64.b64encode(key).decode(),
        'params': params or {},
        'limits': {'cpu_seconds': lim['cpu_seconds'], 'memory_mb': lim['memory_mb']},
        'user': user,
    })

    cmd = [sys.executable, '-I', '-B', RUNNER]
    if network_isolated():
        cmd = ['unshare', '-n'] + cmd
    env = {'PATH': '/usr/bin:/bin', 'HOME': '/tmp', 'LANG': 'C.UTF-8', 'TMPDIR': '/tmp'}

    try:
        proc = subprocess.run(cmd, input=job, capture_output=True, text=True,
                              timeout=lim['timeout'], env=env, cwd='/tmp')
    except subprocess.TimeoutExpired:
        raise CircuitError(f'circuit timed out after {lim["timeout"]}s')

    if not proc.stdout.strip():
        killed = -proc.returncode if proc.returncode < 0 else None
        detail = f'killed by signal {killed}' if killed else (proc.stderr or '').strip()[-400:]
        raise CircuitError(f'circuit produced no output ({detail or "unknown failure"})')
    try:
        out = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise CircuitError(f'circuit wrote non-JSON to stdout: {proc.stdout[:200]!r}')
    if not out.get('ok'):
        raise CircuitError(out.get('error', 'circuit failed'))
    return out


def transform(source: str, op: str, data: bytes, key: bytes,
              params: Optional[dict] = None, **kw) -> bytes:
    """encrypt/decrypt `data`, returning the resulting bytes."""
    out = run(source, op, data=data, key=key, params=params, **kw)
    return base64.b64decode(out['data_b64'])


def selftest(source: str, key: bytes, params: Optional[dict] = None, **kw) -> dict:
    """Roundtrip a known sample through the circuit — the upload gate."""
    return run(source, 'selftest', key=key, params=params, **kw)
