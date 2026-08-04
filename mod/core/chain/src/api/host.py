"""Host readout — CPU, memory, disk, processes and network traffic.

Everything here is a parse of /proc plus os.statvfs, so the API gains no new
dependency and the numbers match what htop/ifstat would print on the box.

CPU percentages and network rates are only meaningful as a *rate*, so collect()
samples twice around a short sleep and reports the delta over that window
rather than the counters' lifetime averages.
"""

import asyncio
import os
import platform
import time

CLK_TCK = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100


def _read(path: str) -> str:
    try:
        with open(path) as f:
            return f.read()
    except Exception:
        return ""


# ── CPU ──────────────────────────────────────────────────────────────────────

def _cpu_jiffies():
    """(total, idle) jiffies per cpuN line — index 0 is core 0."""
    out = []
    for line in _read("/proc/stat").splitlines():
        if not line.startswith("cpu") or not line[3:4].isdigit():
            continue
        nums = [int(v) for v in line.split()[1:] if v.isdigit()]
        if len(nums) < 5:
            continue
        out.append((sum(nums), nums[3] + nums[4]))  # idle + iowait
    return out


def _busy_pct(before, after) -> float:
    total = after[0] - before[0]
    idle = after[1] - before[1]
    if total <= 0:
        return 0.0
    return max(0.0, min(100.0, (total - idle) * 100.0 / total))


# ── Memory / disk ────────────────────────────────────────────────────────────

def _meminfo() -> dict:
    """/proc/meminfo as {key: kB}."""
    mem = {}
    for line in _read("/proc/meminfo").splitlines():
        key, _, rest = line.partition(":")
        parts = rest.split()
        if parts and parts[0].isdigit():
            mem[key] = int(parts[0])
    return mem


def _disk(path: str = "/") -> dict:
    try:
        st = os.statvfs(path)
    except Exception:
        return {"total_mb": 0, "available_mb": 0}
    mb = 1024 * 1024
    return {
        "total_mb": st.f_blocks * st.f_frsize // mb,
        "available_mb": st.f_bavail * st.f_frsize // mb,
    }


# ── Network ──────────────────────────────────────────────────────────────────

def _net_dev() -> dict:
    """Per-interface byte/packet/error counters from /proc/net/dev."""
    ifaces = {}
    for line in _read("/proc/net/dev").splitlines()[2:]:
        name, sep, rest = line.partition(":")
        if not sep:
            continue
        cols = rest.split()
        if len(cols) < 16:
            continue
        ifaces[name.strip()] = {
            "rx_bytes": int(cols[0]), "rx_packets": int(cols[1]),
            "rx_errs": int(cols[2]), "rx_drop": int(cols[3]),
            "tx_bytes": int(cols[8]), "tx_packets": int(cols[9]),
            "tx_errs": int(cols[10]), "tx_drop": int(cols[11]),
        }
    return ifaces


def _connections() -> dict:
    """Socket census: TCP by state (hex codes from tcp_states.h) plus UDP."""
    tcp = {"established": 0, "listen": 0, "other": 0}
    for src in ("/proc/net/tcp", "/proc/net/tcp6"):
        for line in _read(src).splitlines()[1:]:
            cols = line.split()
            if len(cols) < 4:
                continue
            if cols[3] == "01":
                tcp["established"] += 1
            elif cols[3] == "0A":
                tcp["listen"] += 1
            else:
                tcp["other"] += 1
    udp = 0
    for src in ("/proc/net/udp", "/proc/net/udp6"):
        udp += max(0, len(_read(src).splitlines()) - 1)
    return {"tcp": tcp, "udp": udp}


# ── Processes ────────────────────────────────────────────────────────────────

def _usernames() -> dict:
    """uid → login name; unknown uids fall back to the bare number later."""
    names = {}
    for line in _read("/etc/passwd").splitlines():
        cols = line.split(":")
        if len(cols) > 2 and cols[2].isdigit():
            names[int(cols[2])] = cols[0]
    return names


def _pids():
    try:
        return [int(p) for p in os.listdir("/proc") if p.isdigit()]
    except Exception:
        return []


def _pid_stat(pid: int):
    """(state, cpu_jiffies, rss_pages) — None if the process is already gone."""
    raw = _read(f"/proc/{pid}/stat")
    head, sep, tail = raw.rpartition(")")
    if not sep:
        return None
    cols = tail.split()
    if len(cols) < 22:
        return None
    comm = head.partition("(")[2]
    try:
        return comm, cols[0], int(cols[11]) + int(cols[12]), int(cols[21])
    except ValueError:
        return None


def _pid_uid(pid: int):
    for line in _read(f"/proc/{pid}/status").splitlines():
        if line.startswith("Uid:"):
            parts = line.split()
            if len(parts) > 1 and parts[1].isdigit():
                return int(parts[1])
    return None


def _pid_command(pid: int, comm: str) -> str:
    """Full argv, falling back to htop's [comm] form. Capped: one giant argv
    should not bloat the payload."""
    raw = _read(f"/proc/{pid}/cmdline")
    joined = " ".join(t for t in raw.split("\0") if t)
    return (joined or f"[{comm}]")[:240]


