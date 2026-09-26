// The historical price tape an ORIGINATION backtest runs on.
//
// A momentum strat trades a market's own odds, so replaying one means having
// the odds as they stood, minute by minute, over a window that already closed.
// The live engine builds the same shape (`MarketPriceSeries[]`) for the last
// few hours — `assembleMarketPrices` / `fetch_candle_series` — off endpoints
// that only ever answer "now". This module builds it for "then".
//
// Two modes, mirroring the two the engine has:
//
//   CANDLES (`momentum.candles`) — a recurring sub-hour series names its
//     markets deterministically: "btc-updown-5m-<candle start unix>". So the
//     markets a past window contained are ENUMERABLE, no search involved: walk
//     the candle starts, batch them 20 per gamma request, then pull each
//     candle's 1-minute price history for exactly its own lifetime. A closed
//     candle also carries its OUTCOME (gamma's outcomePrices), so the replay
//     settles on fact rather than on a last mark.
//
//   QUERY (everything else) — the engine searches gamma for the top-volume
//     markets matching the strat's query and reads 5-minute history. The same
//     search here is honest only about markets that still exist: one that
//     closed before today won't come back from a search run today, so a query
//     tape is SURVIVORSHIP-BIASED and says so in its note. Candle mode has no
//     such problem, which is the other reason the flagship strat uses it.
//
// Budget matters: one day of 5-minute candles is 288 markets, i.e. 288 price
// requests. `budget` caps how many the tape covers, newest-first — a card that
// replays the last 8 hours of a 1-day window must say "8h of 24h", never print
// the number as if it covered the day. That's what `markets`/`expected` carry.

import type { MomentumParams, PolymarketMarket } from "./types";
import type { MarketPriceSeries } from "./strats/strat";
import { candleSlug, MAX_MOMENTUM_QUERIES } from "./strats/strat";
import { marketQueryGroups } from "./marketQuery";
import { emptyTape, type PriceTape } from "./originationBacktest";
import { fetchMarketsBySlugs, fetchPriceWindow, searchMarkets } from "./polymarket";
import { legKey } from "./leg";

/** Markets a browser-side replay will fetch history for. Each is one CLOB
    request; the console runs these while the user waits. */
export const BROWSER_TAPE_BUDGET = 120;
/** The background worker has no one waiting and hits a proxy that caches
    prices-history on disk for 24h — it can cover a full day of 5m candles. */
export const WORKER_TAPE_BUDGET = 320;

/** Concurrent price-history requests. The same restraint feedFetcher shows:
    this shares the upstream budget with a live engine whose fills are
    time-critical. */
const CONCURRENCY = 4;

export interface TapeOpts {
  fromMs: number;
  toMs: number;
  /** Max markets to pull history for (newest-first). */
  budget?: number;
  /** Free-text market topic the strat carries, used when the momentum params
      don't name their own query — same precedence the engine uses. */
  marketQuery?: string;
}

/** Memo so one hub pass doesn't fetch the same candle window once per strat
    (both BTC strats share a prefix) or once per walk-forward half. */
const memo = new Map<string, { at: number; tape: Promise<PriceTape> }>();
const MEMO_TTL_MS = 10 * 60_000;

function memoKey(mo: MomentumParams, o: TapeOpts): string {
  return JSON.stringify([
    mo.candles?.slugPrefix ?? null, mo.candles?.periodMinutes ?? null,
    mo.query ?? o.marketQuery ?? "", mo.maxMarkets ?? null,
    Math.round(o.fromMs / 60_000), Math.round(o.toMs / 60_000), o.budget ?? 0,
  ]);
}

/** Build the tape for one momentum strat over `[fromMs, toMs]`. Never throws:
    a failed fetch returns an empty tape carrying the reason, because a card
    that says "no data" is right and a card that says "$0" is a lie. */
export function fetchPriceTape(mo: MomentumParams, opts: TapeOpts): Promise<PriceTape> {
  const key = memoKey(mo, opts);
  const hit = memo.get(key);
  if (hit && Date.now() - hit.at < MEMO_TTL_MS) return hit.tape;
  const tape = (mo.candles ? candleTape(mo, opts) : queryTape(mo, opts))
    .catch((e): PriceTape => ({
      ...emptyTape(mo.candles ? "candles" : "query"),
      fromMs: opts.fromMs,
      toMs: opts.toMs,
      note: `price tape unavailable: ${e instanceof Error ? e.message : String(e)}`,
    }));
  memo.set(key, { at: Date.now(), tape });
  return tape;
}

