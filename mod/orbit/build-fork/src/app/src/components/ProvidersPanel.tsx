"use client";

import { useCallback, useEffect, useRef, useState } from "react";

// PROCESSES panel — polls the public /providers endpoint and renders one row
// per app provider (module): live RSS/CPU from the /proc attribution pass,
// merged with the activator's scale-to-zero state (running / sleeping /
// pinned / idle). The header shows host memory headroom plus the OOM guard —
// the memory floor and running-app cap that stop LRU modules before the box
// can hit the kernel OOM killer. Owner sessions also get the knobs and
// per-module start/stop/sleep/pin controls (the backend gates them anyway).

type ActivatorInfo = {
  running: boolean;
  pinned: boolean;
  disabled: boolean;
  idleSeconds: number;
  apiPort?: number | null;
  appPort?: number | null;
};

type Provider = {
  module: string;
  category: string | null;
  mem_mb: number;
  cpu_pct: number;
  procs: { pid: number; label: string; mem_mb: number }[];
  activator: ActivatorInfo | null;
  sleeping: boolean;
  stopped?: boolean; // pm2-registered but killed — offer RUN
  installed?: boolean; // on disk with a launch recipe, never started — offer START
  cid?: string | null; // registry CID (~/.mod/api/registry.json) — null if never registered
  started_at?: number | null; // epoch secs, oldest live process — "up 3h"
  last_up?: number | null; // epoch secs, pm2's last start of a down app — "3h ago"
};

type Guard = {
  minFreeMb: number;
  maxRunning: number;
  availableMb: number | null;
  underPressure: boolean;
  pressureStops: number;
  capStops: number;
  lastStop: { module: string; reason: string; at: string } | null;
};

type Payload = {
  host: { mem: { total_mb: number; available_mb: number; used_mb: number } } | null;
  activator: boolean;
  guard: Guard | null;
  idle_seconds: number | null;
  providers: Provider[];
};

const POLL_MS = 5000; // the endpoint itself samples /proc for ~400ms

function utilColor(pct: number): string {
  if (pct >= 85) return "var(--crt-red)";
  if (pct >= 60) return "var(--crt-amber)";
  return "var(--crt-green)";
}

function fmtMB(mb: number): string {
  return mb >= 1024 ? `${(mb / 1024).toFixed(1)}G` : `${mb}M`;
}

