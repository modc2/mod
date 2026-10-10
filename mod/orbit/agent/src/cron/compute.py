"""
compute — what machine an agent is running on, read off the box itself

No network, no SDK, no cloud metadata endpoint: /proc, os, shutil and (when it
exists) nvidia-smi. Everything is best-effort — a field that can't be read is
left out rather than guessed — and the slow part (GPUs) is cached for a minute.

Two questions, kept apart because they have different answers:

    host()          where the agent LOOP and its TOOLS run: this process's box
    inference(...)  where the MODEL runs: this box (local weights), the
                    visitor's tab, a fleet module, an agent CLI, or a hosted
                    API somewhere else

`where(provider, model, harness)` is both, as one dict a UI can draw.
"""
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional

_BOOT = time.time()
_GPU_TTL = 60.0
_gpu_cache: Dict[str, Any] = {"t": 0.0, "gpus": []}

# provider short name → where its weights are (see Mod.PROVIDERS)
LOCAL = {"liquidai": "local weights on this host (CPU/GPU)",
         "hermes": "Hermes weights on this host"}
TAB = {"browser": "the visitor's browser tab (WebGPU/WASM)"}
RELAY = {"liquidai-cloud": "Liquid's cloud", "openrouter": "OpenRouter (hosted API)",
         "model.openrouter": "OpenRouter (hosted API)", "venice": "Venice (hosted API)"}


def _read(path: str) -> str:
    try:
        with open(path) as f:
            return f.read()
    except Exception:
        return ""


def _proc_uptime() -> Optional[int]:
    """Seconds this process has been up (from /proc), else since import."""
    try:
        start = int(_read("/proc/self/stat").rsplit(")", 1)[1].split()[19])
        boot_up = float(_read("/proc/uptime").split()[0])
        return int(boot_up - start / os.sysconf("SC_CLK_TCK"))
    except Exception:
        return int(time.time() - _BOOT)


def _cpu_model() -> Optional[str]:
    for line in _read("/proc/cpuinfo").splitlines():
        if line.lower().startswith(("model name", "hardware", "cpu model")):
            return line.split(":", 1)[1].strip()
    return platform.processor() or None


def _mem() -> Dict[str, float]:
    out = {}
    for line in _read("/proc/meminfo").splitlines():
        k, _, v = line.partition(":")
        if k in ("MemTotal", "MemAvailable"):
            try:
                out[k] = round(int(v.split()[0]) / 1048576, 1)   # kB → GiB
            except Exception:
                pass
    return {"total_gb": out.get("MemTotal"), "free_gb": out.get("MemAvailable")}


def _gpus() -> List[Dict[str, Any]]:
    now = time.time()
    if now - _gpu_cache["t"] < _GPU_TTL:
        return _gpu_cache["gpus"]
    gpus: List[Dict[str, Any]] = []
    if shutil.which("nvidia-smi"):
        try:
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,memory.total,memory.used,utilization.gpu",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=3, stdin=subprocess.DEVNULL).stdout
            for row in out.strip().splitlines():
                p = [x.strip() for x in row.split(",")]
                if len(p) >= 4:
                    gpus.append({"name": p[0], "mem_gb": round(float(p[1]) / 1024, 1),
                                 "used_gb": round(float(p[2]) / 1024, 1), "util": int(float(p[3]))})
        except Exception:
            pass
    _gpu_cache.update(t=now, gpus=gpus)
    return gpus


def host() -> Dict[str, Any]:
    """This box, now. Cheap enough to call on every status poll."""
    try:
        load = [round(x, 2) for x in os.getloadavg()]
    except Exception:
        load = None
    try:
        du = shutil.disk_usage(os.path.expanduser("~"))
        disk = {"total_gb": round(du.total / 2**30, 1), "free_gb": round(du.free / 2**30, 1)}
    except Exception:
        disk = {}
    cores = os.cpu_count() or 0
    out = {
        "hostname": socket.gethostname(),
        "os": f"{platform.system()} {platform.release()}",
        "arch": platform.machine(),
        "cpu": _cpu_model(),
        "cores": cores,
        "load": load,
        # 1-minute load over cores: >1 means work is queueing for a CPU
        "busy": round(load[0] / cores, 2) if load and cores else None,
        "mem": _mem(),
        "disk": disk,
        "gpus": _gpus(),
        "python": sys.version.split()[0],
        "pid": os.getpid(),
        "uptime_s": _proc_uptime(),
        "container": os.path.exists("/.dockerenv") or "docker" in _read("/proc/1/cgroup"),
    }
    return {k: v for k, v in out.items() if v not in (None, "", {})}


def inference(provider: str = None, model: str = None, harness: str = None) -> Dict[str, Any]:
    """Where the model half of a run executes."""
    p = str(provider or "")
    if harness:
        return {"kind": "cli", "where": f"`{harness}` agent CLI on this host",
                "provider": harness, "model": model}
    if p in LOCAL:
        return {"kind": "local", "where": LOCAL[p], "provider": p, "model": model}
    if p in TAB:
        return {"kind": "tab", "where": TAB[p], "provider": p, "model": model}
    if p.startswith("mod:"):
        return {"kind": "fleet", "where": f"the {p[4:]} module (worker process on this host, "
                                          f"its own upstream)", "provider": p, "model": model}
    return {"kind": "remote", "where": RELAY.get(p, f"{p or 'default provider'} (hosted API)"),
            "provider": p or None, "model": model}


def where(provider: str = None, model: str = None, harness: str = None) -> Dict[str, Any]:
    return {"host": host(), "inference": inference(provider, model, harness)}