/** The tape a saved strat needs for a `[asOf − days, asOf]` replay, or
 *  undefined when the strat originates nothing (every copy strat) — in which
 *  case `runBacktest` skips the origination pass entirely and costs nothing. */
export function tapeFor(
  momentum: MomentumParams | undefined,
  marketQuery: string | undefined,
  days: number,
  asOf: number,
  budget?: number,
): Promise<PriceTape | undefined> {
  if (!momentum) return Promise.resolve(undefined);
  const toMs = asOf > 0 ? asOf : Date.now();
  return fetchPriceTape(momentum, {
    fromMs: toMs - days * 86400_000,
    toMs,
    budget,
    marketQuery,
  });
}

/** Run `work` over `items` at a bounded concurrency, dropping failures. */
async function pooled<T, R>(items: T[], work: (item: T) => Promise<R | null>): Promise<R[]> {
  const out: R[] = [];
  let cursor = 0;
  const workers = Array.from({ length: Math.min(CONCURRENCY, items.length) }, async () => {
    for (;;) {
      const i = cursor++;
      if (i >= items.length) return;
      try {
        const r = await work(items[i]);
        if (r) out.push(r);
      } catch {
        // one market's history missing is not fatal — momentum works off
        // whatever slice of the feed resolved, live and here.
      }
    }
  });
  await Promise.all(workers);
  return out;
}

/** Did this market settle, and to what? gamma serves `outcomePrices` as live
    prices while a market trades and as the PAYOUT once it resolves, with no
    flag in the normalized shape to tell them apart — so require both that the
    market's end has passed and that the prices are the exact 0/1 a settlement
    pays. Anything else stays unknown, which the sim reports as a guess. */
function resolutionOf(
  conditionId: string, outcomes: string[], prices: number[], endMs: number, nowMs: number,
): Array<[string, number]> {
  if (!(endMs > 0 && endMs <= nowMs)) return [];
  if (outcomes.length !== prices.length || prices.length < 2) return [];
  const sum = prices.reduce((s, p) => s + p, 0);
  if (Math.abs(sum - 1) > 0.01) return [];
  if (!prices.every((p) => p < 0.001 || p > 0.999)) return [];
  return outcomes.map((o, i) => [legKey(conditionId, o), Math.round(prices[i])]);
}

async function candleTape(mo: MomentumParams, opts: TapeOpts): Promise<PriceTape> {
  const prefix = mo.candles?.slugPrefix ?? "btc-updown-5m";
  const periodMin = Math.max(1, Math.round(mo.candles?.periodMinutes ?? 5));
  const periodMs = periodMin * 60_000;
  const budget = opts.budget ?? BROWSER_TAPE_BUDGET;
  const now = Date.now();

  // Every candle that OPENED inside the window. The one live at `fromMs` is
  // included: a strat standing at the window start sees it mid-flight, exactly
  // as the engine would.
  const firstStart = Math.floor(opts.fromMs / periodMs) * periodMs;
  const starts: number[] = [];
  for (let s = firstStart; s < opts.toMs; s += periodMs) starts.push(s);
  const expected = starts.length;
  // Newest-first: a partial tape should cover the END of the window, which is
  // the half a walk-forward's "next" leg and the card's headline both care
  // about, and the half whose markets are still cached upstream.
  const covered = starts.slice(Math.max(0, starts.length - budget));

  const slugs = covered.map((s) => candleSlug(prefix, periodMin, s));
  const markets = await fetchMarketsBySlugs(slugs);

  const resolved = new Map<string, number>();
  const series = await pooled(markets, async (m): Promise<MarketPriceSeries | null> => {
    const tokens = m.clobTokenIds ?? [];
    if (tokens.length < 2 || m.outcomes.length < 2 || !m.conditionId) return null;
    const endMs = Date.parse(m.endDate);
    if (!Number.isFinite(endMs)) return null;
    const startMs = endMs - periodMs;
    // A minute of pre-roll: `seriesMomentum` needs a point at/before the
    // lookback cutoff or it refuses to price the move at all.
    const points = await fetchPriceWindow(
      tokens[0],
      Math.floor((startMs - 120_000) / 1000),
      Math.ceil((endMs + 60_000) / 1000),
      1,
    );
    if (points.length < 2) return null;
    for (const [k, v] of resolutionOf(m.conditionId, m.outcomes, m.outcomePrices, endMs, now)) {
      resolved.set(k, v);
    }
    return {
      conditionId: m.conditionId,
      market: m.question,
      outcomes: [m.outcomes[0], m.outcomes[1]],
      tokenIds: [tokens[0], tokens[1]],
      endDateMs: endMs,
      // Never let a candle's tape run past its own close: the settlement
      // print (0 or 1) is not a tradable price, and a strat that "saw" it
      // would exit on hindsight.
      points: points.filter((p) => p.t <= endMs),
    };
  });
  series.sort((a, b) => (a.endDateMs ?? 0) - (b.endDateMs ?? 0));

  const coveredFrom = series.length > 0
    ? (series[0].endDateMs ?? opts.fromMs) - periodMs
    : opts.fromMs;
  const hours = (opts.toMs - coveredFrom) / 3_600_000;
  return {
    series,
    resolved,
    fromMs: coveredFrom,
    toMs: opts.toMs,
    markets: series.length,
    expected,
    fidelityMs: 60_000,
    mode: "candles",
    note: series.length === 0
      ? `no ${periodMin}-minute ${prefix} candles found in this window`
      : series.length < expected
        ? `replayed the last ${hours < 1 ? `${Math.round(hours * 60)}m` : `${hours.toFixed(1)}h`} — ${series.length} of the window's ${expected} candles`
        : undefined,
  };
}