function fmtIdle(secs: number): string {
  if (secs < 60) return `${secs}s`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h`;
  return `${Math.floor(secs / 86400)}d`;
}

/** Seconds since an epoch-second stamp, clamped at 0 (clock skew). */
function since(epochSecs: number): number {
  return Math.max(0, Math.floor(Date.now() / 1000) - epochSecs);
}

type Props = {
  apiBase: string;
  authHeader?: Record<string, string>;
  canControl?: boolean;
};

export default function ProvidersPanel({ apiBase, authHeader, canControl }: Props) {
  const [data, setData] = useState<Payload | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [actionErr, setActionErr] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null); // "module:action" in flight
  const [knobDraft, setKnobDraft] = useState<{ minFreeMb: string; maxRunning: string } | null>(null);
  const [selected, setSelected] = useState<string | null>(null); // module name of the selected row
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const copyCid = async (cid: string) => {
    try {
      await navigator.clipboard.writeText(cid);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard unavailable (http origin) — the CID is still selectable text */
    }
  };

  const poll = useCallback(async () => {
    try {
      const r = await fetch(`${apiBase}/providers`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setData(await r.json());
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "fetch failed");
    }
    timer.current = setTimeout(poll, POLL_MS);
  }, [apiBase]);

  useEffect(() => {
    poll();
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [poll]);

  // Two control planes. The activator (reaper/control) sleeps/wakes managed
  // modules; pm2 (modules/:name/process) stops/starts anything registered,
  // managed or not. Both surface their error body so a 401/403 reads as
  // "sign in as owner", not a silently dead button.
  const reaperControl = async (module: string, action: string) => {
    const r = await fetch(`${apiBase}/reaper/control`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...(authHeader || {}) },
      body: JSON.stringify({ module, action }),
    });
    if (!r.ok) {
      const d = await r.json().catch(() => null);
      throw new Error(d?.error || `HTTP ${r.status}`);
    }
  };

  const processControl = async (module: string, action: "stop" | "start" | "restart") => {
    const r = await fetch(`${apiBase}/modules/${encodeURIComponent(module)}/process`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...(authHeader || {}) },
      body: JSON.stringify({ action }),
    });
    if (!r.ok) {
      const d = await r.json().catch(() => null);
      throw new Error(d?.error || `HTTP ${r.status}`);
    }
  };

  const act = async (key: string, fn: () => Promise<void>) => {
    setBusy(key);
    try {
      await fn();
      setActionErr(null);
    } catch (e) {
      setActionErr(e instanceof Error ? e.message : "action failed");
    } finally {
      setBusy(null);
      if (timer.current) clearTimeout(timer.current);
      poll();
    }
  };

  const control = (module: string, action: string) =>
    act(`${module}:${action}`, () => reaperControl(module, action));

  // KILL: stop now, STAY stopped, and actually be dead. The activator's sleep
  // only pm2-stops what pm2 registered, so ALWAYS follow with the process
  // stop — its straggler sweep SIGTERM/SIGKILLs every remaining process
  // attributed to the module (bare procs, detached children, dev servers). A
  // managed module must also be disabled first, or the activator resurrects
  // it on the very next request.
  const killApp = (p: Provider) =>
    act(`${p.module}:kill`, async () => {
      if (p.activator) {
        await reaperControl(p.module, "disable");
        await reaperControl(p.module, "sleep");
      }
      await processControl(p.module, "stop");
    });

  // RUN: start now. Re-enables auto-wake on managed modules (no-op if enabled).
  const runApp = (p: Provider) =>
    act(`${p.module}:run`, async () => {
      if (p.activator) {
        await reaperControl(p.module, "enable");
        await reaperControl(p.module, "wake");
      } else {
        await processControl(p.module, "start");
      }
    });

  const saveKnobs = async () => {
    if (!knobDraft) return;
    setBusy("knobs");
    try {
      const body: Record<string, number | null> = {};
      const free = parseInt(knobDraft.minFreeMb, 10);
      const cap = parseInt(knobDraft.maxRunning, 10);
      if (Number.isFinite(free)) body.min_free_mb = free;
      if (Number.isFinite(cap)) body.max_running = cap;
      await fetch(`${apiBase}/reaper/config`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...(authHeader || {}) },
        body: JSON.stringify(body),
      });
      setKnobDraft(null);
      if (timer.current) clearTimeout(timer.current);
      await poll();
    } finally {
      setBusy(null);
    }
  };

  if (err && !data) {
    return (
      <div className="text-[11px] font-mono py-3" style={{ color: "var(--crt-red)" }}>
        processes unavailable: {err}
      </div>
    );
  }
  if (!data) {
    return (
      <div className="text-[11px] font-mono py-3" style={{ color: "var(--text-tertiary)" }}>
        sampling /proc…
      </div>
    );
  }

  const mem = data.host?.mem;
  const memPct = mem && mem.total_mb > 0 ? (mem.used_mb / mem.total_mb) * 100 : 0;
  const guard = data.guard;
  const running = data.providers.filter((p) => !p.sleeping && !p.stopped);
  const sleeping = data.providers.filter((p) => p.sleeping);
  const stopped = data.providers.filter((p) => p.stopped && !p.installed);
  const installed = data.providers.filter((p) => p.stopped && p.installed);

  return (
    <div className="flex flex-col gap-3 font-mono text-[11px]">
      {/* Host memory + OOM guard header */}
      {mem && (
        <div className="flex flex-col gap-1.5">
          <div className="flex items-center gap-2">
            <span className="uppercase tracking-wider text-[9px] w-10 shrink-0" style={{ color: "var(--text-tertiary)" }}>
              mem
            </span>
            <div className="flex-1 h-2 rounded-sm overflow-hidden" style={{ background: "var(--bg-secondary)" }}>
              <div
                className="h-full transition-all"
                style={{ width: `${Math.min(100, memPct)}%`, background: utilColor(memPct) }}
              />
            </div>
            <span style={{ color: "var(--text-secondary)" }}>
              {fmtMB(mem.used_mb)}/{fmtMB(mem.total_mb)} · {fmtMB(mem.available_mb)} free
            </span>
          </div>
          {guard && (
            <div className="flex items-center gap-2 flex-wrap">
              <span
                className="px-1.5 py-0.5 rounded text-[9.5px] uppercase tracking-wider font-bold"
                style={{
                  color: guard.underPressure ? "var(--crt-red)" : "var(--crt-green)",
                  background: `color-mix(in srgb, ${guard.underPressure ? "var(--crt-red)" : "var(--crt-green)"} 10%, transparent)`,
                  border: `1px solid color-mix(in srgb, ${guard.underPressure ? "var(--crt-red)" : "var(--crt-green)"} 30%, transparent)`,
                }}
                title="OOM guard: below the floor, least-recently-used managed apps are stopped before the kernel OOM killer fires"
              >
                {guard.underPressure ? "▲ pressure" : "● guarded"}
              </span>
              <span style={{ color: "var(--text-tertiary)" }}>
                floor {guard.minFreeMb > 0 ? fmtMB(guard.minFreeMb) : "off"} · cap{" "}
                {guard.maxRunning > 0 ? `${guard.maxRunning} apps` : "∞"} · idle{" "}
                {data.idle_seconds != null ? fmtIdle(data.idle_seconds) : "—"}
              </span>
              {(guard.pressureStops > 0 || guard.capStops > 0) && (
                <span style={{ color: "var(--crt-amber)" }} title={guard.lastStop ? `last: ${guard.lastStop.module} (${guard.lastStop.reason}) ${guard.lastStop.at}` : undefined}>
                  {guard.pressureStops + guard.capStops} evicted
                </span>
              )}
              {canControl && !knobDraft && (
                <button
                  onClick={() => setKnobDraft({ minFreeMb: String(guard.minFreeMb), maxRunning: String(guard.maxRunning) })}
                  className="text-[9.5px] px-1.5 py-0.5 rounded uppercase tracking-wider"
                  style={{ color: "var(--text-tertiary)", border: "1px solid var(--border-color)" }}
                  title="Edit the OOM-guard memory floor and running-app cap"
                >
                  tune
                </button>
              )}
            </div>
          )}
          {canControl && knobDraft && (
            <div className="flex items-center gap-2 flex-wrap">
              <label className="flex items-center gap-1" style={{ color: "var(--text-tertiary)" }}>
                floor MB
                <input
                  value={knobDraft.minFreeMb}
                  onChange={(e) => setKnobDraft({ ...knobDraft, minFreeMb: e.target.value.replace(/\D/g, "") })}
                  className="w-16 px-1 py-0.5 rounded text-[11px]"
                  style={{ background: "var(--bg-secondary)", border: "1px solid var(--border-color)", color: "var(--text-secondary)" }}
                  title="Keep at least this much memory free (0 = guard off)"
                />
              </label>
              <label className="flex items-center gap-1" style={{ color: "var(--text-tertiary)" }}>
                max apps
                <input
                  value={knobDraft.maxRunning}
                  onChange={(e) => setKnobDraft({ ...knobDraft, maxRunning: e.target.value.replace(/\D/g, "") })}
                  className="w-12 px-1 py-0.5 rounded text-[11px]"
                  style={{ background: "var(--bg-secondary)", border: "1px solid var(--border-color)", color: "var(--text-secondary)" }}
                  title="Cap on concurrently running managed apps (0 = uncapped)"
                />
              </label>
              <button
                onClick={saveKnobs}
                disabled={busy === "knobs"}
                className="text-[9.5px] px-2 py-0.5 rounded uppercase tracking-wider font-bold"
                style={{ color: "var(--crt-green)", border: "1px solid color-mix(in srgb, var(--crt-green) 40%, transparent)" }}
              >
                save
              </button>
              <button
                onClick={() => setKnobDraft(null)}
                className="text-[9.5px] px-2 py-0.5 rounded uppercase tracking-wider"
                style={{ color: "var(--text-tertiary)", border: "1px solid var(--border-color)" }}
              >
                cancel
              </button>
            </div>
          )}
        </div>
      )}

      {!data.activator && (
        <div className="text-[10px]" style={{ color: "var(--crt-amber)" }}>
          activator offline — apps are not being auto-slept/woken
        </div>
      )}

      {/* One row per provider: running (by RSS), then sleeping, then killed */}
      <div className="flex flex-col">
        {[...running, ...sleeping, ...stopped, ...installed].map((p) => {
          const act = p.activator;
          const down = p.sleeping || !!p.stopped;
          // "off" = installed on disk but never started (has a launch recipe);
          // "killed" = down and staying down (pm2-stopped, or sleep+disabled);
          // "asleep" = scale-to-zero'd, wakes on the next request.
          const state = p.installed
            ? "off"
            : p.stopped || (p.sleeping && act?.disabled)
              ? "killed"
              : p.sleeping
                ? "asleep"
                : act?.pinned
                  ? "pinned"
                  : "run";
          const stateColor =
            state === "killed"
              ? "var(--crt-red)"
              : state === "off" || p.sleeping
                ? "var(--text-tertiary)"
                : act?.pinned
                  ? "var(--crt-amber)"
                  : "var(--crt-green)";
          const memPctRow = mem && mem.total_mb > 0 ? (p.mem_mb / mem.total_mb) * 100 : 0;
          // Live apps report the start of their oldest process; down ones fall
          // back to pm2's record of the last start. Older APIs send neither.
          const upSince = (down ? p.last_up : p.started_at) ?? null;
          const isSelected = selected === p.module;
          return (
            <div
              key={p.module}
              className="flex flex-col border-b last:border-b-0"
              style={{
                borderColor: "color-mix(in srgb, var(--border-color) 55%, transparent)",
                background: isSelected ? "color-mix(in srgb, var(--crt-green) 5%, transparent)" : "transparent",
              }}
            >
            <div
              onClick={() => setSelected(isSelected ? null : p.module)}
              className="flex items-center gap-2 py-1 cursor-pointer"
              title={isSelected ? undefined : "Select to show this provider's CID"}
            >
              <span
                className="w-12 shrink-0 text-[9px] uppercase tracking-wider text-center px-1 py-0.5 rounded"
                style={{ color: stateColor, background: `color-mix(in srgb, ${stateColor} 8%, transparent)` }}
                title={act ? `activator-managed · idle ${fmtIdle(act.idleSeconds)}${act.disabled ? " · disabled" : ""}` : p.installed ? "installed but never started" : "not activator-managed (always on)"}
              >
                {state}
              </span>
              <span className="w-24 shrink-0 truncate font-bold" style={{ color: "var(--text-secondary)" }} title={p.category ? `${p.category}/${p.module}` : p.module}>
                {p.module}
              </span>
              <div className="flex-1 h-1.5 rounded-sm overflow-hidden min-w-8" style={{ background: "var(--bg-secondary)" }}>
                <div className="h-full" style={{ width: `${Math.min(100, memPctRow)}%`, background: utilColor(memPctRow * 3) }} />
              </div>
              <span className="w-14 text-right shrink-0" style={{ color: "var(--text-secondary)" }}>
                {down ? "—" : fmtMB(p.mem_mb)}
              </span>
              <span className="w-12 text-right shrink-0" style={{ color: "var(--text-tertiary)" }}>
                {down ? "" : `${p.cpu_pct.toFixed(0)}%`}
              </span>
              {/* Uptime for a live app, last-seen-up for a down one. */}
              <span
                className="w-16 text-right shrink-0"
                style={{ color: down ? "var(--text-tertiary)" : "var(--text-secondary)" }}
                title={
                  upSince != null
                    ? `${down ? "Last started" : "Up since"} ${new Date(upSince * 1000).toLocaleString()}`
                    : "Never started on this host"
                }
              >
                {upSince == null ? "—" : down ? `${fmtIdle(since(upSince))} ago` : fmtIdle(since(upSince))}
              </span>
              {canControl && (
                <span className="flex gap-1 shrink-0" onClick={(e) => e.stopPropagation()}>
                  {/* START and STOP always sit side by side, in the same place
                      on every row — the one that can't apply is greyed, not
                      hidden, so the buttons never move under the cursor. */}
                  <button
                    onClick={() => runApp(p)}
                    disabled={busy !== null || !down}
                    className="text-[9px] px-1.5 py-0.5 rounded uppercase font-bold"
                    style={{
                      color: "var(--crt-green)",
                      border: "1px solid color-mix(in srgb, var(--crt-green) 40%, transparent)",
                      opacity: busy || !down ? 0.35 : 1,
                      cursor: down && !busy ? "pointer" : "default",
                    }}
                    title={
                      !down
                        ? "Already running"
                        : act
                          ? "Start now and re-enable auto-wake"
                          : p.installed
                            ? "First start — bootstraps from the module's ecosystem.config.js / start.sh"
                            : "Start the app's pm2 processes"
                    }
                  >
                    {busy === `${p.module}:run` ? "…" : "start"}
                  </button>
                  <button
                    onClick={() => killApp(p)}
                    disabled={busy !== null || down}
                    className="text-[9px] px-1.5 py-0.5 rounded uppercase font-bold"
                    style={{
                      color: "var(--crt-red)",
                      border: "1px solid color-mix(in srgb, var(--crt-red) 40%, transparent)",
                      opacity: busy || down ? 0.35 : 1,
                      cursor: !down && !busy ? "pointer" : "default",
                    }}
                    title={
                      down
                        ? "Already stopped"
                        : act
                          ? "Kill every process attributed to this module and stay stopped (disables auto-wake until START)"
                          : "Kill every process attributed to this module"
                    }
                  >
                    {busy === `${p.module}:kill` ? "…" : "stop"}
                  </button>
                  {act && !down && (
                    <button
                      onClick={() => control(p.module, "sleep")}
                      disabled={busy !== null}
                      className="text-[9px] px-1.5 py-0.5 rounded uppercase"
                      style={{ color: "var(--text-tertiary)", border: "1px solid var(--border-color)", opacity: busy ? 0.5 : 1 }}
                      title="Stop now (wakes again on next request)"
                    >
                      {busy === `${p.module}:sleep` ? "…" : "sleep"}
                    </button>
                  )}
                  {act && (
                    <button
                      onClick={() => control(p.module, act.pinned ? "unpin" : "pin")}
                      disabled={busy !== null}
                      className="text-[9px] px-1.5 py-0.5 rounded uppercase"
                      style={{
                        color: act.pinned ? "var(--crt-amber)" : "var(--text-tertiary)",
                        border: `1px solid ${act.pinned ? "color-mix(in srgb, var(--crt-amber) 40%, transparent)" : "var(--border-color)"}`,
                        opacity: busy ? 0.5 : 1,
                      }}
                      title={act.pinned ? "Unpin — allow auto-sleep" : "Pin — never auto-sleep"}
                    >
                      {busy === `${p.module}:${act.pinned ? "unpin" : "pin"}` ? "…" : act.pinned ? "unpin" : "pin"}
                    </button>
                  )}
                </span>
              )}
            </div>
            {/* Selected row detail: the module's registry CID — the address of
                its snapshot in the mod-protocol registry. Click to copy. */}
            {isSelected && (
              <div className="flex items-center gap-2 pb-1.5 pl-14 pr-1">
                <span className="uppercase tracking-wider text-[9px] shrink-0" style={{ color: "var(--text-tertiary)" }}>
                  cid
                </span>
                {p.cid ? (
                  <>
                    <span
                      onClick={() => copyCid(p.cid!)}
                      className="truncate cursor-pointer select-all"
                      style={{ color: "var(--crt-green)" }}
                      title="Click to copy the registry CID"
                    >
                      {p.cid}
                    </span>
                    <span
                      onClick={() => copyCid(p.cid!)}
                      className="shrink-0 text-[9px] uppercase tracking-wider cursor-pointer"
                      style={{ color: copied ? "var(--crt-amber)" : "var(--text-tertiary)" }}
                    >
                      {copied ? "copied" : "copy"}
                    </span>
                  </>
                ) : (
                  <span style={{ color: "var(--text-tertiary)" }}>
                    not registered yet — autosnap mints a CID within a minute of first scan
                  </span>
                )}
              </div>
            )}
            </div>
          );
        })}
      </div>
      {actionErr && (
        <div className="text-[10px]" style={{ color: "var(--crt-red)" }}>
          {actionErr}
        </div>
      )}
      <div className="text-[9.5px] leading-relaxed" style={{ color: "var(--text-tertiary)" }}>
        {running.length} running · {sleeping.length} asleep · {stopped.length} killed
        {installed.length > 0 ? ` · ${installed.length} off` : ""}. The time column is how long an
        app has been up, or how long ago it was last started. Managed apps sleep after their
        idle timeout and wake on the next request; STOP takes down every process attributed to an
        app (supervisor stop + straggler sweep) until you START it again — START also boots an
        installed app that has never run; the OOM guard evicts least-recently-used apps
        when memory headroom drops below the floor.
      </div>
    </div>
  );
}
