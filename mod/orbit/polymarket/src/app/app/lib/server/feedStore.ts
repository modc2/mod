// The server's trader-feed store — the cache the background backtester
// replays over.
//
// The console has one: lib/cache.ts keeps every trader's 30-day trade history
// in localStorage, hour-bucketed, so opening a strat twice costs one fetch.
// The Next server had none — `getCached`/`setCache` return null and no-op when
// `window` is undefined — so the hub worker re-walked a paginated 30-day
// /activity feed for EVERY trader of EVERY strat on every pass. The Rust proxy
// (api/src/cache.rs) held those pages for an hour; the worker ran every two.
// Every pass therefore missed both caches by construction and went upstream,
// which is the data-api 429s the hub kept blaming on "rate limits".
//
// This is the missing half: the same 30-day window, on disk under
// `~/.mod/polymarket/feeds/`, surviving restarts. It is a STORE, not a
// fetcher — feedFetcher.ts decides when it needs filling, and hubWorker.ts
// replays out of it.
//
// Each trader is TWO files:
//
//   <addr>.meta.json   a few hundred bytes: when it was last synced, how far
//                      back it reaches, failure state, row counts
//   <addr>.json        the payload — trades + positions, up to a couple of MB
//
// The split is not tidiness. A 73-trader roster is ~100MB of payload; the
// scheduler asks "is this due?" for all of them every cycle and the status
// line asks "how much is cached?" on every console poll. Parsing 100MB of
// JSON to answer either would cost more than the fetching it exists to avoid.
// Only code that actually replays a trader loads their payload.

import { mkdirSync, readdirSync, readFileSync, renameSync, statSync, unlinkSync, writeFileSync } from "fs";
import { join } from "path";

import type { PolymarketPosition, PolymarketTrade } from "../types";
import { stateDir } from "./ownerToken";

/** Hard cap on stored trades per address. A 30-day window for the most active
    copy targets is a few thousand rows; this bounds a pathological account to
    a file the worker can still parse in a few ms. Newest wins — the backtest
    windows are all ≤ 30d and read from the recent end. */
const MAX_TRADES_PER_FEED = 8000;

/** A feed that hasn't been synced in this long — which means it dropped off
    every roster, since anything on a roster is synced every cycle — is
    deleted. A strat the user retired shouldn't keep megabytes forever. */
export const FEED_IDLE_TTL_MS = 7 * 86400_000;

/** Parsed payloads held between calls. Small on purpose: within one replay
    pass the caller already memoizes feeds in its own map, so this only has to
    cover a read-then-write pair. Holding all 73 would be ~400MB of live heap
    inside a Next server that also has an app to serve. */
const MEM_LIMIT = 4;

/** Bumped whenever stored rows mean something different from what an older
    sync wrote, so `feedFetcher` knows to throw the payload away and cold-walk
    it again instead of incrementally topping up a feed that is wrong.
    1 → 2: rows are aggregated per leader action instead of per fill (feeds
    written before this are missing the 2nd..Nth fill of every book-walking
    order), and `coveredFromMs` reflects the window actually held. */
export const FEED_FORMAT = 2;

export interface FeedMeta {
  address: string;
  /** `FEED_FORMAT` this payload was written under. Absent = 1. */
  format?: number;
  /** ms epoch of the last successful trades sync. 0 = never fetched. */
  tradesAt: number;
  /** ms epoch of the last successful positions sync. */
  positionsAt: number;
  /** Left edge of the window actually walked (ms epoch). A feed backfilled to
      the 30-day ceiling sits at ~now-30d; a replay asking for a window older
      than this is asking for data that was never fetched.

      This used to be set to the 30-day cutoff unconditionally, on every sync,
      whether or not the walk got that far. It usually didn't: the cold walk
      stops after `maxTrades` rows, and 133 of 169 stored feeds turned out to
      hold a median of 12.9 days while this field claimed 30. A 30-day card
      over one of those replays an empty first half as if the trader had gone
      quiet — which reads as a real (and flattering) change in behaviour. */
  coveredFromMs: number;
  /** True when the last full walk stopped on the row cap instead of reaching
      its cutoff, i.e. `coveredFromMs` is the trader's activity ceiling and not
      the requested window. Purely informational — `coveredFromMs` is already
      honest — but it distinguishes "quiet trader" from "we ran out of pages".*/
  truncated?: boolean;
  /** Consecutive failed syncs — feedFetcher's backoff exponent. Reset on any
      success, INCLUDING one that returned nothing (an inactive trader is a
      real answer, not a failure). */
  fails: number;
  /** ms epoch of the last attempt, successful or not. The backoff clock. */
  attemptedAt: number;
  lastError?: string;
  /** Row counts, so status and coverage never touch the payload. */
  tradeCount: number;
  positionCount: number;
}