async function queryTape(mo: MomentumParams, opts: TapeOpts): Promise<PriceTape> {
  const query = mo.query || opts.marketQuery || "bitcoin";
  const now = Date.now();
  // One search per OR-group, merged and deduped — the same fan-out the two
  // engines do (`assembleMarketPrices` / `fetch_momentum_series`). A replay
  // that searched the multi-asset query as ONE string would build its tape
  // from a different (and, measured, non-overlapping) universe than the
  // engine trades — the card would describe markets the strat never sees.
  const groups = marketQueryGroups(query).slice(0, MAX_MOMENTUM_QUERIES);
  const found: PolymarketMarket[] = [];
  const seen = new Set<string>();
  for (const g of groups.length > 0 ? groups : [query]) {
    let hits: PolymarketMarket[] = [];
    try {
      hits = await searchMarkets(g, 60);
    } catch {
      continue;
    }
    for (const m of hits) {
      const cid = m.conditionId?.toLowerCase();
      if (!cid || seen.has(cid)) continue;
      seen.add(cid);
      found.push(m);
    }
  }
  const candidates = found
    .filter((m) =>
      m.conditionId &&
      (m.clobTokenIds?.length ?? 0) >= 2 &&
      (m.outcomes?.length ?? 0) >= 2 &&
      // Must have overlapped the window — a market that opened after it
      // closed was never on the engine's screen.
      (!m.endDate || !Number.isFinite(Date.parse(m.endDate)) || Date.parse(m.endDate) > opts.fromMs),
    )
    .sort((a, b) => (b.volume || 0) - (a.volume || 0))
    .slice(0, Math.min(mo.maxMarkets ?? 12, opts.budget ?? BROWSER_TAPE_BUDGET));

  const resolved = new Map<string, number>();
  const series = await pooled(candidates, async (m): Promise<MarketPriceSeries | null> => {
    const tokens = m.clobTokenIds ?? [];
    const endMs = Date.parse(m.endDate);
    const points = await fetchPriceWindow(
      tokens[0],
      Math.floor((opts.fromMs - 2 * 3_600_000) / 1000), // pre-roll for the lookback
      Math.ceil(opts.toMs / 1000),
      5,
    );
    if (points.length < 2) return null;
    if (Number.isFinite(endMs)) {
      for (const [k, v] of resolutionOf(m.conditionId, m.outcomes, m.outcomePrices, endMs, now)) {
        resolved.set(k, v);
      }
    }
    return {
      conditionId: m.conditionId,
      market: m.question,
      outcomes: [m.outcomes[0], m.outcomes[1]],
      tokenIds: [tokens[0], tokens[1]],
      endDateMs: Number.isFinite(endMs) ? endMs : undefined,
      points: Number.isFinite(endMs) ? points.filter((p) => p.t <= endMs) : points,
    };
  });

  return {
    series,
    resolved,
    fromMs: opts.fromMs,
    toMs: opts.toMs,
    markets: series.length,
    expected: candidates.length,
    fidelityMs: 300_000,
    mode: "query",
    note: series.length === 0
      ? `no price history for markets matching "${query}"`
      : `search-built tape: markets that closed before today can't be found today, so this window is survivorship-biased`,
  };
}
