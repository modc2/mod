"""Can this machine serve, and if not, which line of install.md is the reason.

FreeToken's stated requirements are Linux x86_64, an NVIDIA GPU on driver
r580+ (CUDA 13), and Python >= 3.10, with a CUDA 13 toolkit on PATH because
the kernels are JIT-compiled on first use. Every check below is one of those,
answered from this machine, with what was found next to what was wanted.

Two numbers that are not in the requirements list are reported anyway, because
they decide whether a model fits rather than whether it runs: host RAM (the
offload backend keeps the experts there) and free disk (a checkpoint lands
there first).
"""
from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import sys
from typing import Any, Dict, List

from src import state

MIN_PYTHON = (3, 10)
MIN_DRIVER = 580          # r580+, per docs/install.md
MIN_CUDA = 13


def _run(argv: List[str], timeout: float = 8.0) -> str:
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return ''
    return done.stdout if done.returncode == 0 else ''


def gpus() -> List[Dict[str, Any]]:
    """Every NVIDIA GPU nvidia-smi will admit to. Empty list is a normal answer."""
    out = _run(['nvidia-smi', '--query-gpu=name,driver_version,memory.total,compute_cap',
                '--format=csv,noheader,nounits'])
    found = []
    for line in out.strip().splitlines():
        parts = [p.strip() for p in line.split(',')]
        if len(parts) < 3:
            continue
        found.append({'name': parts[0], 'driver': parts[1],
                      'vram_gb': round(float(parts[2]) / 1024, 1) if parts[2].replace('.', '').isdigit() else None,
                      'compute_capability': parts[3] if len(parts) > 3 else None})
    return found


def cuda_toolkit() -> Dict[str, Any]:
    nvcc = shutil.which('nvcc')
    version = None
    if nvcc:
        match = re.search(r'release (\d+)\.(\d+)', _run([nvcc, '--version']))
        if match:
            version = f'{match.group(1)}.{match.group(2)}'
    return {'nvcc': nvcc, 'version': version,
            'major': int(version.split('.')[0]) if version else None}


def host_ram_gb() -> float:
    try:
        with open('/proc/meminfo') as fh:
            for line in fh:
                if line.startswith('MemTotal:'):
                    return round(int(line.split()[1]) / 1024 / 1024, 1)
    except OSError:
        pass
    return 0.0


def free_disk_gb(path: str = None) -> float:
    try:
        usage = shutil.disk_usage(path or str(state.home()))
        return round(usage.free / 1024 ** 3, 1)
    except OSError:
        return 0.0


def _check(name: str, ok: bool, found: Any, want: str, blocking: bool = True,
           note: str = '') -> Dict[str, Any]:
    return {'check': name, 'ok': bool(ok), 'found': found, 'want': want,
            'blocking': bool(blocking), 'note': note}


def checks() -> List[Dict[str, Any]]:
    cards = gpus()
    toolkit = cuda_toolkit()
    driver = max((float(re.sub(r'[^\d.]', '', g['driver']) or 0) for g in cards),
                 default=0.0)
    machine = platform.machine()
    ram, disk = host_ram_gb(), free_disk_gb()
    return [
        _check('os', platform.system() == 'Linux', platform.system(), 'Linux',
               note='the wheels are Linux x86_64 only'),
        _check('arch', machine in ('x86_64', 'AMD64'), machine, 'x86_64'),
        _check('python', sys.version_info[:2] >= MIN_PYTHON,
               '.'.join(map(str, sys.version_info[:3])), '>= 3.10'),
        _check('nvidia_gpu', bool(cards), [g['name'] for g in cards] or None,
               'an NVIDIA GPU (RTX 30/40/50 have native support)'),
        _check('driver', driver >= MIN_DRIVER, driver or None, f'>= r{MIN_DRIVER}',
               note='r580+ is what ships CUDA 13'),
        _check('cuda_toolkit', (toolkit['major'] or 0) >= MIN_CUDA,
               toolkit['version'], f'>= {MIN_CUDA}.0 with nvcc on PATH',
               note='kernels are JIT-compiled on first use'),
        _check('uv', bool(shutil.which('uv')), shutil.which('uv'), 'recommended',
               blocking=False, note='plain pip + venv also works'),
        _check('host_ram', ram >= 32, f'{ram} GB', '>= 32 GB for expert offload',
               blocking=False, note='the offload backend keeps the experts in host RAM'),
        _check('free_disk', disk >= 50, f'{disk} GB', '>= 50 GB for a checkpoint',
               blocking=False, note=f'measured at {state.home()}'),
    ]


def report() -> Dict[str, Any]:
    """The verdict, and the exact list of what is in the way."""
    result = checks()
    blocking = [c for c in result if c['blocking'] and not c['ok']]
    advisory = [c for c in result if not c['blocking'] and not c['ok']]
    can = not blocking
    return {
        'can_serve_here': can,
        'verdict': ('this machine can run a FreeToken engine' if can else
                    'this machine cannot host the engine — point the module at one that can '
                    '(m freetoken/add_box name=gpu url=http://host:1919)'),
        'blocking': [c['check'] for c in blocking],
        'advisory': [c['check'] for c in advisory],
        'checks': result,
        'gpus': gpus(),
        'cuda': cuda_toolkit(),
        'host': {'os': platform.system(), 'arch': platform.machine(),
                 'python': '.'.join(map(str, sys.version_info[:3])),
                 'cpus': os.cpu_count(), 'ram_gb': host_ram_gb(),
                 'free_disk_gb': free_disk_gb()},
    }
