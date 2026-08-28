// What every market the replays touch actually PAID OUT — the ground truth a
// backtest needs to book its losses.
//
// A copy-trading replay only ever sees the leaders' fills. When a leader buys
// and never sells, the sim is left holding something, and it has to decide
// what that something is worth. Before this store it used the last price it
// had observed, which is systematically wrong in one direction: leaders trade
// their winners on the way up (so a winner's last print is near $1 and gets
// booked), and they simply let their losers expire (so a loser's last print is
// the ENTRY price and the loss never appears at all). That is the same shape
// as the live bug in polymarket_silent_expiry_losses — the ledger read +$96
// while the wallet held $0.70 — except in the backtest it was still running,
// and the hub was ranking strategies on it.
//
// The live engine already resolved this for real money (`fetch_closed_outcomes`
// in api/src/live_engine.rs). This is its backtest twin.
//
// Design notes:
//
//   • A resolution is IMMUTABLE. Once a market has paid out it can never say
//     anything else, so a hit is cached forever and never revalidated. That is
//     what makes this cheap: the store only ever grows toward the set of
//     markets the roster has touched, and a steady-state pass fetches nothing.
//   • A MISS is not a resolution. An open market, a market gamma doesn't know
//     about, a failed request — all of them mean "unknown", and the sim falls
//     back to marking (and says so). Nothing here is ever allowed to imply a
//     leg was worthless just because a lookup failed; a negative cached with
//     the same confidence as a positive is how you invent losses.
//   • Unknown ids are re-checked on a backoff (`RECHECK_MS`) because "open
//     today" becomes "resolved tomorrow", and 5-minute candle markets make
//     that transition constantly.

import { mkdirSync, readFileSync, renameSync, unlinkSync, writeFileSync } from "fs";
import { join } from "path";

import { fetchMarketResolutions } from "../polymarket";
import { legKey } from "../leg";
import { stateDir } from "./ownerToken";

/** An id we looked up and gamma had no resolution for is re-checked no more
    often than this. Short enough that a market resolving this afternoon is
    priced correctly by tonight's pass. */
export const RECHECK_MS = 6 * 3600_000;

/** Ceiling on ids fetched in ONE call, so a first pass over a fresh 30-day
    roster (tens of thousands of markets) can't monopolise the data-api budget
    the live engine shares. Whatever doesn't fit is picked up next pass —
    stalest first — and until then those legs settle as MARKED, which the
    result reports honestly rather than hiding. */
export const DEFAULT_FETCH_BUDGET = 600;

interface StoredResolution {
  /** outcome (lowercased) → payout 0 or 1. */
  legs: Record<string, number>;
  /** gamma endDate in ms, 0 if absent. */
  endMs: number;
}

interface StoreFile {
  /** conditionId → resolution. Immutable once written. */
  resolved: Record<string, StoredResolution>;
  /** conditionId → ms epoch of the last fruitless lookup. */
  unknown: Record<string, number>;
}

const emptyStore = (): StoreFile => ({ resolved: {}, unknown: {} });

function storePath(): string {
  const dir = stateDir();
  mkdirSync(dir, { recursive: true });
  return join(dir, "resolutions.json");
}

let mem: StoreFile | null = null;

function load(): StoreFile {
  if (mem) return mem;
  try {
    const raw = JSON.parse(readFileSync(storePath(), "utf8")) as Partial<StoreFile>;
    mem = { resolved: raw.resolved ?? {}, unknown: raw.unknown ?? {} };
  } catch {
    mem = emptyStore();
  }
  return mem;
}

function save(store: StoreFile): void {
  const path = storePath();
  const tmp = `${path}.${process.pid}.tmp`;
  try {
    writeFileSync(tmp, JSON.stringify(store));
    renameSync(tmp, path);
  } catch {
    try { unlinkSync(tmp); } catch { /* nothing to clean up */ }
  }
}

