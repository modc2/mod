// The background backtest worker — two loops, not one.
//
//   FETCH   (every ~10 min)  tops up the on-disk trader feed store for the
//                            roster the console published: stalest traders
//                            first, a bounded number per cycle, one /activity
//                            page each (incremental), paced so the live copy
//                            engine keeps its share of data-api's budget.
//
//   REPLAY  (every ~30 min)  replays every published strat and every
//                            recommended template OVER THAT STORE. Zero
//                            network requests in the steady state, so the
//                            cadence is bounded by CPU rather than by
//                            Cloudflare, and results land in
//                            ~/.mod/polymarket/hub/backtests.json.
//
// It used to be one loop that fetched and replayed together, every 2 hours.
// That was the rate-limit problem: the Rust proxy holds /activity for an hour
// (api/src/cache.rs) and the app's own trade cache is localStorage — a no-op
// in Node — so a 2-hourly pass missed BOTH caches by construction and walked
// a paginated 30-day feed for every trader of every strat, every time. Split
// apart, fetching happens because a feed is stale (and only for what's
// actually stale), and backtesting happens because time passed.
//
// The replay is still the app's own TypeScript — lib/hubReplay.ts →
// lib/backtest.ts → lib/strats/strat.ts, the exact code the console and the
// BACKTEST tab run — which is why this lives inside the Next server instead of
// being its own service. A second engine is how backtest and live drifted
// apart the last time (see lib/strats/parity.fixture.json).

import { mkdirSync, readFileSync } from "fs";
import { join } from "path";

import { API_BASE, serverAuthHeaders, setServerAuthToken } from "../polymarket";
import { DEFAULT_STRATS, templateRoster } from "../defaultStrats";
import { WORKER_TAPE_BUDGET } from "../momentumTape";
import {
  HUB_BACKTEST_DAYS, backtestOne, backtestTemplate, templateBacktestKey,
  type HubBacktest, type TraderFeed,
} from "../hubReplay";
import type { SavedIndex } from "../types";
import { coverage, pruneFeeds, writeAtomic, type FeedCoverage } from "./feedStore";
import { TRADES_TTL_MS, feedSession, refreshRoster } from "./feedFetcher";
import { mintOwnerToken, stateDir } from "./ownerToken";
import { resolutionCoverage, resolutionsFor } from "./resolutionStore";

function envMinutes(name: string, fallbackMs: number): number {
  const raw = Number(process.env[name]);
  return Number.isFinite(raw) && raw > 0 ? raw * 60_000 : fallbackMs;
}

/** How often every strat is replayed out of the store. */
const INTERVAL_MS = envMinutes("POLYMARKET_HUB_BACKTEST_MINUTES", 30 * 60_000);
/** How often the store itself is topped up. */
const REFRESH_MS = envMinutes("POLYMARKET_HUB_REFRESH_MINUTES", 10 * 60_000);
/** Delay before the first fetch cycle — let the API finish booting (and its
    own trader sync warm the Rust caches) before we ask it for anything. */
const BOOT_DELAY_MS = 45_000;
/** The first replay waits for the first fetch cycle to have put something in
    the store, so a cold start doesn't publish a wall of empty cards. */
const REPLAY_BOOT_DELAY_MS = BOOT_DELAY_MS + 90_000;
/** Market resolutions looked up per replay pass (see resolutionStore.ts).
    ~30 gamma requests, shared across every strat in the pass — small next to
    the fetch loop, and resolutions are cached forever once learned. */
const RESOLUTION_BUDGET_PER_PASS = Number(process.env.POLYMARKET_HUB_RESOLUTION_BUDGET) || 600;
/** Replay the window BEFORE each card's window too, so every card carries a
    walk-forward verdict (see `ForwardCheck` in hubReplay.ts). Doubles the
    pass's CPU and spends no extra upstream requests — both windows read the
    same cached feed. `POLYMARKET_HUB_FORWARD=0` turns it off. */
const FORWARD_CHECK = process.env.POLYMARKET_HUB_FORWARD !== "0";
/** Template rosters come from the leaderboard, which barely moves within a
    few hours — and `templateRoster`'s own cache is localStorage, so without
    this every replay pass would re-query it for every template. */
const ROSTER_TTL_MS = 3 * 3600_000;

export interface HubManifest {
  /** Window to replay, in days. The console's CURRENT window; kept for caches
      written before `windows`, and folded into it. */
  days: number;
  /** Every window to replay this pass. See `manifestWindows`. */
  windows?: number[];
  /** The strats to replay — published by the console. */
  strats: SavedIndex[];
  at: number;
}

