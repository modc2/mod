"""Running `ft serve` on this machine, when this machine is the one with the GPU.

Upstream ships a control daemon (`ft daemon`, :1900) that does start/stop/switch
over HTTP, and when a box has one this module uses it — see `src.client`. This
file is the other case: no daemon, just a process to supervise, a pidfile and a
log, so `m freetoken/serve` and `m freetoken/logs` work on a bare install.

`--model` is the only flag `ft serve` requires; everything below it is optional
and, left alone, resolves from the checkpoint and the GPU. The table here exists
so those flags can be passed through a JSON API without shell quoting, and so a
typo is rejected here instead of thirty seconds into a model load.
"""
from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from src import boxes, client, install, preflight, state

LOG = 'serve.log'
PID = 'serve.pid'
META = 'serve.json'

# python name → `ft serve` flag. docs/cli.md.
FLAGS: Dict[str, str] = {
    'served_model_name': '--served-model-name',
    'host': '--host', 'port': '--port',
    'max_running_requests': '--max-running-requests',
    'max_output_tokens': '--max-output-tokens',
    'max_seq_len_override': '--max-seq-len-override',
    'max_prefill_length': '--max-prefill-length',
    'cuda_graph_max_bs': '--cuda-graph-max-bs', 'graph': '--cuda-graph-max-bs',
    'decode_log_interval': '--decode-log-interval',
    'memory_ratio': '--memory-ratio',
    'num_pages': '--num-pages', 'num_tokens': '--num-tokens',
    'page_size': '--page-size', 'cache_type': '--cache-type',
    'attention_backend': '--attention-backend', 'attn': '--attention-backend',
    'moe_backend': '--moe-backend',
    'moe_cache_size': '--moe-cache-size', 'moe_cache_rate': '--moe-cache-rate',
    'kv_reserve_tokens': '--kv-reserve-tokens',
    'moe_cpu_threads': '--moe-cpu-threads', 'moe_cpu_layers': '--moe-cpu-layers',
    'moe_hybrid_max_fetch': '--moe-hybrid-max-fetch',
    'sampling_defaults': '--sampling-defaults',
    'tool_call_parser': '--tool-call-parser',
    'reasoning_parser': '--reasoning-parser',
}

# Flags that take no value.
SWITCHES: Dict[str, str] = {
    'moe_cache_auto': '--moe-cache-auto',
    'moe_prefill_hit_d2d': '--moe-prefill-hit-d2d',
    'disable_moe_prefill_overlap': '--disable-moe-prefill-overlap',
    'enable_cache_report': '--enable-cache-report',
}


def truthy(value: Any) -> bool:
    """CLI arguments arrive as strings; `force=false` has to mean false."""
    if isinstance(value, str):
        return value.strip().lower() not in ('', '0', 'false', 'no', 'off')
    return bool(value)


def serve_argv(model: str, **flags: Any) -> List[str]:
    """The argv for `ft serve`, with unknown flags rejected by name."""
    if not model:
        raise ValueError('a model is required: a local dir, an HF repo id, or an FTW dir')
    argv = ['serve', '--model', str(model)]
    unknown = [k for k in flags if k not in FLAGS and k not in SWITCHES]
    if unknown:
        raise ValueError(f'not a `ft serve` flag: {", ".join(sorted(unknown))} — '
                         f'known: {", ".join(sorted(set(FLAGS) | set(SWITCHES)))}')
    for name, flag in SWITCHES.items():
        if name in flags and truthy(flags[name]):
            argv.append(flag)
    for name, value in flags.items():
        if name in FLAGS and value not in (None, ''):
            argv += [FLAGS[name], str(value)]
    return argv


def _pid() -> Optional[int]:
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


def start(model: str, force: bool = False, **flags: Any) -> Dict[str, Any]:
    """Spawn `ft serve` here, detached, logging to ~/.mod/freetoken/logs/serve.log."""
    running = _pid()
    if running:
        return {'ok': False, 'why': 'a serve is already running here', 'pid': running,
                'stop_it': 'm freetoken/stop'}
    gate = preflight.report()
    if not gate['can_serve_here'] and not truthy(force):
        return {'ok': False, 'why': gate['verdict'], 'blocking': gate['blocking'],
                'checks': [c for c in gate['checks'] if c['blocking'] and not c['ok']],
                'override': 'pass force=1 to try anyway'}
    argv = serve_argv(model, **flags)
    binary = install.ft_bin()
    if not binary:
        return {'ok': False, 'why': 'ft is not installed here — m freetoken/install'}
    log = state.logs() / LOG
    handle = log.open('ab')
    handle.write(f'\n=== {binary} {" ".join(argv)}\n'.encode())
    handle.flush()
    process = subprocess.Popen([binary] + argv, stdout=handle,
                               stderr=subprocess.STDOUT, start_new_session=True)
    (state.logs() / PID).write_text(str(process.pid))
    port = int(flags.get('port') or boxes.SERVE_PORT)
    meta = {'model': model, 'argv': argv, 'pid': process.pid, 'port': port,
            'host': flags.get('host', '127.0.0.1'), 'started': int(time.time())}
    state.write(META, meta)
    return {'ok': True, **meta, 'log': str(log),
            'url': f'http://127.0.0.1:{port}',
            'ready_when': f'the log reaches "API server is ready to serve on '
                          f'{meta["host"]}:{port}"',
            'watch': 'm freetoken/logs'}


def status() -> Dict[str, Any]:
    """The local process, and whether the port it claims actually answers."""
    pid, meta = _pid(), state.read(META, {}) or {}
    card: Dict[str, Any] = {'running': pid is not None, 'pid': pid,
                            'model': meta.get('model'), 'port': meta.get('port'),
                            'argv': meta.get('argv'),
                            'uptime_s': int(time.time() - meta['started'])
                            if pid and meta.get('started') else None,
                            'log': str(state.logs() / LOG)}
    if pid and meta.get('port'):
        box = {'name': 'local', 'url': f'http://127.0.0.1:{meta["port"]}'}
        try:
            card['health'] = client.health(box, timeout=3.0)
            card['serving'] = True
        except (client.Unreachable, client.Refused) as exc:
            card['serving'] = False
            card['not_yet'] = str(exc)
    return card


def stop(force: bool = False) -> Dict[str, Any]:
    pid = _pid()
    if not pid:
        return {'ok': False, 'why': 'no serve started by this module is running here'}
    sig = signal.SIGKILL if truthy(force) else signal.SIGTERM
    try:
        os.killpg(os.getpgid(pid), sig)
    except OSError as exc:
        return {'ok': False, 'why': str(exc), 'pid': pid}
    (state.logs() / PID).unlink(missing_ok=True)
    return {'ok': True, 'signalled': pid, 'signal': sig.name}


def logs(lines: int = 60) -> Dict[str, Any]:
    return {'log': str(state.logs() / LOG), 'running': _pid() is not None,
            'tail': state.tail(state.logs() / LOG, int(lines))}