/** Forget the in-memory copy — tests only. */
export function resetResolutionMemo(): void {
  mem = null;
}

export interface ResolutionStats {
  /** Ids asked for. */
  asked: number;
  /** Ids answered from the store without any network. */
  cached: number;
  /** Ids actually answered upstream this call. */
  fetched: number;
  /** Ids whose lookup failed — retried next pass, never cached as a negative. */
  failed: number;
  /** Ids that came back resolved. */
  learned: number;
  /** Ids left unlooked-up because the budget ran out. */
  deferred: number;
}

/** The leg → payout map `runBacktestSim` takes, for the given markets.
 *
 *  Cached resolutions are free; unknown ids are fetched up to `budget` per
 *  call, oldest-checked first. The returned map only contains legs we KNOW —
 *  a caller must treat a missing key as "unknown", never as zero. */
export async function resolutionsFor(
  conditionIds: Iterable<string>,
  opts: { budget?: number; now?: number } = {},
): Promise<{ resolved: Map<string, number>; stats: ResolutionStats }> {
  const budget = opts.budget ?? DEFAULT_FETCH_BUDGET;
  const now = opts.now ?? Date.now();
  const store = load();
  const ids = [...new Set([...conditionIds].filter((c) => c && c.startsWith("0x")))];

  const stats: ResolutionStats = { asked: ids.length, cached: 0, fetched: 0, failed: 0, learned: 0, deferred: 0 };
  const missing: string[] = [];
  for (const id of ids) {
    if (store.resolved[id]) { stats.cached++; continue; }
    const checkedAt = store.unknown[id] ?? 0;
    if (now - checkedAt < RECHECK_MS) continue; // asked recently, still open
    missing.push(id);
  }

  // Stalest first: an id we've never checked (0) outranks one we checked this
  // morning, so a big roster converges instead of re-asking the same head.
  missing.sort((a, b) => (store.unknown[a] ?? 0) - (store.unknown[b] ?? 0));
  const take = missing.slice(0, Math.max(0, budget));
  stats.deferred = missing.length - take.length;

  if (take.length > 0) {
    const { resolutions, checked } = await fetchMarketResolutions(take);
    stats.fetched = checked.size;
    stats.failed = take.length - checked.size;
    for (const id of take) {
      const hit = resolutions.get(id);
      if (hit) {
        store.resolved[id] = { legs: hit.legs, endMs: hit.endMs };
        delete store.unknown[id];
        stats.learned++;
      } else if (checked.has(id)) {
        // Asked, and gamma has no resolution: still open. Not a loss — just
        // not knowable yet. Stamped so we back off before asking again.
        store.unknown[id] = now;
      }
      // Not checked at all (the request failed) ⇒ nothing is written, so the
      // next pass retries immediately instead of inheriting a fake negative.
    }
    save(store);
  }

  const resolved = new Map<string, number>();
  for (const id of ids) {
    const hit = store.resolved[id];
    if (!hit) continue;
    for (const [outcome, price] of Object.entries(hit.legs)) {
      resolved.set(legKey(id, outcome), price);
    }
  }
  return { resolved, stats };
}

/** Cached resolutions only — no network, for request paths that must answer
    immediately (the console asking what the server already knows). */
export function knownResolutions(conditionIds: Iterable<string>): Map<string, number> {
  const store = load();
  const out = new Map<string, number>();
  for (const id of new Set(conditionIds)) {
    const hit = store.resolved[id];
    if (!hit) continue;
    for (const [outcome, price] of Object.entries(hit.legs)) {
      out.set(legKey(id, outcome), price);
    }
  }
  return out;
}

export interface ResolutionCoverage {
  resolved: number;
  unknown: number;
}

/** How much of the store is fact vs still-open — for the worker's status line. */
export function resolutionCoverage(): ResolutionCoverage {
  const store = load();
  return {
    resolved: Object.keys(store.resolved).length,
    unknown: Object.keys(store.unknown).length,
  };
}
