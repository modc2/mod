// When the server actually goes to the network for a trader.
//
// The rule this file exists to enforce: **the backtester never fetches.** It
// replays whatever feedStore.ts already holds. Fetching is a separate, paced,
// budgeted loop that tops up feeds that have gone stale — so a backtest pass
// costs no upstream requests at all once the store is warm, and the request
// rate is a function of the roster and the refresh cadence rather than of how
// often somebody opens the hub.
//
// Two entry points:
//
//   • `refreshRoster` — the fetch loop. Stalest first, a bounded number of
//     traders per cycle, paced so a 60-trader roster trickles instead of
//     bursting 60 paginated walks into data-api's Cloudflare limiter.
//   • `feedSession` — the loader a replay pass hands to hubReplay. Serves the
//     store; only a trader with NO feed at all (a strat published a minute
//     ago) triggers an inline fetch, and even that is capped per pass. Traders
//     over the cap come back empty and are reported as `warming`, because a
//     card that prints $0 over data we never fetched is a lie.
//
// Everything incremental goes through `fetchWalletTradesIncremental`, the same
// function the live engine and console use: page /activity from the newest
// trade until it hits a row we already have, then merge. One page instead of
// sixty, per trader per refresh.

import {
  MAX_LOOKBACK_DAYS, fetchPositions, fetchWalletTradesIncremental, fetchWalletTradesUntil,
} from "../polymarket";
import type { TraderFeed } from "../hubReplay";
import {
  coverage, emptyMeta, readFeed, readMeta, writeFeed, writeMeta,
  type FeedCoverage, type FeedMeta,
} from "./feedStore";

/** How old a trade feed may get before the refresh loop tops it up. Below the
    loop's own cadence on purpose: a feed skipped because a cycle ran out of
    budget is still due on the next one. */
export const TRADES_TTL_MS = 20 * 60_000;
/** Positions are a snapshot, not an append-only log — refreshing them costs a
    full re-fetch, so they move slower than trades. The replay uses them for
    open-position context, which is exactly what tolerates being minutes old. */
export const POSITIONS_TTL_MS = 45 * 60_000;

/** Concurrent upstream walks. Deliberately tiny: this shares data-api's
    per-IP budget with the live copy engine, whose fills are time-critical and
    whose requests must never queue behind a backtest warmup. */
const CONCURRENCY = 2;
/** Minimum wall-clock between two syncs starting. At 400ms a 40-trader cycle
    spreads over ~8s of upstream time instead of arriving as one burst. */
const MIN_GAP_MS = 400;
/** Traders refreshed per cycle. A roster larger than this rotates: stalest
    first, so everyone comes round within a few cycles. */
const REFRESH_BUDGET = 40;
/** Cold backfills a single replay pass may trigger inline. A cold feed is a
    full 30-day walk (tens of pages), so this is much smaller than the refresh
    budget — the rest are picked up by the next fetch cycle. */
const INLINE_COLD_BUDGET = 4;

/** Backoff after consecutive failures: 1m, 4m, 16m, capped at 1h. A trader
    whose feed 429s repeatedly stops being retried every cycle. */
function backoffMs(fails: number): number {
  if (fails <= 0) return 0;
  return Math.min(60_000 * 4 ** (fails - 1), 3600_000);
}

// ── Pacing ──────────────────────────────────────────────────────
// One gate for every upstream walk this module starts, shared across the
// refresh loop and any inline backfill, so the two can't double the rate by
// running at the same time.

let active = 0;
let lastStart = 0;
const waiting: (() => void)[] = [];

function release(): void {
  active--;
  const next = waiting.shift();
  if (next) next();
}

async function withSlot<T>(fn: () => Promise<T>): Promise<T> {
  if (active >= CONCURRENCY) {
    await new Promise<void>((resolve) => waiting.push(resolve));
  }
  active++;
  const gap = MIN_GAP_MS - (Date.now() - lastStart);
  if (gap > 0) await new Promise((r) => setTimeout(r, gap));
  lastStart = Date.now();
  try {
    return await fn();
  } finally {
    release();
  }
}

// A trader can be due from the refresh loop and missing from a replay pass at
// the same instant; without this both would walk /activity for them.
const inFlight = new Map<string, Promise<FeedMeta>>();

/** True when this address is due for a network sync. Reads meta only — the
    scheduler asks this for the whole roster every cycle. */
function isDue(meta: FeedMeta | null, maxAgeMs: number): boolean {
  if (!meta || meta.tradesAt === 0) return true;
  if (Date.now() - meta.attemptedAt < backoffMs(meta.fails)) return false;
  return Date.now() - meta.tradesAt > maxAgeMs;
}

/** Fetch one trader into the store. Incremental when we already have a
    baseline, a full 30-day walk when we don't. Never throws: a failed sync
    records the error and leaves the previous feed readable, because a stale
    backtest beats no backtest. Returns the resulting META — callers that want
    the payload read it back, and the scheduler never needs it. */
