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
import { HUB_BACKTEST_DAYS } from "./hubReplay";
import type { HubBacktest } from "./hubReplay";
import type { SavedIndex } from "./types";

const HUB_API = `${process.env.NEXT_PUBLIC_BASE_PATH ?? ""}/api/hub`;

/** The fetch loop: how much of the roster the server's feed cache holds, and
    when it last topped it up. Replays run over THIS — a card is only as
    current as the feed behind it, so the header reports both. */
export interface FeedStatus {
  at: number;
  nextAt: number;
  running: boolean;
  traders: number;
  cached: number;
  stale: number;
  missing: number;
  synced: number;
  cold: number;
  deferred: number;
  errors: number;
  error?: string;
}

export interface WorkerStatus {
  /** When the worker last finished a pass (ms epoch), 0 if never. */
  at: number;
  /** When the next pass is due. */
  nextAt: number;
  /** The worker's primary window, in days — always HUB_BACKTEST_DAYS. */
  days: number;
  /** Every window the last pass covered. A window absent from this list is one
      the hub replays in the browser instead. */
  windows?: number[];
  /** How many strats were in the last pass. */
  strats: number;
  /** True while a pass is in flight. */
  running: boolean;
  /** Last pass's failure, if it failed. */
  error?: string;
  /** The cache the pass replayed over. */
  feeds?: FeedStatus;
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
    saved ones — params only, no keys and no wallet state.

    `windows` says which windows to replay, and always names HUB_BACKTEST_DAYS
    as well as the one being viewed: the worker's coverage must not depend on
    where someone left a browser tab. `days` rides along for a server that
    predates `windows`. */
export async function publishHubManifest(indexes: SavedIndex[], days: number): Promise<void> {
  if (indexes.length === 0) return;
  try {
    await fetch(HUB_API, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({ days, windows: [HUB_BACKTEST_DAYS, days], strats: indexes }),
    });
  } catch {
    // The worker keeps its previous manifest; the hub still replays locally.
  }
}

/** What the given markets PAID OUT — leg key → 1 or 0 (see lib/leg.ts), for
 *  every id the server's resolution store already knows.
 *
 *  This is what lets the BACKTEST tab book its losses. Without it, inventory
 *  the leaders stopped trading is valued at the last price they printed, which
 *  is the entry price for anything that quietly expired worthless — so the tab
 *  would show a rosier number than the same strat's hub card. Missing ids are
 *  simply unknown (the store fills in the background); the replay reports how
 *  much of its result rests on them.
 *
 *  Batched to keep the request body sane; a 30-day window across a big
 *  watchlist can touch tens of thousands of markets. */
export async function fetchResolvedLegs(conditionIds: string[]): Promise<Map<string, number>> {
  const ids = [...new Set(conditionIds.filter(Boolean))];
  const out = new Map<string, number>();
  const CHUNK = 2000;
  for (let i = 0; i < ids.length; i += CHUNK) {
    try {
      const res = await fetch(HUB_API, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify({ resolve: ids.slice(i, i + CHUNK) }),
      });
      if (!res.ok) break;
      const body = (await res.json()) as { resolved?: Record<string, number> };
      for (const [leg, price] of Object.entries(body.resolved ?? {})) out.set(leg, price);
    } catch {
      break; // the tab marks what it can't resolve, and says so
    }
  }
  return out;
}

/** Ask the worker to run a pass NOW (the hub's ↻ RERUN button). Resolves once
    the pass is queued, not once it finishes. The pass replays over the cached
    feeds; use `triggerHubRefresh` to make it go and get newer data first. */
export async function triggerHubWorker(): Promise<boolean> {
  try {
    const res = await fetch(`${HUB_API}?run=1`, { method: "POST", headers: authHeaders() });
    return res.ok;
  } catch {
    return false;
  }
}

/** Ask the server to top up its trader-feed cache NOW. This is the only
    control in the hub that spends upstream requests. */
export async function triggerHubRefresh(): Promise<boolean> {
  try {
    const res = await fetch(`${HUB_API}?refresh=1`, { method: "POST", headers: authHeaders() });
    return res.ok;
  } catch {
    return false;
  }
}
