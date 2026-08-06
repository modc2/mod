// The hub's replay, with no React and no browser in it.
//
// One strat → one N-day backtest → one card-sized result. This half is
// deliberately environment-free because TWO callers run it:
//
//   • the console (lib/hubBacktest.ts) — the React hook that paints cards and
//     keeps a localStorage snapshot per window;
//   • the background worker (lib/server/hubWorker.ts) — a timer inside the
//     Next server that replays every published strat every 2 hours and writes
//     the results to ~/.mod/polymarket/hub/backtests.json, so the console has
//     numbers waiting for it instead of computing a wall of backtests on the
//     first frame.
//
// Both go through `runBacktest` (lib/backtest.ts) — the same engine the
// BACKTEST tab and the live session use — so a card, a tab and a deployment
// can never quote three different numbers for one strat.

import { PolymarketPosition, PolymarketTrade, SavedIndex } from "./types";
import { fetchPositions, fetchWalletTradesUntil, MAX_LOOKBACK_DAYS } from "./polymarket";
import { fetchTraderBankrolls } from "./liveSessions";
import { runBacktest, stratBacktestParams, stratFromIndex, type EntryFunnel } from "./backtest";
import { templateIndex, templateRoster, type StratTemplate } from "./defaultStrats";

/** The window every card is measured over, unless the user picks another. */
export const HUB_BACKTEST_DAYS = 1;
/** Windows the hub offers. 30 is the data-api's lookback ceiling
    (MAX_LOOKBACK_DAYS) — asking for more silently returns less. */
export const HUB_WINDOWS = [1, 3, 7, 14, 30];

/** Cards address their backtest by this key: saved strats by id, templates by
    slug. One namespace, so `backtests[hubKey]` works for both card types. */
export function templateBacktestKey(slug: string): string {
  return `tpl:${slug}`;
}

/** How long a card's replay stays good. Matches the background worker's
    cadence: the worker refreshes every card every 2 hours, so a card younger
    than that is by definition the freshest run that exists and the console
    must not burn the user's rate limit re-deriving it. */
export const TTL_MS = 2 * 3600_000;
/** Sparkline resolution — enough to read the shape, small enough to persist. */
const CURVE_POINTS = 48;
/** Traders fetched in parallel. Deliberately small: each is a paginated
    /activity walk, and the console shares its rate gate with the live engine. */
const FETCH_CONCURRENCY = 4;

export interface HubBacktest {
  /** Net PnL of the replay ($) — costs modeled exactly as the live engine. */
  pnl: number;
  /** pnl as a % of the paper capital the replay started with. */
  roi: number;
  trades: number;
  skipped: number;
  /** Where the leader flow went — see EntryFunnel. Absent on snapshots
      written before the funnel existed. */
  funnel?: EntryFunnel;
  capital: number;
  /** Window replayed, in days — the N the card labels itself with. */
  days: number;
  /** Traders replayed. */
  traders: number;
  /** Equity through the window, thinned for the card sparkline. */
  curve: number[];
  /** Why this replay is empty, when it is: a strat with no watchlist, one that
      originates its own trades, or one whose every candidate was gated —
      that's an answer, not a $0 result, and the card must not print it as
      breaking even. */
  note?: string;
  /** When this replay ran, and what it ran on (params fingerprint). */
  at: number;
  sig: string;
  /** "worker" when it came from the 2-hourly background pass, "live" when the
      console replayed it itself. Cards show the difference. */
  by?: "worker" | "live";
}

/** Everything about a strat that changes its backtest. A card whose signature
    still matches its snapshot doesn't need replaying. */
export function signature(idx: SavedIndex, days: number): string {
  const p = stratBacktestParams(idx);
  return JSON.stringify([
    days,
    idx.traders.filter((t) => t.enabled !== false).map((t) => [t.address, t.weight]),
    p.capital, p.minTrade, p.maxTrade, p.maxOpenPositions, p.stopLossPct,
    p.takeProfitFrac, p.marketQuery, p.pollMinutes, p.minMinutesToClose,
    idx.maxPerCycle ?? 3, idx.tradeFilters ?? null, idx.filter ?? null,
    idx.sizing ?? null, idx.turnover ?? null, idx.maxUpscale ?? null,
  ]);
}

/** Thin an equity history down to CURVE_POINTS evenly-spaced samples. */
export function thinCurve(history: { liq: number; pos: number }[]): number[] {
  if (history.length === 0) return [];
  if (history.length <= CURVE_POINTS) return history.map((h) => h.liq + h.pos);
  const out: number[] = [];
  for (let i = 0; i < CURVE_POINTS; i++) {
    const h = history[Math.round((i * (history.length - 1)) / (CURVE_POINTS - 1))];
    out.push(h.liq + h.pos);
  }
  return out;
}

export interface TraderFeed {
  positions: PolymarketPosition[];
  trades: PolymarketTrade[];
}