export function syncFeed(address: string): Promise<FeedMeta> {
  const key = address.toLowerCase();
  const running = inFlight.get(key);
  if (running) return running;

  const run = (async (): Promise<FeedMeta> => {
    const prevMeta = readMeta(key) ?? emptyMeta(key);
    const prev = prevMeta.tradeCount > 0 || prevMeta.positionCount > 0 ? readFeed(key) : null;
    const prevTrades = prev?.trades ?? [];
    const cutoffMs = Date.now() - MAX_LOOKBACK_DAYS * 86400_000;
    const cutoffSec = Math.floor(cutoffMs / 1000);
    try {
      const trades = await withSlot(() =>
        prevTrades.length > 0
          ? fetchWalletTradesIncremental(key, prevTrades, cutoffSec)
          : fetchWalletTradesUntil(key, cutoffSec, undefined, 4000),
      );
      // Positions age on their own clock — a trade sync shouldn't drag a
      // second paginated fetch along with it every time.
      let positions = prev?.positions ?? [];
      let positionsAt = prevMeta.positionsAt;
      if (Date.now() - prevMeta.positionsAt > POSITIONS_TTL_MS) {
        try {
          positions = await withSlot(() => fetchPositions(key));
          positionsAt = Date.now();
        } catch {
          // Keep the last book; the trades half of the sync still counts.
        }
      }
      const now = Date.now();
      const meta: FeedMeta = {
        ...prevMeta,
        tradesAt: now,
        positionsAt,
        // A full walk establishes coverage; an incremental one inherits it.
        coveredFromMs: prevTrades.length > 0 && prevMeta.coveredFromMs > 0
          ? Math.max(prevMeta.coveredFromMs, cutoffMs)
          : cutoffMs,
        fails: 0,
        attemptedAt: now,
        lastError: undefined,
        tradeCount: trades.length,
        positionCount: positions.length,
      };
      writeFeed({ meta, trades, positions });
      return meta;
    } catch (e) {
      // Payload untouched — only the failure state moves.
      const meta: FeedMeta = {
        ...prevMeta,
        fails: prevMeta.fails + 1,
        attemptedAt: Date.now(),
        lastError: e instanceof Error ? e.message : String(e),
      };
      writeMeta(meta);
      return meta;
    } finally {
      inFlight.delete(key);
    }
  })();

  inFlight.set(key, run);
  return run;
}

export interface RefreshStats {
  /** Roster size this cycle looked at. */
  traders: number;
  /** Traders that were due and got synced. */
  synced: number;
  /** Of those, ones with no prior feed (a full 30-day backfill). */
  cold: number;
  /** Due but left for the next cycle — budget ran out. */
  deferred: number;
  /** Syncs that failed. */
  errors: number;
  /** Store coverage AFTER the cycle. */
  coverage: FeedCoverage;
}

/** One fetch cycle over a roster. Returns what it did, for the status line. */
export async function refreshRoster(
  addresses: string[],
  opts: { budget?: number; maxAgeMs?: number } = {},
): Promise<RefreshStats> {
  const budget = opts.budget ?? REFRESH_BUDGET;
  const maxAge = opts.maxAgeMs ?? TRADES_TTL_MS;
  const roster = [...new Set(addresses.map((a) => a.toLowerCase()))];

  const due = roster
    .map((addr) => ({ addr, meta: readMeta(addr) }))
    .filter((r) => isDue(r.meta, maxAge))
    // Never-fetched first (a strat with no data can't be backtested at all),
    // then oldest. A roster bigger than the budget still rotates fully.
    .sort((a, b) => (a.meta?.tradesAt ?? 0) - (b.meta?.tradesAt ?? 0));

  const take = due.slice(0, budget);
  let cold = 0;
  let errors = 0;
  // Sequential by design — `withSlot` provides the concurrency, and issuing
  // them all at once would just build a queue that ignores the budget.
  for (const { addr, meta } of take) {
    const wasCold = !meta || meta.tradesAt === 0;
    const after = await syncFeed(addr);
    if (wasCold) cold++;
    if (after.lastError) errors++;
  }

  return {
    traders: roster.length,
    synced: take.length,
    cold,
    deferred: due.length - take.length,
    errors,
    coverage: coverage(roster, maxAge),
  };
}

export interface SessionStats {
  /** Served straight from the store, no network. */
  hits: number;
  /** Served from the store while past its freshness window (the fetch loop
      owns topping these up; the replay does not block on it). */
  stale: number;
  /** Backfilled inline because there was nothing cached at all. */
  cold: number;
  /** Cold, but over the inline budget — replayed as empty and reported. */
  warming: number;
}

export interface FeedSession {
  load: (addr: string, cache: Map<string, Promise<TraderFeed>>) => Promise<TraderFeed>;
  stats: SessionStats;
  /** Addresses this pass wanted but had no data for — the fetch loop's
      priority queue for the next cycle. */
  pending: Set<string>;
}

/** The loader a replay pass uses. Reads the store; fetches only what has never
    been fetched, and only up to `coldBudget` of those. */
export function feedSession(opts: { coldBudget?: number } = {}): FeedSession {
  let coldBudget = opts.coldBudget ?? INLINE_COLD_BUDGET;
  const stats: SessionStats = { hits: 0, stale: 0, cold: 0, warming: 0 };
  const pending = new Set<string>();

  const load = async (
    addr: string,
    cache: Map<string, Promise<TraderFeed>>,
  ): Promise<TraderFeed> => {
    const key = addr.toLowerCase();
    const hit = cache.get(key);
    if (hit) return hit;

    const p = (async (): Promise<TraderFeed> => {
      const meta = readMeta(key);
      if (meta && meta.tradesAt > 0) {
        const stored = readFeed(key);
        if (stored) {
          if (Date.now() - meta.tradesAt > TRADES_TTL_MS) stats.stale++;
          else stats.hits++;
          return { positions: stored.positions, trades: stored.trades };
        }
        // Meta without a payload: the file was lost or truncated. Treat it as
        // uncached and let the cold path below rebuild it.
      }
      if (coldBudget > 0) {
        coldBudget--;
        stats.cold++;
        const fresh = await syncFeed(key);
        if (fresh.tradesAt > 0) {
          const stored = readFeed(key);
          if (stored) return { positions: stored.positions, trades: stored.trades };
        }
      }
      stats.warming++;
      pending.add(key);
      return { positions: [], trades: [] };
    })();

    cache.set(key, p);
    return p;
  };

  return { load, stats, pending };
}
