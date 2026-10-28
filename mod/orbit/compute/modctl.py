#!/usr/bin/env python3
"""modctl — the mod protocol's control surface on a remote box.

This file is uploaded to every node this module bootstraps. It is the only
thing on the far side that knows what a module is: everything else in the
transport chain moves bytes. One JSON payload in (base64 on argv, so no shell
quoting can corrupt it), one JSON answer out between markers (so a login
banner, a pip warning or a CUDA notice cannot be mistaken for the result).

    python3 modctl.py <base64-json>       {"op": "mods"}

ops
    info    python, mod version, module count, uptime — the readiness check
    mods    every module on this node
    fns     one module's callable surface
    call    run a module function and return what it returned
    ps      what is running here (pm2 if present, else process list)
    ports   which of this box's ports are listening

Nothing here imports anything that is not either stdlib or mod itself, because
a node in the middle of `pip install` must still be able to answer `info`.
"""

import base64
import json
import os
import subprocess
import sys
import time

BEGIN, END = '<<<MODCTL', 'MODCTL>>>'
MOD_DIR = os.environ.get('MOD_DIR') or os.path.expanduser('~/mod')


def _mod():
    """Import mod from the node's checkout, from the directory it expects."""
    if MOD_DIR not in sys.path:
        sys.path.insert(0, MOD_DIR)
    os.chdir(MOD_DIR)
    import mod
    return mod


# ── ops ──────────────────────────────────────────────────────────────────

def op_info(**_):
    out = {'python': sys.version.split()[0], 'mod_dir': MOD_DIR,
           'exists': os.path.isdir(MOD_DIR), 'host': os.uname().nodename,
           'time': time.time()}
    try:
        with open('/proc/uptime') as f:
            out['uptime_s'] = round(float(f.read().split()[0]))
    except Exception:
        pass
    try:
        m = _mod()
        out['mod'] = True
        out['version'] = getattr(m, '__version__', None) or _version()
        out['modules'] = len(m.Mod().mods())
    except Exception as e:
        out['mod'] = False
        out['error'] = f'{type(e).__name__}: {e}'
    out['gpu'] = _gpu()
    return out


def _version():
    try:
        with open(os.path.join(MOD_DIR, 'config.json')) as f:
            return json.load(f).get('version')
    except Exception:
        return None


def _gpu():
    try:
        r = subprocess.run(['nvidia-smi', '--query-gpu=name,memory.total,utilization.gpu',
                            '--format=csv,noheader'], capture_output=True, text=True,
                           timeout=15)
        rows = [l.strip() for l in r.stdout.splitlines() if l.strip()]
        return rows or None
    except Exception:
        return None


def op_mods(**_):
    return {'mods': _mod().Mod().mods()}


def op_fns(mod=None, **_):
    if not mod:
        raise ValueError('fns: mod is required')
    m = _mod()
    obj = m.mod(mod)
    fns = []
    for name in sorted(dir(obj)):
        if name.startswith('_'):
            continue
        try:
            attr = getattr(obj, name)
        except Exception:
            continue
        if callable(attr):
            fns.append({'name': name, 'doc': (attr.__doc__ or '').strip().split('\n')[0]})
    return {'mod': mod, 'fns': fns}


def op_call(mod=None, fn='forward', args=None, kwargs=None, init=None, **_):
    """Instantiate a module here and call one of its functions."""
    if not mod:
        raise ValueError('call: mod is required')
    m = _mod()
    cls = m.mod(mod)
    obj = cls(**(init or {})) if isinstance(cls, type) else cls
    target = getattr(obj, fn, None)
    if target is None:
        raise AttributeError(f'{mod} has no fn "{fn}"')
    result = target(*(args or []), **(kwargs or {})) if callable(target) else target
    return {'mod': mod, 'fn': fn, 'result': result}


def op_ps(**_):
    for cmd in (['pm2', 'jlist'], ):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if r.returncode == 0 and r.stdout.strip().startswith('['):
                procs = json.loads(r.stdout)
                return {'pm2': [{'name': p.get('name'),
                                 'status': (p.get('pm2_env') or {}).get('status'),
                                 'cpu': (p.get('monit') or {}).get('cpu'),
                                 'mem_mb': round((p.get('monit') or {}).get('memory', 0) / 1e6, 1),
                                 'restarts': (p.get('pm2_env') or {}).get('restart_time')}
                                for p in procs]}
        except Exception:
            pass
    try:
        r = subprocess.run(['ps', '-eo', 'pid,pcpu,pmem,comm', '--sort=-pcpu'],
                           capture_output=True, text=True, timeout=30)
        return {'ps': r.stdout.splitlines()[:25]}
    except FileNotFoundError:
        pass
    # A slim image has neither pm2 nor procps. /proc is always there.
    rows = []
    for pid in sorted((p for p in os.listdir('/proc') if p.isdigit()), key=int):
        try:
            with open(f'/proc/{pid}/cmdline') as f:
                cmd = f.read().replace('\0', ' ').strip()
            rows.append(f'{pid:>7}  {cmd[:110]}')
        except Exception:
            continue
    return {'ps': rows[:25], 'note': 'from /proc — no ps on this box'}


def op_ports(**_):
    for cmd in (['ss', '-ltnp'], ['netstat', '-ltnp']):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
            if r.returncode == 0:
                return {'listening': r.stdout.splitlines()[:40]}
        except Exception:
            continue
    return {'listening': [], 'note': 'neither ss nor netstat on this box'}


OPS = {'info': op_info, 'mods': op_mods, 'fns': op_fns, 'call': op_call,
       'ps': op_ps, 'ports': op_ports}


# ── entry ────────────────────────────────────────────────────────────────

def main(argv):
    raw = argv[1] if len(argv) > 1 else ''
    if raw in ('', '-'):
        raw = sys.stdin.read().strip()
    try:
        payload = json.loads(base64.b64decode(raw).decode())
    except Exception:
        payload = json.loads(raw) if raw.startswith('{') else {'op': raw or 'info'}
    op = payload.pop('op', 'info')
    t0 = time.time()
    try:
        if op not in OPS:
            raise KeyError(f'unknown op "{op}" — have {", ".join(OPS)}')
        answer = {'ok': True, 'op': op, **OPS[op](**payload)}
    except Exception as e:
        answer = {'ok': False, 'op': op, 'error': f'{type(e).__name__}: {e}'}
    answer['took_ms'] = round((time.time() - t0) * 1000)
    print(BEGIN + json.dumps(answer, default=str) + END)


if __name__ == '__main__':
    main(sys.argv)
