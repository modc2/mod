// SCORE STRATS — every score function IS a strat.
//
// A score function (the ƒ SCORE MARKET's listings: builtin shelf + community
// store) ranks the trader board. Its strat is the obvious one: copy the TOP N
// traders it ranks, as a TRADER INDEX (the console's default sizing — each
// trade scaled by your capital against theirs). Nothing about the strat is
// new machinery; it is `templateIndex(traderIndexTemplate(), topN)` with a
// `scoreFn` stamp that remembers which rule picked the roster, so the roster
// can be re-ranked later instead of rotting the way a hand-picked list does.
//
// WHERE THE RANKING RUNS: in the browser, through the exact compile path the
// board's score box uses (compileScore / Pyodide). Never on the server — a
// community score fn is someone else's code, and the Next server has fs
// access. The server only ever sees the RESULT (a plain address list), which
// is data.
//
// THE BACKTEST IS OUT-OF-SAMPLE BY CONSTRUCTION. Ranking on today's board and
// replaying the last N days scores traders on the very days they were picked
// for — "top by 7d ROI" always backtests beautifully, which is the
// HOLDOUT/walk-forward lesson this console already paid for. So the rule is
// replayed the honest way: rank on the ARCHIVED board scan from N days ago
// (scans.rs keeps ~a week+ of hourly boards), then replay the N days AFTER it.
// The picks knew nothing about the window they are graded on. The number is a
// property of the score function ("does picking by this rule pay?"), which is
// exactly what a strat whose roster is re-picked by that rule needs.
//
// Pure helpers first (unit-tested in __test_score_strats__.ts), IO after.

import { traderIndexTemplate, templateIndex } from "./defaultStrats";
import { fetchResolvedLegs } from "./hubCache";
import { backtestOne, type HubBacktest, type LegResolver, type TraderFeed } from "./hubReplay";
import { equalWeightTraders } from "./indexStore";
import {
  DEFAULT_ACTIVE_HOURS, WARMED_CANDIDATE_POOL, fetchScans, fetchTradersPage,
  type ScanMeta, type TopTrader,
} from "./polymarket";
import { compilePyScore } from "./pyScore";
import { compileScore, detectScoreLang, scoreInputs, type CompiledScore, type ScoreFn } from "./scoreFormula";
import type { ScoreFnListing } from "./scoreMarket";
import type { SavedIndex, ScoreStratMeta } from "./types";

/** Default bench size. TRADER INDEX seeds 8; a score rule's point is
    selectivity, so the default keeps only its strongest few. */
export const SCORE_STRAT_DEFAULT_TOP_N = 5;
export const SCORE_STRAT_MAX_TOP_N = 20;
/** Board window the rule ranks on — the TRADER INDEX seed window. */
export const SCORE_STRAT_BOARD_DAYS = 7;
/** Test windows offered. 7 is the edge: hourly scans are kept ~a week+. */
export const SCORE_STRAT_TEST_WINDOWS = [1, 3, 7] as const;
export const SCORE_STRAT_DEFAULT_TEST_DAYS = 3;
/** A scan further than this from the asked-for instant isn't "the board as of
    then" any more — say so instead of grading on the wrong day. */
const SCAN_TOLERANCE_SEC = 12 * 3600;

const ID_PREFIX = "scorefn-";

/** Stable strat id per listing — a re-rank UPDATES the strat instead of
    growing a duplicate. Community ids are namespaced so a community "best"
    can never collide with a builtin. */
export function scoreStratId(l: Pick<ScoreFnListing, "id" | "builtin">): string {
  return `${ID_PREFIX}${l.builtin ? "" : "c-"}${l.id}`;
}

export function isScoreStratId(id: string): boolean {
  return id.startsWith(ID_PREFIX);
}

/** Short content hash so the card can tell when a listing's source changed
    under a strat it already made (djb2 — identity, not security). */
export function sourceHash(src: string): string {
  let h = 5381;
  for (let i = 0; i < src.length; i++) h = ((h << 5) + h + src.charCodeAt(i)) | 0;
  return (h >>> 0).toString(36);
}

export interface Ranked {
  address: string;
  score: number;
}

/** Score every row, drop the ones the function filtered (null) or couldn't
    score (non-finite — an expression's sink), sort desc, keep N. Ties break by
    address so the same board always yields the same roster. */
export function rankByScore(rows: TopTrader[], fn: ScoreFn, n: number): Ranked[] {
  const out: Ranked[] = [];
  const seen = new Set<string>();
  for (const t of rows) {
    const addr = t.address?.toLowerCase();
    if (!addr || seen.has(addr)) continue;
    seen.add(addr);
    let s: number | null;
    try {
      s = fn(scoreInputs(t));
    } catch {
      s = null;
    }
    if (s === null || !Number.isFinite(s)) continue;
    out.push({ address: t.address, score: s });
  }
  out.sort((a, b) => b.score - a.score || a.address.localeCompare(b.address));
  return out.slice(0, Math.max(0, n));
}

