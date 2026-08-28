#!/usr/bin/env python3
"""
Circuit runner — the *only* place user-supplied code executes.

Runs as a short-lived child process, ideally under `unshare -n` (no network) and
dropped to an unprivileged user. It reads one job from stdin and writes one JSON
line to stdout:

    in   {"source": "...", "op": "encrypt", "data_b64": "...",
          "key_b64": "...", "params": {}, "limits": {...}}
    out  {"ok": true, "data_b64": "..."}  |  {"ok": false, "error": "..."}

The circuit source arrives on stdin rather than by path so the child never needs
to read the filesystem — it can drop privileges before touching anything.

Deliberately dependency-free and importable-by-path-free: it is started with
`python3 -I -B`, so the mod repo is not on its sys.path. Circuits get the plain
standard library plus whatever is installed system-wide (e.g. `cryptography`).
"""
import base64
import json
import os
import resource
import sys

SELFTEST = b'mod encrypt circuit selftest \x00\xff'


def _apply_limits(limits: dict) -> None:
    cpu = int(limits.get('cpu_seconds', 10))
    mem = int(limits.get('memory_mb', 512)) * 1024 * 1024
    for res, soft_hard in (
        (resource.RLIMIT_CPU, (cpu, cpu + 1)),
        (resource.RLIMIT_AS, (mem, mem)),
        (resource.RLIMIT_FSIZE, (0, 0)),   # circuits may not write files
        (resource.RLIMIT_CORE, (0, 0)),
    ):
        try:
            resource.setrlimit(res, soft_hard)
        except (ValueError, OSError):
            pass


def _drop_privileges(user: str) -> None:
    """Become `user` when we start as root. A no-op otherwise (the caller
    reports the resulting isolation level in /status, so nothing is silent)."""
    if os.geteuid() != 0 or not user:
        return
    import pwd
    try:
        pw = pwd.getpwnam(user)
    except KeyError:
        return
    os.setgroups([])
    os.setgid(pw.pw_gid)
    os.setuid(pw.pw_uid)


def _load(source: str):
    """Execute the circuit source in a fresh namespace and return it."""
    ns: dict = {'__name__': 'circuit', '__file__': '<circuit>', '__builtins__': __builtins__}
    exec(compile(source, '<circuit>', 'exec'), ns)
    for fn in ('encrypt', 'decrypt'):
        if not callable(ns.get(fn)):
            raise ValueError(f'circuit does not define a callable {fn}(data, key, params)')
    return ns


def _call(fn, data: bytes, key: bytes, params: dict) -> bytes:
    out = fn(data, key, params)
    if isinstance(out, str):
        out = out.encode()
    if not isinstance(out, (bytes, bytearray)):
        raise TypeError(f'circuit returned {type(out).__name__}, expected bytes')
    return bytes(out)


def main() -> int:
    try:
        job = json.loads(sys.stdin.read())
    except Exception as e:
        print(json.dumps({'ok': False, 'error': f'bad job: {e}'}))
        return 0

    os.chdir('/tmp')
    _apply_limits(job.get('limits') or {})
    _drop_privileges(job.get('user') or '')

    try:
        ns = _load(job['source'])
        op = job.get('op', 'encrypt')
        key = base64.b64decode(job.get('key_b64') or '')
        params = job.get('params') or {}

        if op == 'selftest':
            # Prove the circuit is a real cipher before we let anyone store
            # bytes with it: a roundtrip must return exactly what went in, and
            # the ciphertext must not simply *be* the plaintext.
            ct = _call(ns['encrypt'], SELFTEST, key, params)
            pt = _call(ns['decrypt'], ct, key, params)
            if pt != SELFTEST:
                raise ValueError('roundtrip failed: decrypt(encrypt(x)) != x')
            if ct == SELFTEST:
                raise ValueError('circuit is a no-op: ciphertext == plaintext')
            return _emit({'ok': True, 'roundtrip': True, 'ciphertext_bytes': len(ct)})

        if op not in ('encrypt', 'decrypt'):
            raise ValueError(f'unknown op {op}')
        data = base64.b64decode(job.get('data_b64') or '')
        out = _call(ns[op], data, key, params)
        return _emit({'ok': True, 'data_b64': base64.b64encode(out).decode()})
    except MemoryError:
        return _emit({'ok': False, 'error': 'circuit exceeded its memory limit'})
    except Exception as e:
        return _emit({'ok': False, 'error': f'{type(e).__name__}: {e}'})


def _emit(payload: dict) -> int:
    sys.stdout.write(json.dumps(payload))
    sys.stdout.flush()
    return 0


if __name__ == '__main__':
    sys.exit(main())
