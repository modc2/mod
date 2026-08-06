// The background backtest worker.
//
// Every strat the console has published gets replayed over the same 1-day
// window every 2 hours, whether or not anybody has the console open, and the
// results are written to ~/.mod/polymarket/hub/backtests.json. Opening /strats
// then paints a wall of real backtests on the first frame instead of firing a
// dozen paginated /activity walks and watching cards trickle in.
//
// It runs inside the Next server (started from instrumentation.ts) rather than
// as its own service for one reason: the replay is the app's own TypeScript —
// lib/hubReplay.ts → lib/backtest.ts → lib/strats/strat.ts, the exact code the
// console and the BACKTEST tab run. A separate worker process would mean a
// second build of the engine, and a second engine is how backtest and live
// drifted apart the last time (see lib/strats/parity.fixture.json).
//
// It authenticates to the Rust API as the owner (mintOwnerToken) because every
// data route is behind the access gate, and it fetches nothing the console
// wouldn't: /activity, /positions, /live/bankroll.

import { mkdirSync, readFileSync, writeFileSync } from "fs";
import { join } from "path";

import { setServerAuthToken } from "../polymarket";
import { DEFAULT_STRATS } from "../defaultStrats";
import {
  HUB_BACKTEST_DAYS, backtestOne, backtestTemplate, templateBacktestKey,
  type HubBacktest, type TraderFeed,
} from "../hubReplay";
import type { SavedIndex } from "../types";
import { mintOwnerToken, stateDir } from "./ownerToken";

/** How often every strat is replayed. */
const INTERVAL_MS = 2 * 3600_000;
/** Delay before the first pass — let the API finish booting (and its own
    trader sync warm the caches) before a dozen backtests pile in. */
const BOOT_DELAY_MS = 45_000;

export interface HubManifest {
  /** Window to replay, in days. */
  days: number;
  /** The strats to replay — published by the console. */
  strats: SavedIndex[];
  at: number;
}

export interface HubCacheFile {
  status: {
    at: number;
    nextAt: number;
    days: number;
    strats: number;
    running: boolean;
    error?: string;
  };
  /** `<card key>@<days>d` → result. Same key shape the browser snapshot uses,
      so the two caches merge without translation. */
  results: Record<string, HubBacktest>;
}

function hubDir(): string {
  const dir = join(stateDir(), "hub");
  mkdirSync(dir, { recursive: true });
  return dir;
}
const manifestPath = () => join(hubDir(), "manifest.json");
const cachePath = () => join(hubDir(), "backtests.json");

function readJson<T>(path: string, fallback: T): T {
  try {
    return JSON.parse(readFileSync(path, "utf8")) as T;
  } catch {
    return fallback;
  }
}

export function readManifest(): HubManifest {
  return readJson<HubManifest>(manifestPath(), { days: HUB_BACKTEST_DAYS, strats: [], at: 0 });
}

export function writeManifest(m: HubManifest): void {
  writeFileSync(manifestPath(), JSON.stringify(m));
}

export function readCache(): HubCacheFile {
  return readJson<HubCacheFile>(cachePath(), {
    status: { at: 0, nextAt: 0, days: HUB_BACKTEST_DAYS, strats: 0, running: false },
    results: {},
  });
}

function writeCache(c: HubCacheFile): void {
  writeFileSync(cachePath(), JSON.stringify(c));
}

let running = false;
let timer: ReturnType<typeof setTimeout> | null = null;

/** Replay every published strat + every recommended template once. Results are
    written as they land, so a long pass still leaves the console something to
    read — and a crash mid-pass costs only the strats it hadn't reached. */
export async function runPass(): Promise<HubCacheFile> {
  const cache = readCache();
  if (running) return cache;
  const token = mintOwnerToken();
  if (!token) {
    cache.status = { ...cache.status, error: "no owner/secret — access gate unconfigured", running: false };
    writeCache(cache);
    return cache;
  }
  setServerAuthToken(token);

  running = true;
  const manifest = readManifest();
  const days = manifest.days || HUB_BACKTEST_DAYS;
  cache.status = {
    at: cache.status.at, nextAt: Date.now() + INTERVAL_MS, days,
    strats: manifest.strats.length, running: true,
  };
  writeCache(cache);

  // One feed cache for the whole pass: strats overlap heavily on traders, and
  // each trader is a paginated 30-day /activity walk.
  const feeds = new Map<string, Promise<TraderFeed>>();
  const publish = (key: string, bt: HubBacktest) => {
    cache.results[`${key}@${days}d`] = { ...bt, by: "worker" };
    writeCache(cache);
  };

  let error: string | undefined;
  try {
    for (const idx of manifest.strats) {
      const bt = await backtestOne(idx, days, feeds);
      if (bt) publish(idx.id, bt);
    }
    for (const t of DEFAULT_STRATS) {
      const bt = await backtestTemplate(t, days, feeds);
      if (bt) publish(templateBacktestKey(t.slug), bt);
    }
  } catch (e) {
    error = e instanceof Error ? e.message : String(e);
  } finally {
    running = false;
  }

  const now = Date.now();
  cache.status = {
    at: now, nextAt: now + INTERVAL_MS, days,
    strats: manifest.strats.length, running: false,
    ...(error ? { error } : {}),
  };
  writeCache(cache);
  return cache;
}

/** Queue a pass without waiting for it (the route's `?run=1`). */
export function triggerPass(): boolean {
  if (running) return false;
  setTimeout(() => { void runPass(); }, 0);
  return true;
}

export function workerRunning(): boolean {
  return running;
}

/** Start the 2-hourly loop. Idempotent: Next may call `register()` more than
    once in a process (dev HMR), and a second timer would double every fetch. */
export function startHubWorker(): void {
  if (timer) return;
  const schedule = (delay: number) => {
    timer = setTimeout(async () => {
      try {
        await runPass();
      } catch {
        // A failed pass is recorded in the cache status; the loop continues.
      }
      schedule(INTERVAL_MS);
    }, delay);
    // Never hold the process open for a backtest.
    if (typeof timer.unref === "function") timer.unref();
  };
  // A restart shouldn't re-run a pass that's still fresh — a deploy loop would
  // otherwise re-backtest the whole hub every time the app bounced.
  const age = Date.now() - readCache().status.at;
  schedule(age >= INTERVAL_MS ? BOOT_DELAY_MS : Math.max(BOOT_DELAY_MS, INTERVAL_MS - age));
}