/** Fetch one trader's history, memoized per address for the session. The
    30-day pull is what the console already caches hourly (and what the strat's
    scoring window needs) — asking for 1 day here would trade a smaller
    download for a cache miss on every console visit. */
export function traderFeed(addr: string, cache: Map<string, Promise<TraderFeed>>): Promise<TraderFeed> {
  const key = addr.toLowerCase();
  const hit = cache.get(key);
  if (hit) return hit;
  const cutoffSec = Math.floor((Date.now() - MAX_LOOKBACK_DAYS * 86400_000) / 1000);
  const p = (async (): Promise<TraderFeed> => {
    try {
      const [positions, trades] = await Promise.all([
        fetchPositions(addr),
        fetchWalletTradesUntil(addr, cutoffSec, undefined, 2000),
      ]);
      return { positions, trades };
    } catch {
      return { positions: [], trades: [] };
    }
  })();
  cache.set(key, p);
  return p;
}

/** Replay a RECOMMENDED strat: materialize the template into the SavedIndex a
    fork would produce — seeded from the same cached leaderboard roster the
    fork will use — and replay that. The card's number is the strat you'd get. */
export async function backtestTemplate(
  t: StratTemplate,
  days: number,
  cache: Map<string, Promise<TraderFeed>>,
): Promise<HubBacktest | null> {
  try {
    const roster = await templateRoster(t);
    // Pinned `now`: id/name never reach the replay, and letting them move
    // would churn the signature for nothing.
    return await backtestOne(templateIndex(t, roster, 0), days, cache);
  } catch {
    return null;
  }
}

/** Replay ONE strat over the window. A strat with nothing to copy still
    returns a result — carrying the REASON, so its card says "originates its
    own trades" instead of printing a $0 that reads as breaking even. */
export async function backtestOne(
  idx: SavedIndex,
  days: number,
  cache: Map<string, Promise<TraderFeed>>,
): Promise<HubBacktest | null> {
  const watchlist = idx.traders.filter((t) => t.enabled !== false).map((t) => t.address);
  if (watchlist.length === 0) {
    const p = stratBacktestParams(idx);
    return {
      pnl: 0, roi: 0, trades: 0, skipped: 0, capital: p.capital, days, traders: 0,
      curve: [],
      note: idx.momentum
        ? "originates its own trades — no copied flow to replay"
        : "no traders to copy",
      at: Date.now(),
      sig: signature(idx, days),
    };
  }

  const traderTrades = new Map<string, PolymarketTrade[]>();
  const traderPositions = new Map<string, PolymarketPosition[]>();
  for (let i = 0; i < watchlist.length; i += FETCH_CONCURRENCY) {
    const slice = watchlist.slice(i, i + FETCH_CONCURRENCY);
    const feeds = await Promise.all(slice.map((a) => traderFeed(a, cache)));
    slice.forEach((addr, j) => {
      traderTrades.set(addr, feeds[j].trades);
      traderPositions.set(addr, feeds[j].positions);
    });
  }

  const traderWeights: Record<string, number> = {};
  for (const t of idx.traders) traderWeights[t.address] = Math.round(t.weight * 100);
  const traderBankrolls = await fetchTraderBankrolls(watchlist);
  const p = stratBacktestParams(idx);

  const { sim } = runBacktest({
    watchlist,
    traderTrades,
    traderPositions,
    traderWeights,
    traderBankrolls,
    // The WHOLE strat, not the four fields the hub used to rebuild by hand —
    // a card that drops `sizing` or the time-to-close gate is measuring a
    // strat the user doesn't own.
    strat: stratFromIndex(idx),
    sizing: idx.sizing,
    turnover: idx.turnover,
    days,
    ...p,
  });

  return {
    pnl: sim.netPnl,
    roi: p.capital > 0 ? Math.round((sim.netPnl / p.capital) * 10_000) / 100 : 0,
    trades: sim.rows.length,
    skipped: sim.skipped,
    funnel: sim.funnel,
    capital: p.capital,
    days,
    traders: watchlist.length,
    curve: thinCurve(sim.equityHistory),
    // Observed flow but zero executions is a real answer too — every
    // candidate was gated. Say WHICH gate: "225 blocked" with no name is the
    // complaint this replaced.
    note: sim.rows.length === 0 ? emptyNote(sim.funnel) : undefined,
    at: Date.now(),
    sig: signature(idx, days),
  };
}

/** Why a replay executed nothing, in the user's vocabulary — the dominant
    gate and its share, e.g. "all 225 entries blocked · time-to-close". */
export function emptyNote(funnel: EntryFunnel): string {
  const top = Object.entries(funnel.reasons).sort((a, b) => b[1] - a[1])[0];
  if (!funnel.observed) return "no leader flow in this window";
  if (!top) return "every candidate was filtered out";
  return `all ${funnel.observed} entries blocked · ${top[0]}`;
}