/** The COPY DESK's leaders, as strats to replay.
 *
 * The manifest is *published by a browser* — it holds what someone's console
 * had in localStorage the last time they opened it. The copy book isn't like
 * that: it lives on the server (api/src/copy.rs), and an agent can add a
 * leader to it over MCP with no browser involved at all. So the worker asks
 * the API for it directly rather than waiting to be told.
 *
 * The strat it gets back is the identity template — literally the same object
 * the live engine runs — so "how would copying this trader have gone" is
 * replayed by the same code, over the same windows, as every other card.
 *
 * Failure is silent and empty: a desk with no leaders and an API that is
 * momentarily down look the same from here, and neither is a reason to stop
 * replaying the manifest's own strats. */
async function copyDeskStrats(): Promise<SavedIndex[]> {
  try {
    const res = await fetch(`${API_BASE}/copy/strats`, {
      headers: serverAuthHeaders(),
      signal: AbortSignal.timeout(10_000),
    });
    if (!res.ok) return [];
    const body = (await res.json()) as { strats?: SavedIndex[] };
    return Array.isArray(body.strats) ? body.strats : [];
  } catch {
    return [];
  }
}

/** Everything this pass replays: the copy book's leaders FIRST (the desk this
    deployment leads with), then the console's own published strats, minus any
    id already covered. Ids collide only when the same allocation was also
    published as a saved strat — one card, not two. */
function mergeStrats(deskStrats: SavedIndex[], manifest: HubManifest): SavedIndex[] {
  const seen = new Set(deskStrats.map((s) => s.id));
  return [...deskStrats, ...manifest.strats.filter((s) => !seen.has(s.id))];
}

/** Windows a single pass will replay, at most. Each one costs a full replay of
    every strat, and the console picks the list — so it's bounded here. A window
    that doesn't fit isn't lost: the hub replays anything the worker doesn't
    cover in the browser (lib/hubBacktest.ts), which is what it did for all of
    them before this worker existed. */
const MAX_WINDOWS = 3;

/** The windows one pass replays — ALWAYS including HUB_BACKTEST_DAYS.
 *
 * The manifest's `days` is whichever window the console happened to be LOOKING
 * at when it last published. That made the worker's coverage a side effect of
 * where someone left a browser tab: leave the hub on 3D and it stops producing
 * 1-day numbers entirely, which is exactly how every owned strat ended up
 * either with a 1D card most of a day old or with none at all. So the default
 * window is not negotiable — the pass always replays it, and the console's
 * window is an EXTRA on top. */
function manifestWindows(m: HubManifest): number[] {
  const asked = [...(m.windows ?? []), m.days]
    .map(Number)
    .filter((d) => Number.isFinite(d) && d > 0);
  // Ascending, so the mandatory 1-day window runs FIRST: it gets first claim on
  // the pass's resolution budget, and a pass that dies halfway still leaves the
  // window every card defaults to.
  return [...new Set([HUB_BACKTEST_DAYS, ...asked])].sort((a, b) => a - b).slice(0, MAX_WINDOWS);
}

/** What the fetch loop did, for the console's status line. */
export interface FeedStatus {
  /** When the last fetch cycle finished (ms epoch), 0 if never. */
  at: number;
  nextAt: number;
  running: boolean;
  /** Traders in the roster, and how many the store actually holds. */
  traders: number;
  cached: number;
  stale: number;
  missing: number;
  /** Synced in the last cycle, of which cold (full 30-day backfills). */
  synced: number;
  cold: number;
  /** Due but postponed to the next cycle (budget). */
  deferred: number;
  errors: number;
  error?: string;
}