# ── Collect ──────────────────────────────────────────────────────────────────

async def collect(interval: float = 0.5, top: int = 30) -> dict:
    """One host snapshot. Sleeps `interval` between counter samples, so a call
    costs roughly that much wall time — poll it, don't hammer it."""
    cpu_before = _cpu_jiffies()
    net_before = _net_dev()
    proc_before = {}
    for pid in _pids():
        st = _pid_stat(pid)
        if st:
            proc_before[pid] = st[2]
    t_before = time.monotonic()

    await asyncio.sleep(interval)

    cpu_after = _cpu_jiffies()
    net_after = _net_dev()
    dt = max(1e-3, time.monotonic() - t_before)

    per_core = [_busy_pct(b, a) for b, a in zip(cpu_before, cpu_after)]
    cores = len(per_core)

    # ── processes ──
    names = _usernames()
    rows, running, total_tasks = [], 0, 0
    for pid in _pids():
        st = _pid_stat(pid)
        if not st:
            continue
        comm, state, jiffies, rss_pages = st
        total_tasks += 1
        if state == "R":
            running += 1
        used = jiffies - proc_before.get(pid, jiffies)
        cpu_pct = max(0.0, used * 100.0 / (dt * CLK_TCK))
        uid = _pid_uid(pid)
        rows.append({
            "pid": pid,
            "user": names.get(uid, str(uid) if uid is not None else "?"),
            "state": state,
            "cpu_pct": round(cpu_pct, 1),
            "mem_mb": rss_pages * os.sysconf("SC_PAGE_SIZE") // (1024 * 1024),
            "command": _pid_command(pid, comm),
        })
    rows.sort(key=lambda r: (r["cpu_pct"], r["mem_mb"]), reverse=True)

    # ── network ──
    # A container host has dozens of idle veth/bridge stubs; only interfaces
    # that have ever carried a byte are worth the payload. Busiest first.
    interfaces = []
    tot = {"rx_bytes": 0, "tx_bytes": 0, "rx_rate": 0.0, "tx_rate": 0.0}
    for name, after in net_after.items():
        before = net_before.get(name, after)
        if after["rx_bytes"] + after["tx_bytes"] == 0:
            continue
        rx_rate = max(0.0, (after["rx_bytes"] - before["rx_bytes"]) / dt)
        tx_rate = max(0.0, (after["tx_bytes"] - before["tx_bytes"]) / dt)
        interfaces.append({
            "name": name,
            "rx_bytes": after["rx_bytes"], "tx_bytes": after["tx_bytes"],
            "rx_rate": round(rx_rate, 1), "tx_rate": round(tx_rate, 1),
            "rx_pps": round(max(0.0, (after["rx_packets"] - before["rx_packets"]) / dt), 1),
            "tx_pps": round(max(0.0, (after["tx_packets"] - before["tx_packets"]) / dt), 1),
            "errs": after["rx_errs"] + after["tx_errs"],
            "drops": after["rx_drop"] + after["tx_drop"],
            "loopback": name == "lo",
        })
        if name != "lo":  # totals are about traffic that left the box
            tot["rx_bytes"] += after["rx_bytes"]
            tot["tx_bytes"] += after["tx_bytes"]
            tot["rx_rate"] += rx_rate
            tot["tx_rate"] += tx_rate
    interfaces.sort(key=lambda i: (i["rx_rate"] + i["tx_rate"], i["rx_bytes"] + i["tx_bytes"]), reverse=True)
    tot["rx_rate"] = round(tot["rx_rate"], 1)
    tot["tx_rate"] = round(tot["tx_rate"], 1)

    mem = _meminfo()
    total_kb = mem.get("MemTotal", 0)
    avail_kb = mem.get("MemAvailable", mem.get("MemFree", 0))
    swap_total_kb = mem.get("SwapTotal", 0)
    load1, load5, load15 = os.getloadavg() if hasattr(os, "getloadavg") else (0, 0, 0)

    return {
        "host": {
            "name": platform.node(),
            "kernel": platform.release(),
            "arch": platform.machine(),
        },
        "cpu": {
            "cores": cores,
            "pct": round(sum(per_core) / cores, 1) if cores else 0.0,
            "per_core": [round(p, 1) for p in per_core],
            "load1": round(load1, 2), "load5": round(load5, 2), "load15": round(load15, 2),
        },
        "mem": {
            "total_mb": total_kb // 1024,
            "used_mb": max(0, total_kb - avail_kb) // 1024,
            "available_mb": avail_kb // 1024,
            "cached_mb": mem.get("Cached", 0) // 1024,
            "swap_total_mb": swap_total_kb // 1024,
            "swap_used_mb": max(0, swap_total_kb - mem.get("SwapFree", 0)) // 1024,
        },
        "disk": _disk(),
        "net": {"interfaces": interfaces, "total": tot, "connections": _connections()},
        "uptime_secs": int(float((_read("/proc/uptime").split() or ["0"])[0])),
        "tasks": {"total": total_tasks, "running": running},
        "procs_total": total_tasks,
        "procs": rows[:top],
        "sampled_secs": round(dt, 3),
    }