export interface StoredFeed {
  meta: FeedMeta;
  trades: PolymarketTrade[];
  positions: PolymarketPosition[];
}

export function feedsDir(): string {
  const dir = join(stateDir(), "feeds");
  mkdirSync(dir, { recursive: true });
  return dir;
}

/** Addresses are 0x + 40 hex from every caller, but this builds a path:
    anything else gets flattened rather than escaping the directory. */
function slug(address: string): string {
  return address.toLowerCase().replace(/[^a-z0-9]/g, "");
}
const payloadPath = (addr: string) => join(feedsDir(), `${slug(addr)}.json`);
const metaPath = (addr: string) => join(feedsDir(), `${slug(addr)}.meta.json`);

// NOTE: no `format` here on purpose. `readMeta` builds its result as
// `{...emptyMeta(), ...rawFileContents}`, so any field defaulted here is
// silently claimed by a file that never recorded it — a `format: FEED_FORMAT`
// default made every pre-existing feed read back as already-current and the
// one-time rebuild never ran. Absent means "unknown", which is what a file
// written before the field existed actually is.
export function emptyMeta(address: string): FeedMeta {
  return {
    address: address.toLowerCase(),
    tradesAt: 0,
    positionsAt: 0,
    coveredFromMs: 0,
    fails: 0,
    attemptedAt: 0,
    tradeCount: 0,
    positionCount: 0,
  };
}

function readJsonFile<T>(path: string): T | null {
  try {
    return JSON.parse(readFileSync(path, "utf8")) as T;
  } catch {
    // A half-written or corrupt file is a cache entry, not a crash.
    return null;
  }
}

/** Write via tmp + rename: the fetch loop and a replay pass live in the same
    process but not the same tick, and a reader must never see a truncation. */
function writeAtomic(path: string, body: string): void {
  const tmp = `${path}.${process.pid}.tmp`;
  try {
    writeFileSync(tmp, body);
    renameSync(tmp, path);
  } catch {
    try {
      unlinkSync(tmp);
    } catch {
      // nothing to clean up
    }
  }
}

// meta is tiny (a few hundred bytes × roster), so it's cached in full and
// revalidated by mtime.
const metaMem = new Map<string, { mtimeMs: number; meta: FeedMeta }>();

export function readMeta(address: string): FeedMeta | null {
  const key = address.toLowerCase();
  const path = metaPath(key);
  let mtimeMs: number;
  try {
    mtimeMs = statSync(path).mtimeMs;
  } catch {
    metaMem.delete(key);
    return null;
  }
  const hit = metaMem.get(key);
  if (hit && hit.mtimeMs === mtimeMs) return hit.meta;
  const raw = readJsonFile<Partial<FeedMeta>>(path);
  if (!raw) {
    metaMem.delete(key);
    return null;
  }
  const meta: FeedMeta = { ...emptyMeta(key), ...raw, address: key };
  metaMem.set(key, { mtimeMs, meta });
  return meta;
}

const payloadMem = new Map<string, { mtimeMs: number; feed: StoredFeed }>();

/** The full feed. Only callers that actually replay a trader want this. */
export function readFeed(address: string): StoredFeed | null {
  const key = address.toLowerCase();
  const meta = readMeta(key);
  if (!meta) return null;
  const path = payloadPath(key);
  let mtimeMs: number;
  try {
    mtimeMs = statSync(path).mtimeMs;
  } catch {
    payloadMem.delete(key);
    return null;
  }
  const hit = payloadMem.get(key);
  if (hit && hit.mtimeMs === mtimeMs) {
    // Refresh LRU position.
    payloadMem.delete(key);
    payloadMem.set(key, hit);
    return { ...hit.feed, meta };
  }
  const raw = readJsonFile<{ trades?: PolymarketTrade[]; positions?: PolymarketPosition[] }>(path);
  if (!raw) {
    payloadMem.delete(key);
    return null;
  }
  const feed: StoredFeed = {
    meta,
    trades: Array.isArray(raw.trades) ? raw.trades : [],
    positions: Array.isArray(raw.positions) ? raw.positions : [],
  };
  payloadMem.set(key, { mtimeMs, feed });
  while (payloadMem.size > MEM_LIMIT) {
    const oldest = payloadMem.keys().next().value;
    if (oldest === undefined) break;
    payloadMem.delete(oldest);
  }
  return feed;
}

