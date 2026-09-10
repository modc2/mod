"""Getting `ft` onto this machine, into a venv of its own.

FreeToken pins torch to a single minor (>=2.11,<2.12) and pins two kernel
packages exactly, so installing it into a shared environment is a good way to
break a shared environment. This module installs it into ~/.mod/freetoken/venv
and looks there first; an `ft` already on PATH is used as-is if there is no
managed venv, so an existing install is never duplicated.

The install downloads CUDA wheels and takes minutes, so it runs detached with
its output in a log, and `status()` reports on it while it goes.
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from src import state

PACKAGE = 'freetoken'
LOG = 'install.log'
PID = 'install.pid'


def venv_bin(name: str = 'ft') -> Optional[str]:
    candidate = state.venv() / 'bin' / name
    return str(candidate) if candidate.exists() else None


def ft_bin() -> Optional[str]:
    """The `ft` this module drives: the managed venv first, then PATH."""
    return venv_bin('ft') or shutil.which('ft')


def version() -> Optional[str]:
    binary = ft_bin()
    if not binary:
        return None
    try:
        done = subprocess.run([binary, '--version'], capture_output=True,
                              text=True, timeout=60)
    except (subprocess.SubprocessError, OSError):
        return None
    return (done.stdout or done.stderr).strip() or None


def status() -> Dict[str, Any]:
    binary = ft_bin()
    running = _install_pid()
    return {
        'installed': bool(binary),
        'ft': binary,
        'version': version() if binary else None,
        'managed_venv': str(state.venv()) if state.venv().exists() else None,
        'from': ('managed venv' if venv_bin() else 'PATH' if binary else None),
        'installing': running is not None,
        'install_pid': running,
        'log': str(state.logs() / LOG),
        'tail': state.tail(state.logs() / LOG, 12) if (state.logs() / LOG).exists() else '',
    }


def _install_pid() -> Optional[int]:
    raw = state.logs() / PID
    if not raw.exists():
        return None
    try:
        pid = int(raw.read_text().strip())
        os.kill(pid, 0)
        return pid
    except (ValueError, OSError):
        raw.unlink(missing_ok=True)
        return None


def plan(source: bool = False, accel: bool = True, upgrade: bool = False,
         ref: str = None) -> Dict[str, Any]:
    """The commands `install()` would run. Printed by `m freetoken/install dry=1`."""
    target = state.venv()
    extra = f'{PACKAGE}[accel]' if accel else PACKAGE
    uv = shutil.which('uv')
    steps: List[List[str]] = []
    if not (target / 'bin' / 'python').exists():
        steps.append([uv, 'venv', str(target)] if uv
                     else [sys.executable, '-m', 'venv', str(target)])
    pip = ([uv, 'pip', 'install', '--python', str(target / 'bin' / 'python')] if uv
           else [str(target / 'bin' / 'pip'), 'install'])
    if upgrade:
        pip = pip + ['--upgrade']
    if source:
        checkout = state.home() / 'FreeToken'
        if not checkout.exists():
            steps.append(['git', 'clone', 'https://github.com/FlashML-org/FreeToken.git',
                          str(checkout)])
        if ref:
            steps.append(['git', '-C', str(checkout), 'checkout', ref])
        steps.append(pip + ['-e', f'{checkout}[accel]' if accel else str(checkout)])
    else:
        steps.append(pip + [extra])
    return {'venv': str(target), 'uv': bool(uv), 'source': bool(source),
            'accel': bool(accel), 'steps': [' '.join(s) for s in steps],
            'argv': steps, 'log': str(state.logs() / LOG)}


def install(source: bool = False, accel: bool = True, upgrade: bool = False,
            ref: str = None, dry: bool = False) -> Dict[str, Any]:
    """Run the plan detached, appending to install.log. Returns immediately."""
    recipe = plan(source=source, accel=accel, upgrade=upgrade, ref=ref)
    if dry:
        return {'dry_run': True, **recipe}
    if _install_pid():
        return {'ok': False, 'why': 'an install is already running',
                'pid': _install_pid(), 'log': recipe['log']}
    script = ' && '.join(_quote(argv) for argv in recipe['argv'])
    log = state.logs() / LOG
    handle = log.open('ab')
    handle.write(f'\n=== {script}\n'.encode())
    handle.flush()
    process = subprocess.Popen(['/bin/sh', '-c', script], stdout=handle,
                               stderr=subprocess.STDOUT, start_new_session=True)
    (state.logs() / PID).write_text(str(process.pid))
    return {'ok': True, 'pid': process.pid, 'steps': recipe['steps'],
            'log': recipe['log'],
            'note': 'CUDA wheels are large; watch it with m freetoken/install_log'}


def cancel() -> Dict[str, Any]:
    pid = _install_pid()
    if not pid:
        return {'ok': False, 'why': 'nothing installing'}
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except OSError as exc:
        return {'ok': False, 'why': str(exc)}
    (state.logs() / PID).unlink(missing_ok=True)
    return {'ok': True, 'killed': pid}


def log(lines: int = 60) -> Dict[str, Any]:
    return {'log': str(state.logs() / LOG), 'installing': _install_pid() is not None,
            'tail': state.tail(state.logs() / LOG, int(lines))}


def _quote(argv: List[str]) -> str:
    import shlex
    return ' '.join(shlex.quote(str(a)) for a in argv)


def run(sub: List[str], timeout: float = 300.0) -> Dict[str, Any]:
    """One `ft ...` invocation, captured. Used for ctl/bench/checkpoint/launch."""
    binary = ft_bin()
    if not binary:
        return {'ok': False, 'why': 'ft is not installed here — m freetoken/install',
                'preflight': 'm freetoken/preflight'}
    argv = [binary] + [str(a) for a in sub]
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {'ok': False, 'why': f'timed out after {timeout}s', 'argv': argv}
    except OSError as exc:
        return {'ok': False, 'why': str(exc), 'argv': argv}
    return {'ok': done.returncode == 0, 'argv': argv, 'code': done.returncode,
            'out': (done.stdout or '')[-8000:], 'err': (done.stderr or '')[-4000:]}
