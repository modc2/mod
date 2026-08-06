"use client";

// Client side of the background backtest cache.
//
// The console talks to the worker through one route (`/polymarket/api/hub`):
// it POSTs the roster the worker should replay (strats live in this browser's
// localStorage — the server has no other way to learn about them) and GETs
// whatever the worker's last 2-hourly pass produced.
//
// Both directions carry the owner Bearer token. The route is served by the
// Next app, not the Rust API, so access.ts's fetch patch — which only stamps
// requests aimed at the API base — doesn't cover it; we attach it here.

import { getAccessToken } from "./access";
import type { HubBacktest } from "./hubReplay";
import type { SavedIndex } from "./types";

const HUB_API = `${process.env.NEXT_PUBLIC_BASE_PATH ?? ""}/api/hub`;

export interface WorkerStatus {
  /** When the worker last finished a pass (ms epoch), 0 if never. */
  at: number;
  /** When the next pass is due. */
  nextAt: number;
  /** Window the worker replays, in days. */
  days: number;
  /** How many strats were in the last pass. */
  strats: number;
  /** True while a pass is in flight. */
  running: boolean;
  /** Last pass's failure, if it failed. */
  error?: string;
}

export interface WorkerCache {
  status: WorkerStatus;
  results: Record<string, HubBacktest>;
}

function authHeaders(): Record<string, string> {
  const t = getAccessToken();
  return t ? { Authorization: `Bearer ${t}` } : {};
}

/** The worker's cached results for one window. null on any failure — the hub
    falls back to replaying in the browser, which is what it always did. */
export async function fetchWorkerBacktests(days: number): Promise<WorkerCache | null> {
  try {
    const res = await fetch(`${HUB_API}?days=${days}`, {
      headers: authHeaders(),
      cache: "no-store",
    });
    if (!res.ok) return null;
    return (await res.json()) as WorkerCache;
  } catch {
    return null;
  }
}

/** Publish the roster the worker should keep warm. Strats are the user's own
    saved ones — params only, no keys and no wallet state. */
export async function publishHubManifest(indexes: SavedIndex[], days: number): Promise<void> {
  if (indexes.length === 0) return;
  try {
    await fetch(HUB_API, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({ days, strats: indexes }),
    });
  } catch {
    // The worker keeps its previous manifest; the hub still replays locally.
  }
}

/** Ask the worker to run a pass NOW (the hub's ↻ RERUN button). Resolves once
    the pass is queued, not once it finishes. */
export async function triggerHubWorker(): Promise<boolean> {
  try {
    const res = await fetch(`${HUB_API}?run=1`, { method: "POST", headers: authHeaders() });
    return res.ok;
  } catch {
    return false;
  }
}