export interface HubCacheFile {
  status: {
    at: number;
    nextAt: number;
    /** The pass's primary window — HUB_BACKTEST_DAYS. Kept as a scalar for
        readers written before a pass covered more than one window. */
    days: number;
    /** Every window the last pass replayed. Anything not in here is the
        browser's own job to replay. */
    windows?: number[];
    strats: number;
    running: boolean;
    /** Market-resolution coverage after the last pass: how many markets the
        store can price as fact, how many are still open/unknown, and how many
        the pass learned. Absent on caches written before resolutions. */
    resolutions?: { resolved: number; unknown: number; learned: number };
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
const rosterPath = () => join(hubDir(), "rosters.json");
// The fetch loop's status lives in its OWN file. The two loops run
// independently, and a replay pass rewrites backtests.json after every strat
// — parking the fetch status in there would let a long pass stamp back a
// snapshot of the schedule taken before the fetch cycle it overlapped.
const feedStatusPath = () => join(hubDir(), "feeds.json");

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

// tmp + rename, via feedStore's writer: `/api/hub` reads these files from the
// same process on another tick, and a plain writeFileSync let a reader catch
// a truncated backtests.json mid-write and fall back to an empty cache.
export function writeManifest(m: HubManifest): void {
  writeAtomic(manifestPath(), JSON.stringify(m));
}

export function readCache(): HubCacheFile {
  return readJson<HubCacheFile>(cachePath(), {
    status: { at: 0, nextAt: 0, days: HUB_BACKTEST_DAYS, strats: 0, running: false },
    results: {},
  });
}

function writeCache(c: HubCacheFile): void {
  writeAtomic(cachePath(), JSON.stringify(c));
}

const EMPTY_FEED_STATUS: FeedStatus = {
  at: 0, nextAt: 0, running: false, traders: 0, cached: 0, stale: 0,
  missing: 0, synced: 0, cold: 0, deferred: 0, errors: 0,
};

export function readFeedStatus(): FeedStatus {
  return { ...readJson<FeedStatus>(feedStatusPath(), EMPTY_FEED_STATUS), running: refreshing };
}

function writeFeedStatus(s: FeedStatus): void {
  writeAtomic(feedStatusPath(), JSON.stringify(s));
}

// ── Template rosters (disk-cached) ──────────────────────────────

type RosterFile = Record<string, { addrs: string[]; at: number }>;

/** Resolve every template's roster, re-querying the leaderboard only for the
    ones whose cached roster has aged out. A failed query keeps the previous
    roster rather than emptying the template's card. */
async function resolveRosters(): Promise<Map<string, string[]>> {
  const file = readJson<RosterFile>(rosterPath(), {});
  const out = new Map<string, string[]>();
  let dirty = false;
  for (const t of DEFAULT_STRATS) {
    const hit = file[t.slug];
    if (hit && Date.now() - hit.at < ROSTER_TTL_MS && hit.addrs.length > 0) {
      out.set(t.slug, hit.addrs);
      continue;
    }
    let addrs: string[] = [];
    try {
      addrs = await templateRoster(t);
    } catch {
      addrs = [];
    }
    if (addrs.length > 0) {
      file[t.slug] = { addrs, at: Date.now() };
      dirty = true;
      out.set(t.slug, addrs);
    } else if (hit) {
      out.set(t.slug, hit.addrs);
    } else {
      out.set(t.slug, []);
    }
  }
  if (dirty) writeAtomic(rosterPath(), JSON.stringify(file));
  return out;
}

/** Every address the store needs to keep warm: the union of the published
    strats' enabled traders and the recommended templates' rosters. */
function rosterAddresses(strats: SavedIndex[], rosters: Map<string, string[]>): string[] {
  const out = new Set<string>();
  for (const idx of strats) {
    for (const t of idx.traders) {
      if (t.enabled !== false) out.add(t.address.toLowerCase());
    }
  }
  for (const addrs of rosters.values()) {
    for (const a of addrs) out.add(a.toLowerCase());
  }
  return [...out];
}

function enabledWatchlist(idx: SavedIndex): string[] {
  return idx.traders.filter((t) => t.enabled !== false).map((t) => t.address.toLowerCase());
}

/** Forget cards for strats the console no longer publishes.
 *
 * Results are keyed `<strat id | tpl:slug>@<days>d` and nothing ever removed
 * them, so a deleted strat kept answering the hub — and `pm_backtests` kept
 * listing it — with whatever numbers it had on the day it was deleted. Only
 * the strat half of the key is checked: a window this pass isn't running is
 * still a legitimate cached result for a strat we DO own, and the console
 * shows it (with its own age) when you flip windows. */
function pruneResults(cache: HubCacheFile, manifest: HubManifest, strats: SavedIndex[]): number {
  // A manifest that was never published (or was wiped) is not evidence that
  // the user owns nothing — it's evidence we don't know yet. Don't prune on it.
  // The copy book alone is enough evidence, though: it's server-side, so an
  // empty manifest with a populated desk is a complete picture.
  if (!manifest.at && strats.length === 0) return 0;
  if (strats.length === 0) return 0;
  const own = new Set<string>([
    ...strats.map((s) => s.id),
    ...DEFAULT_STRATS.map((t) => templateBacktestKey(t.slug)),
  ]);
  let dropped = 0;
  for (const key of Object.keys(cache.results)) {
    if (own.has(key.replace(/@[\d.]+d$/, ""))) continue;
    delete cache.results[key];
    dropped++;
  }
  return dropped;
}

// ── The two loops ───────────────────────────────────────────────

let running = false;
let refreshing = false;
let timer: ReturnType<typeof setTimeout> | null = null;
let refreshTimer: ReturnType<typeof setTimeout> | null = null;

/** Authenticate as the owner — every data route is behind the access gate and
    there's no browser here to sign in. Returns false when the deployment has
    no owner/secret, in which case both loops stand down. */
function authenticate(cache: HubCacheFile): boolean {
  const token = mintOwnerToken();
  if (!token) {
    cache.status = {
      ...cache.status,
      error: "no owner/secret — access gate unconfigured",
      running: false,
    };
    writeCache(cache);
    return false;
  }
  setServerAuthToken(token);
  return true;
}

/** ONE fetch cycle: top up the stale end of the roster's feeds. This is the
    only thing in the hub that talks to data-api. */
export async function runRefresh(): Promise<FeedStatus | null> {
  if (refreshing) return readFeedStatus();
  if (!authenticate(readCache())) return null;

  refreshing = true;
  const manifest = readManifest();
  writeFeedStatus({ ...readFeedStatus(), running: true, nextAt: Date.now() + REFRESH_MS });

  let error: string | undefined;
  let stats: Awaited<ReturnType<typeof refreshRoster>> | null = null;
  let roster: string[] = [];
  try {
    const rosters = await resolveRosters();
    // The desk's leaders need their feeds kept warm just like a strat's
    // watchlist — a copied trader with no cached history replays as a trader
    // who did nothing.
    roster = rosterAddresses(mergeStrats(await copyDeskStrats(), manifest), rosters);
    stats = await refreshRoster(roster);
    // A trader nobody watches any more shouldn't keep a 30-day feed on disk.
    pruneFeeds(new Set(roster));
  } catch (e) {
    error = e instanceof Error ? e.message : String(e);
  } finally {
    refreshing = false;
  }

  const cov: FeedCoverage = stats?.coverage ?? coverage(roster, TRADES_TTL_MS);
  const now = Date.now();
  const feeds: FeedStatus = {
    at: now,
    nextAt: now + REFRESH_MS,
    running: false,
    traders: cov.traders,
    cached: cov.cached,
    stale: cov.stale,
    missing: cov.missing,
    synced: stats?.synced ?? 0,
    cold: stats?.cold ?? 0,
    deferred: stats?.deferred ?? 0,
    errors: stats?.errors ?? 0,
    ...(error ? { error } : {}),
  };
  writeFeedStatus(feeds);
  return feeds;
}

/** Replay every published strat + every recommended template ONCE, out of the
    feed store. Results are written as they land, so a long pass still leaves
    the console something to read — and a crash mid-pass costs only the strats
    it hadn't reached. */
export async function runPass(): Promise<HubCacheFile> {
  const cache = readCache();
  if (running) return cache;
  if (!authenticate(cache)) return cache;

  running = true;
  const manifest = readManifest();
  // The copy book is read once per pass, before anything is replayed: a leader
  // added over MCP thirty seconds ago gets a card on this pass, not the next.
  const strats = mergeStrats(await copyDeskStrats(), manifest);
  const windows = manifestWindows(manifest);
  cache.status = {
    at: cache.status.at, nextAt: Date.now() + INTERVAL_MS,
    days: windows[0], windows,
    strats: strats.length, running: true,
  };
  pruneResults(cache, manifest, strats);
  writeCache(cache);

  // One feed map for the whole pass: strats overlap heavily on traders — and
  // now windows do too, since a 1-day and a 3-day replay read the same 30-day
  // feed. Loading an address twice is pure waste.
  const feeds = new Map<string, Promise<TraderFeed>>();
  const session = feedSession();
  const publish = (key: string, bt: HubBacktest, watchlist: string[], days: number) => {
    // A trader the store had nothing for was replayed as having done nothing.
    // Say how many, so a half-warmed card can't be read as a flat one.
    const warming = watchlist.filter((a) => session.pending.has(a)).length;
    const slot = `${key}@${days}d`;
    const prev = cache.results[slot];
    // Never trade a COMPLETE result for an incomplete one. On a cold start
    // (fresh deploy, wiped feeds) the first pass can run before the fetch
    // loop has covered the whole roster; overwriting yesterday's real numbers
    // with "0 trades, warming" would make a working hub look broken for an
    // hour. The old result keeps its own timestamp, which says how old it is.
    if (warming > 0 && prev && !prev.warming && Date.now() - prev.at < 6 * 3600_000) return;
    cache.results[slot] = {
      ...bt,
      by: "worker",
      ...(warming > 0
        ? {
            warming,
            note: warming === watchlist.length
              ? "warming cache — no trader history yet"
              : `partial — ${warming}/${watchlist.length} traders still warming`,
          }
        : {}),
    };
    writeCache(cache);
  };

  // How the replays learn what their markets paid out. The budget is per PASS,
  // not per strat — the strats overlap heavily, and the store answers every id
  // it already knows for free, so a pass spends its lookups on markets nobody
  // has priced yet and converges over a few cycles.
  let learned = 0;
  let budget = RESOLUTION_BUDGET_PER_PASS;
  const resolve = async (conditionIds: string[]) => {
    const { resolved, stats } = await resolutionsFor(conditionIds, { budget });
    budget -= stats.fetched;
    learned += stats.learned;
    return resolved;
  };

  let error: string | undefined;
  try {
    const rosters = await resolveRosters();
    // Window-outer, strat-inner: every strat gets its 1-day number before any
    // strat gets its 3-day one. The alternative finishes strat #1 across all
    // windows while strat #8 has nothing, which is the failure this whole
    // change is about — "each strat, over the last day" is the invariant, and
    // it should hold at every instant of the pass, not only at the end.
    for (const days of windows) {
      for (const idx of strats) {
        const bt = await backtestOne(idx, days, feeds, session.load, resolve, {
          forward: FORWARD_CHECK,
          // Origination strats replay off a price tape, and the worker is the
          // right place to pay for a deep one: nobody is waiting on it and the
          // Rust proxy caches prices-history on disk for a day.
          tapeBudget: WORKER_TAPE_BUDGET,
        });
        if (bt) publish(idx.id, bt, enabledWatchlist(idx), days);
      }
      for (const t of DEFAULT_STRATS) {
        const roster = rosters.get(t.slug) ?? [];
        const bt = await backtestTemplate(t, days, feeds, {
          loader: session.load, roster, resolve, forward: FORWARD_CHECK,
          tapeBudget: WORKER_TAPE_BUDGET,
        });
        if (bt) publish(templateBacktestKey(t.slug), bt, roster, days);
      }
    }
  } catch (e) {
    error = e instanceof Error ? e.message : String(e);
  } finally {
    running = false;
  }

  const now = Date.now();
  cache.status = {
    at: now, nextAt: now + INTERVAL_MS, days: windows[0], windows,
    strats: strats.length, running: false,
    resolutions: { ...resolutionCoverage(), learned },
    ...(error ? { error } : {}),
  };
  writeCache(cache);

  // Anything the pass wanted and didn't have is the fetch loop's next job.
  if (session.pending.size > 0 && !refreshing) {
    void refreshRoster([...session.pending], { maxAgeMs: 0 }).catch(() => {});
  }
  return cache;
}

/** Queue a pass without waiting for it (the route's `?run=1`). */
export function triggerPass(): boolean {
  if (running) return false;
  setTimeout(() => { void runPass(); }, 0);
  return true;
}

/** Queue a fetch cycle without waiting for it (the route's `?refresh=1`). */
export function triggerRefresh(): boolean {
  if (refreshing) return false;
  setTimeout(() => { void runRefresh(); }, 0);
  return true;
}

export function workerRunning(): boolean {
  return running;
}

export function refreshRunning(): boolean {
  return refreshing;
}

/** Start both loops. Idempotent: Next may call `register()` more than once in
    a process (dev HMR), and a second timer would double every fetch. */
export function startHubWorker(): void {
  if (timer || refreshTimer) return;

  const loop = (
    set: (t: ReturnType<typeof setTimeout>) => void,
    run: () => Promise<unknown>,
    every: number,
  ) => {
    const schedule = (delay: number) => {
      const t = setTimeout(async () => {
        try {
          await run();
        } catch {
          // A failed cycle is recorded in the cache status; the loop continues.
        }
        schedule(every);
      }, delay);
      // Never hold the process open for a backtest.
      if (typeof t.unref === "function") t.unref();
      set(t);
    };
    return schedule;
  };

  // A restart shouldn't redo work that's still fresh — a deploy loop would
  // otherwise re-fetch and re-backtest the whole hub every time the app
  // bounced.
  const passAge = Date.now() - readCache().status.at;
  const feedAge = Date.now() - readFeedStatus().at;

  loop((t) => { refreshTimer = t; }, runRefresh, REFRESH_MS)(
    feedAge >= REFRESH_MS ? BOOT_DELAY_MS : Math.max(BOOT_DELAY_MS, REFRESH_MS - feedAge),
  );
  loop((t) => { timer = t; }, runPass, INTERVAL_MS)(
    passAge >= INTERVAL_MS ? REPLAY_BOOT_DELAY_MS : Math.max(REPLAY_BOOT_DELAY_MS, INTERVAL_MS - passAge),
  );
}