/** Rows that were still trading at `refSec` — the copyable board. The same
    6h floor `fetchTopTraderAddresses` seeds every template with; applied here
    client-side so an ARCHIVED scan is judged against its own clock, not now. */
export function activeAt(rows: TopTrader[], refSec: number, hours = DEFAULT_ACTIVE_HOURS): TopTrader[] {
  const floor = refSec - hours * 3600;
  return rows.filter((t) => (t.lastTradeTs ?? 0) >= floor);
}

/** The archived board closest to (and not after) `targetSec` that actually
    holds the `days` window — skipped/failed windows carry no rows. Null when
    history doesn't reach back that far. */
export function pickScan(scans: ScanMeta[], targetSec: number, days = SCORE_STRAT_BOARD_DAYS): ScanMeta | null {
  const key = `${days}:0:${WARMED_CANDIDATE_POOL}`;
  let best: ScanMeta | null = null;
  for (const s of scans) {
    if (s.id > targetSec) continue;
    const w = s.windows.find((x) => x.key === key);
    if (!w || w.skipped || w.error || w.count <= 0) continue;
    if (!best || s.id > best.id) best = s;
  }
  if (!best || targetSec - best.id > SCAN_TOLERANCE_SEC) return null;
  return best;
}

/** The strat a listing's ranking produces. With `existing`, a RE-RANK: only
    the roster and the stamp move — the user's own edits (name, capital,
    gates, liveEnabled) survive, exactly like a template fork that got
    re-seeded. */
export function scoreStratIndex(
  listing: ScoreFnListing,
  ranked: Ranked[],
  topN: number,
  existing?: SavedIndex,
  now = Date.now(),
): SavedIndex {
  const meta: ScoreStratMeta = {
    fnId: listing.id,
    fnName: listing.name,
    builtin: listing.builtin,
    topN,
    sourceHash: sourceHash(listing.source),
    rankedAt: now,
    boardDays: SCORE_STRAT_BOARD_DAYS,
    scores: Object.fromEntries(ranked.map((r) => [r.address.toLowerCase(), Math.round(r.score * 1000) / 1000])),
    oos: existing?.scoreFn?.oos,
  };
  const traders = equalWeightTraders(ranked.map((r) => r.address));
  if (existing) {
    return { ...existing, traders, scoreFn: meta, updatedAt: now };
  }
  const base = templateIndex(traderIndexTemplate(), [], now);
  return {
    ...base,
    id: scoreStratId(listing),
    name: `TOP ${topN} · ${listing.name}`,
    forkedFrom: `scorefn:${listing.id}`,
    traders,
    liveEnabled: false,
    scoreFn: meta,
  };
}

/** Same roster (order-insensitive)? A re-rank that changes nothing must not
    bump updatedAt — that would re-key every backtest for no reason. */
export function sameRoster(a: SavedIndex["traders"], b: SavedIndex["traders"]): boolean {
  if (a.length !== b.length) return false;
  const s = new Set(a.map((t) => t.address.toLowerCase()));
  return b.every((t) => s.has(t.address.toLowerCase()));
}

// ── IO ──────────────────────────────────────────────────────────────────────

const PAGE_SIZE = 100; // the API's clamp (routes.rs)
const PAGE_CONCURRENCY = 4;
const POOL_TTL_MS = 30 * 60_000;
const poolCache = new Map<string, { at: number; rows: Promise<TopTrader[]> }>();

/** The WHOLE board for one window — every page, because a score function can
    rank anyone (a rule that loves small steady wallets would never see them in
    a PnL-sorted top 500). Memoized per scan for half an hour: every listing
    ranks off the same pool. */