export function writeFeed(input: StoredFeed): void {
  const addr = input.meta.address;
  // The incremental sync merges without a ceiling; enforce it here, on the
  // way to disk, so one hyperactive account can't grow an unbounded file.
  // Newest-first is what every producer hands us, and the replay windows are
  // all ≤ 30d, so the tail is what to drop.
  const feed: StoredFeed = input.trades.length > MAX_TRADES_PER_FEED
    ? { ...input, trades: input.trades.slice(0, MAX_TRADES_PER_FEED) }
    : input;
  const meta: FeedMeta = {
    ...feed.meta,
    tradeCount: feed.trades.length,
    positionCount: feed.positions.length,
  };
  writeAtomic(payloadPath(addr), JSON.stringify({ trades: feed.trades, positions: feed.positions }));
  // Meta LAST: it's what every other reader keys off, so it must never claim
  // a payload that isn't on disk yet.
  writeAtomic(metaPath(addr), JSON.stringify(meta));
  try {
    payloadMem.set(addr, { mtimeMs: statSync(payloadPath(addr)).mtimeMs, feed: { ...feed, meta } });
    metaMem.set(addr, { mtimeMs: statSync(metaPath(addr)).mtimeMs, meta });
  } catch {
    // The next read re-parses; nothing is lost.
  }
}

/** Record a failed sync without rewriting the (unchanged) payload. */
export function writeMeta(meta: FeedMeta): void {
  writeAtomic(metaPath(meta.address), JSON.stringify(meta));
  try {
    metaMem.set(meta.address, { mtimeMs: statSync(metaPath(meta.address)).mtimeMs, meta });
  } catch {
    // ditto
  }
}

export function listFeedAddresses(): string[] {
  try {
    return readdirSync(feedsDir())
      .filter((f) => f.endsWith(".meta.json"))
      .map((f) => f.slice(0, -".meta.json".length));
  } catch {
    return [];
  }
}

/** Delete feeds that left every roster. Anything a roster still names is
    synced every cycle, so "not synced in a week" IS "nobody watches this" —
    and it's readable from the meta file, without touching a payload. */
export function pruneFeeds(keep: Set<string>): number {
  const cutoff = Date.now() - FEED_IDLE_TTL_MS;
  let removed = 0;
  for (const addr of listFeedAddresses()) {
    if (keep.has(addr)) continue;
    const meta = readMeta(addr);
    if (meta && Math.max(meta.tradesAt, meta.attemptedAt) >= cutoff) continue;
    for (const p of [payloadPath(addr), metaPath(addr)]) {
      try {
        unlinkSync(p);
      } catch {
        // Already gone.
      }
    }
    payloadMem.delete(addr);
    metaMem.delete(addr);
    removed++;
  }
  return removed;
}

export interface FeedCoverage {
  /** Roster size. */
  traders: number;
  /** How many have a stored feed at all. */
  cached: number;
  /** Cached, but older than the freshness window. */
  stale: number;
  /** No stored feed yet — these are what a cold start is waiting on. */
  missing: number;
  /** Oldest `tradesAt` across the roster (ms epoch), 0 if none is cached. */
  oldestAt: number;
  /** Cached trade rows across the roster — the size of what a replay pass
      actually has to work with. */
  trades: number;
}

/** What the store holds for a roster — the number the console's status line
    reports, so "cached 20m ago" can't mean "cached, over nothing". Meta only:
    this runs on every fetch cycle and must stay cheap. */
export function coverage(addresses: string[], freshMs: number): FeedCoverage {
  const now = Date.now();
  const out: FeedCoverage = {
    traders: addresses.length,
    cached: 0,
    stale: 0,
    missing: 0,
    oldestAt: 0,
    trades: 0,
  };
  for (const addr of addresses) {
    const meta = readMeta(addr);
    if (!meta || meta.tradesAt === 0) {
      out.missing++;
      continue;
    }
    out.cached++;
    out.trades += meta.tradeCount;
    if (now - meta.tradesAt > freshMs) out.stale++;
    if (out.oldestAt === 0 || meta.tradesAt < out.oldestAt) out.oldestAt = meta.tradesAt;
  }
  return out;
}
