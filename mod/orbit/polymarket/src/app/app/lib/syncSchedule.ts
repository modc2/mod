// Background sync schedule — client side of sync.rs.
//
// The API re-warms the trader leaderboards on a timer that runs on the SERVER,
// independent of whether this console is open. This module reads that schedule
// and lets the owner change it (every route here is behind the owner-only
// access gate, so no extra check is needed client side).

import { API_BASE } from "./polymarket";

/** One leaderboard the server keeps warm. These three fields ARE the server's
    pipeline cache key, which is why they are the thing you can configure: a
    board whose (days, minPerDay, pool) isn't on this list was never
    aggregated, so opening it triggers a ~10-minute cold rebuild the fleet
    activator kills long before it finishes. Pin the view, and it loads. */
export interface WarmWindow {
  days: number;
  minPerDay: number;
  pool: number;
}

/** The server's cache key for a window — same format sync.rs builds, so the
    console can tell "this view is warm" from "this view will go cold". */
export function windowKey(w: WarmWindow): string {
  // `String()` matches Rust's `{}` on an f64 for the values this field can
  // hold: 0 → "0", 2.5 → "2.5".
  return `${w.days}:${String(w.minPerDay)}:${w.pool}`;
}

/** "30D · TOP 2000" / "3D · ≥2/DAY · TOP 500" — a window as a chip label. */
export function describeWindow(w: WarmWindow): string {
  const parts = [`${w.days}D`];
  if (w.minPerDay > 0) parts.push(`≥${w.minPerDay}/DAY`);
  parts.push(`TOP ${w.pool}`);
  return parts.join(" · ");
}

export function hasWindow(list: WarmWindow[], w: WarmWindow): boolean {
  const k = windowKey(w);
  return list.some((x) => windowKey(x) === k);
}

export interface SyncSchedule {
  enabled: boolean;
  intervalSecs: number;
  minIntervalSecs: number;
  maxIntervalSecs: number;
  /** The boards kept warm, in the order the owner listed them. */
  windows: WarmWindow[];
  maxWindows: number;
  minPool: number;
  maxPool: number;
  maxDays: number;
  running: boolean;
  /** Unix SECONDS (not ms) — null until the first cycle of this API process. */
  lastRunAt: number | null;
  lastFinishedAt: number | null;
  lastDurationSecs: number | null;
  lastError: string | null;
  lastTrigger: string | null;
  runs: number;
  nextRunAt: number | null;
  /** Server clock, so countdowns don't inherit the browser's clock skew. */
  now: number;
  configPath: string;
}

/** An API binary from before the warm list was configurable answers
    /sync/status without these fields. Default them rather than letting the
    chip crash on `sched.windows.map` — the cadence half of the panel still
    works against an older server. */
function normalize(raw: Partial<SyncSchedule>): SyncSchedule {
  return {
    ...(raw as SyncSchedule),
    windows: Array.isArray(raw.windows) ? raw.windows : [],
    maxWindows: raw.maxWindows ?? 8,
    minPool: raw.minPool ?? 50,
    maxPool: raw.maxPool ?? 2000,
    maxDays: raw.maxDays ?? 365,
  };
}

export async function fetchSyncSchedule(): Promise<SyncSchedule> {
  const res = await fetch(`${API_BASE}/sync/status`);
  if (!res.ok) throw new Error(`sync status ${res.status}`);
  return normalize(await res.json());
}

export async function updateSyncSchedule(patch: {
  enabled?: boolean;
  intervalSecs?: number;
  /** Sent WHOLE — the list is the setting, not a merge target. */
  windows?: WarmWindow[];
}): Promise<SyncSchedule> {
  const res = await fetch(`${API_BASE}/sync/config`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data?.error || `sync config ${res.status}`);
  return normalize(data as Partial<SyncSchedule>);
}

/** Ask the server to run a cycle now. Returns as soon as it's queued. */
export async function runSyncNow(): Promise<SyncSchedule> {
  const res = await fetch(`${API_BASE}/sync/run`, { method: "POST" });
  if (!res.ok) throw new Error(`sync run ${res.status}`);
  return normalize(await res.json());
}

/** "15M" / "1H" / "2H 30M" / "24H" — the cadence as the chip shows it. */
export function formatInterval(secs: number): string {
  if (secs < 3600) return `${Math.round(secs / 60)}M`;
  const hrs = Math.floor(secs / 3600);
  const mins = Math.round((secs % 3600) / 60);
  return mins ? `${hrs}H ${mins}M` : `${hrs}H`;
}

/** "in 42m" / "in 1h 4m" / "due now" — countdown to the next cycle. */
export function formatCountdown(secs: number): string {
  if (!Number.isFinite(secs) || secs <= 0) return "due now";
  if (secs < 60) return `in ${Math.round(secs)}s`;
  const min = Math.floor(secs / 60);
  if (min < 60) return `in ${min}m`;
  const hr = Math.floor(min / 60);
  return `in ${hr}h ${min % 60}m`;
}
