// Background sync schedule — client side of sync.rs.
//
// The API re-warms the trader leaderboards on a timer that runs on the SERVER,
// independent of whether this console is open. This module reads that schedule
// and lets the owner change it (every route here is behind the owner-only
// access gate, so no extra check is needed client side).

import { API_BASE } from "./polymarket";

export interface SyncSchedule {
  enabled: boolean;
  intervalSecs: number;
  minIntervalSecs: number;
  maxIntervalSecs: number;
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

export async function fetchSyncSchedule(): Promise<SyncSchedule> {
  const res = await fetch(`${API_BASE}/sync/status`);
  if (!res.ok) throw new Error(`sync status ${res.status}`);
  return res.json();
}

export async function updateSyncSchedule(patch: {
  enabled?: boolean;
  intervalSecs?: number;
}): Promise<SyncSchedule> {
  const res = await fetch(`${API_BASE}/sync/config`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data?.error || `sync config ${res.status}`);
  return data as SyncSchedule;
}

/** Ask the server to run a cycle now. Returns as soon as it's queued. */
export async function runSyncNow(): Promise<SyncSchedule> {
  const res = await fetch(`${API_BASE}/sync/run`, { method: "POST" });
  if (!res.ok) throw new Error(`sync run ${res.status}`);
  return res.json();
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
