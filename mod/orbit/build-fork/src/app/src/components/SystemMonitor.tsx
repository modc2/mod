"use client";

import { useCallback, useEffect, useRef, useState } from "react";

// Admin-only htop panel — polls the owner-gated /system/htop endpoint and
// renders per-core CPU meters, memory/swap/disk gauges and a live process
// table. Meter colors are STATUS semantics (ok / hot / critical), and the
// numeric value is always printed beside the bar so state is never
// color-alone.

type Proc = {
  pid: number;
  user: string;
  state: string;
  cpu_pct: number;
  mem_mb: number;
  command: string;
};

type Htop = {
  cpu: { cores: number; pct: number; per_core: number[]; load1: number; load5: number; load15: number };
  mem: { total_mb: number; used_mb: number; available_mb: number; cached_mb: number; swap_total_mb: number; swap_used_mb: number };
  disk: { total_mb: number; available_mb: number };
  uptime_secs: number;
  tasks: { total: number; running: number };
  procs_total: number;
  procs: Proc[];
};

type Props = {
  apiBase: string;
  authHeader?: Record<string, string>;
};

const POLL_MS = 4000; // endpoint itself samples /proc for ~500ms

function utilColor(pct: number): string {
  if (pct >= 85) return "var(--crt-red)";
  if (pct >= 60) return "var(--crt-amber)";
  return "var(--crt-green)";
}

function fmtMB(mb: number): string {
  return mb >= 1024 ? `${(mb / 1024).toFixed(1)}G` : `${mb}M`;
}

function fmtUptime(secs: number): string {
  const d = Math.floor(secs / 86400);
  const h = Math.floor((secs % 86400) / 3600);
  const m = Math.floor((secs % 3600) / 60);
  return d > 0 ? `${d}d ${h}h` : h > 0 ? `${h}h ${m}m` : `${m}m`;
}

/** One horizontal gauge: label · bar · value, htop-style. */
function Meter({ label, pct, value }: { label: string; pct: number; value: string }) {
  const clamped = Math.max(0, Math.min(100, pct));
  const color = utilColor(clamped);
  return (
    <div className="flex items-center gap-1.5 min-w-0">
      <span
        className="text-[9px] font-mono shrink-0 text-right"
        style={{ color: "var(--text-tertiary)", width: 26 }}
      >
        {label}
      </span>
      <div
        className="flex-1 h-[8px] rounded-[2px] overflow-hidden"
        style={{ background: "var(--bg-secondary)", border: "1px solid var(--border-color)" }}
        role="meter"
        aria-valuenow={Math.round(clamped)}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={label}
      >
        <div
          className="h-full rounded-[1px]"
          style={{ width: `${clamped}%`, background: color, transition: "width 0.5s ease" }}
        />
      </div>
      <span
        className="text-[9px] font-mono shrink-0 text-right tabular-nums"
        style={{ color: "var(--text-secondary)", width: 58 }}
      >
        {value}
      </span>
    </div>
  );
}