export function fetchBoardPool(
  opts: { scanId?: number; days?: number } = {},
  onProgress?: (done: number, total: number) => void,
): Promise<TopTrader[]> {
  const days = opts.days ?? SCORE_STRAT_BOARD_DAYS;
  const key = `${opts.scanId ?? "live"}:${days}`;
  const hit = poolCache.get(key);
  if (hit && Date.now() - hit.at < POOL_TTL_MS) return hit.rows;
  const rows = (async () => {
    const base = {
      days, pool: WARMED_CANDIDATE_POOL, sort: "pnl", order: "desc", pageSize: PAGE_SIZE,
      scanId: opts.scanId,
      // Live: let the server drop the inactive (fewer pages). A scan is judged
      // against its own clock instead — see activeAt.
      maxLastTradeHrs: opts.scanId == null ? DEFAULT_ACTIVE_HOURS : undefined,
    };
    const first = await fetchTradersPage({ ...base, page: 0 });
    if (first.cold) throw new Error("the leaderboard cache is cold — the hourly sync hasn't warmed it yet");
    const pages = Math.ceil((first.total ?? first.traders.length) / PAGE_SIZE);
    const all: TopTrader[] = [...first.traders];
    let done = 1;
    onProgress?.(done, pages);
    for (let p = 1; p < pages; p += PAGE_CONCURRENCY) {
      const batch = Array.from({ length: Math.min(PAGE_CONCURRENCY, pages - p) }, (_, i) => p + i);
      const res = await Promise.all(batch.map((page) => fetchTradersPage({ ...base, page }).catch(() => null)));
      for (const r of res) if (r) all.push(...r.traders);
      done += batch.length;
      onProgress?.(done, pages);
    }
    return all;
  })();
  poolCache.set(key, { at: Date.now(), rows });
  rows.catch(() => poolCache.delete(key));
  return rows;
}

const compiled = new Map<string, Promise<CompiledScore>>();

/** Compile a listing's source once per session — JS/expr inline, Python via
    the board's own Pyodide runtime (lazy: nothing loads unless a listing is
    Python). */
export function compileListing(source: string): Promise<CompiledScore> {
  const hit = compiled.get(source);
  if (hit) return hit;
  const p = detectScoreLang(source) === "py"
    ? compilePyScore(source).catch((e) => ({ fn: null, error: String(e) }))
    : Promise.resolve(compileScore(source));
  compiled.set(source, p);
  return p;
}

/** One listing → ranked top N on a given pool. Throws with the compile error
    so the card can print it instead of an empty roster. */
export async function rankListing(listing: ScoreFnListing, pool: TopTrader[], topN: number): Promise<Ranked[]> {
  const c = await compileListing(listing.source);
  if (!c.fn) throw new Error(c.error || "score function did not compile");
  return rankByScore(pool, c.fn, topN);
}

export interface OosResult {
  /** The scan the picks were made on (unix secs) — the board as of then. */
  scanId: number;
  testDays: number;
  picks: string[];
  bt: HubBacktest | null;
  at: number;
}

let scansCache: { at: number; scans: Promise<ScanMeta[]> } | null = null;
function scanList(): Promise<ScanMeta[]> {
  if (scansCache && Date.now() - scansCache.at < POOL_TTL_MS) return scansCache.scans;
  const scans = fetchScans(400).then((r) => r.scans);
  scansCache = { at: Date.now(), scans };
  scans.catch(() => { scansCache = null; });
  return scans;
}

/** The board as it stood `testDays` ago, already narrowed to who was trading
    then. Shared by every listing's out-of-sample run. */
export async function pastBoard(
  testDays: number,
  onProgress?: (done: number, total: number) => void,
): Promise<{ scan: ScanMeta; rows: TopTrader[] }> {
  const target = Math.floor(Date.now() / 1000) - testDays * 86400;
  const scan = pickScan(await scanList(), target);
  if (!scan) throw new Error(`no archived board from ${testDays}d ago — scan history doesn't reach back that far yet`);
  const rows = activeAt(await fetchBoardPool({ scanId: scan.id }, onProgress), scan.id);
  return { scan, rows };
}

/** Grade a score RULE out of sample: pick top N on the board from `testDays`
    ago, replay the `testDays` since with the strat's own params. `feeds` is
    shared across listings — overlapping picks fetch once. */
export async function oosBacktest(
  listing: ScoreFnListing,
  strat: SavedIndex,
  past: { scan: ScanMeta; rows: TopTrader[] },
  testDays: number,
  feeds: Map<string, Promise<TraderFeed>>,
  // What the markets actually PAID OUT. Without it every position still open
  // at the window's end is valued at the last price a leader printed — a
  // 3-day replay of five busy wallets measured +245% that way, $2,380 of its
  // $2,456 "profit" marked rather than settled.
  resolve: LegResolver = fetchResolvedLegs,
): Promise<OosResult> {
  const ranked = await rankListing(listing, past.rows, strat.scoreFn?.topN ?? SCORE_STRAT_DEFAULT_TOP_N);
  const picks = ranked.map((r) => r.address);
  const replayed: SavedIndex = { ...strat, traders: equalWeightTraders(picks) };
  // forward/holdout off: the picks already know nothing about the window —
  // that IS the holdout — and each extra replay is CPU the user waits for.
  const bt = await backtestOne(replayed, testDays, feeds, undefined, resolve, { forward: false, holdout: false });
  return { scanId: past.scan.id, testDays, picks, bt, at: Date.now() };
}