export default function SystemMonitor({ apiBase, authHeader }: Props) {
  const [data, setData] = useState<Htop | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sortBy, setSortBy] = useState<"cpu" | "mem">("cpu");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const poll = useCallback(async () => {
    try {
      const r = await fetch(`${apiBase}/system/htop`, { headers: authHeader });
      if (r.status === 401 || r.status === 403) {
        setError("Owner-only — sign in with the owner wallet to view hardware stats.");
        return; // no reschedule: the session won't change under us
      }
      if (r.status === 404) {
        setError("API is running an older build — restart build-fork-api to enable /system/htop.");
        return;
      }
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setData(await r.json());
      setError(null);
    } catch {
      setError("Could not reach the API.");
    } finally {
      // setTimeout chain (not setInterval): the endpoint takes ~500ms itself,
      // so a fixed interval could stack requests on a slow host.
      timer.current = setTimeout(poll, POLL_MS);
    }
  }, [apiBase, authHeader]);

  useEffect(() => {
    poll();
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiBase]);

  if (error && !data) {
    return (
      <span className="text-[11.5px] py-1" style={{ color: "var(--text-tertiary)" }}>
        {error}
      </span>
    );
  }
  if (!data) {
    return (
      <span className="text-[11.5px] py-1" style={{ color: "var(--text-tertiary)" }}>
        Sampling /proc…
      </span>
    );
  }

  const { cpu, mem, disk, tasks } = data;
  const memPct = mem.total_mb > 0 ? (mem.used_mb / mem.total_mb) * 100 : 0;
  const swapPct = mem.swap_total_mb > 0 ? (mem.swap_used_mb / mem.swap_total_mb) * 100 : 0;
  const diskUsed = disk.total_mb - disk.available_mb;
  const diskPct = disk.total_mb > 0 ? (diskUsed / disk.total_mb) * 100 : 0;
  const procs = [...data.procs].sort((a, b) =>
    sortBy === "cpu" ? b.cpu_pct - a.cpu_pct || b.mem_mb - a.mem_mb : b.mem_mb - a.mem_mb || b.cpu_pct - a.cpu_pct
  );

  const headBtn = (id: "cpu" | "mem", label: string, width: number) => (
    <button
      onClick={() => setSortBy(id)}
      className="font-mono text-right shrink-0 cursor-pointer focus-ring"
      style={{
        width,
        color: sortBy === id ? "var(--crt-green)" : "var(--text-tertiary)",
        fontSize: 9,
      }}
      title={`Sort by ${label}`}
    >
      {label}
      {sortBy === id ? "▾" : ""}
    </button>
  );

  return (
    <div className="flex flex-col gap-3">
      {/* Headline: load / uptime / tasks — the htop top-right block. */}
      <div className="flex items-center justify-between gap-2 flex-wrap text-[9.5px] font-mono" style={{ color: "var(--text-secondary)" }}>
        <span title="Load average 1 / 5 / 15 min">
          <span style={{ color: "var(--text-tertiary)" }}>load </span>
          {cpu.load1.toFixed(2)} {cpu.load5.toFixed(2)} {cpu.load15.toFixed(2)}
        </span>
        <span>
          <span style={{ color: "var(--text-tertiary)" }}>up </span>
          {fmtUptime(data.uptime_secs)}
        </span>
        <span>
          <span style={{ color: "var(--text-tertiary)" }}>tasks </span>
          {tasks.total}
          <span style={{ color: "var(--crt-green)" }}> ({tasks.running} run)</span>
        </span>
      </div>

      {/* Per-core meters, two columns like htop's CPU banks. */}
      <div className="grid grid-cols-2 gap-x-3 gap-y-1">
        {cpu.per_core.map((pct, i) => (
          <Meter key={i} label={`${i}`} pct={pct} value={`${pct.toFixed(1)}%`} />
        ))}
      </div>

      <div className="flex flex-col gap-1">
        <Meter label="MEM" pct={memPct} value={`${fmtMB(mem.used_mb)}/${fmtMB(mem.total_mb)}`} />
        {mem.swap_total_mb > 0 && (
          <Meter label="SWP" pct={swapPct} value={`${fmtMB(mem.swap_used_mb)}/${fmtMB(mem.swap_total_mb)}`} />
        )}
        <Meter label="DSK" pct={diskPct} value={`${fmtMB(diskUsed)}/${fmtMB(disk.total_mb)}`} />
        <span className="text-[9px] font-mono self-end" style={{ color: "var(--text-tertiary)" }}>
          {fmtMB(mem.cached_mb)} cached · {cpu.cores} cores · cpu {cpu.pct.toFixed(1)}%
        </span>
      </div>

      {/* Process table — top consumers of the sort key. */}
      <div className="flex flex-col min-w-0">
        <div
          className="flex items-center gap-1.5 px-1 pb-1"
          style={{ borderBottom: "1px solid var(--border-color)" }}
        >
          <span className="font-mono text-right shrink-0" style={{ width: 40, color: "var(--text-tertiary)", fontSize: 9 }}>PID</span>
          <span className="font-mono shrink-0" style={{ width: 44, color: "var(--text-tertiary)", fontSize: 9 }}>USER</span>
          <span className="font-mono shrink-0" style={{ width: 10, color: "var(--text-tertiary)", fontSize: 9 }}>S</span>
          {headBtn("cpu", "CPU%", 34)}
          {headBtn("mem", "MEM", 38)}
          <span className="font-mono flex-1 min-w-0" style={{ color: "var(--text-tertiary)", fontSize: 9 }}>COMMAND</span>
        </div>
        <div className="flex flex-col overflow-y-auto" style={{ maxHeight: 320 }}>
          {procs.map((p) => (
            <div
              key={p.pid}
              className="flex items-center gap-1.5 px-1 py-[2px] rounded-[2px] transition-colors"
              title={p.command}
              onMouseEnter={(e) => (e.currentTarget.style.background = "color-mix(in srgb, var(--crt-green) 7%, transparent)")}
              onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
            >
              <span className="font-mono text-right shrink-0 tabular-nums" style={{ width: 40, color: "var(--text-tertiary)", fontSize: 10 }}>{p.pid}</span>
              <span className="font-mono shrink-0 truncate" style={{ width: 44, color: "var(--text-secondary)", fontSize: 10 }}>{p.user}</span>
              <span
                className="font-mono shrink-0"
                style={{ width: 10, fontSize: 10, color: p.state === "R" ? "var(--crt-green)" : "var(--text-tertiary)" }}
                title={p.state === "R" ? "running" : p.state === "D" ? "disk sleep" : p.state === "Z" ? "zombie" : "sleeping"}
              >
                {p.state}
              </span>
              <span
                className="font-mono text-right shrink-0 tabular-nums"
                style={{ width: 34, fontSize: 10, color: p.cpu_pct >= 50 ? utilColor(p.cpu_pct) : "var(--text-primary)" }}
              >
                {p.cpu_pct.toFixed(1)}
              </span>
              <span className="font-mono text-right shrink-0 tabular-nums" style={{ width: 38, color: "var(--text-primary)", fontSize: 10 }}>
                {fmtMB(p.mem_mb)}
              </span>
              <span className="font-mono flex-1 min-w-0 truncate" style={{ color: "var(--text-secondary)", fontSize: 10 }}>
                {p.command}
              </span>
            </div>
          ))}
        </div>
        <span className="text-[9px] font-mono px-1 pt-1" style={{ color: "var(--text-tertiary)" }}>
          top {procs.length} of {data.procs_total} userland processes · refreshes every {POLL_MS / 1000}s
        </span>
      </div>

      {error && (
        <span className="text-[10px]" style={{ color: "var(--crt-amber)" }}>
          {error} Retrying…
        </span>
      )}
    </div>
  );
}
