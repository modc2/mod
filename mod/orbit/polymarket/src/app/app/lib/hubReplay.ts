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
import {
  runBacktest, stratBacktestParams, stratFromIndex,
  type EntryFunnel, type Settlement,
} from "./backtest";
import { tapeFor } from "./momentumTape";
import type { PriceTape } from "./originationBacktest";
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

/** How long a card's replay stays good IN THE BROWSER. The server worker
    replays more often than this (it costs no upstream requests — it runs over
    its feed cache), so a card younger than this is at worst one worker cycle
    behind, and re-deriving it here would spend the user's rate limit on a
    number the server already has. */
export const TTL_MS = 2 * 3600_000;
/** Sparkline resolution — enough to read the shape, small enough to persist. */
const CURVE_POINTS = 48;
/** Traders fetched in parallel. Deliberately small: each is a paginated
    /activity walk, and the console shares its rate gate with the live engine. */
const FETCH_CONCURRENCY = 4;

/** The result of asking a strat the only question that matters: did the edge
 *  it showed YESTERDAY still pay TODAY?
 *
 *    "held"      profitable in the prior window AND in the one after it —
 *                the card's headline is confirmed out-of-sample.
 *    "faded"     made money on the evidence window, lost it on the next one.
 *                This is the one to be afraid of: it's what a strat fitted to
 *                one good day looks like the moment you deploy it.
 *    "recovered" lost on the prior window, made money since.
 *    "no-edge"   lost on both.
 *    "untested"  the prior window had no trades, so there was no edge to
 *                confirm — an unproven card, not a passing one.
 *    "stalled"   made money on the evidence window and then stopped trading
 *                entirely. Distinct from "faded": it didn't lose, it went
 *                quiet, and the fix is a gate, not the strategy.
 *    "idle"      neither window traded at all.
 */
export type ForwardVerdict =
  | "held" | "faded" | "recovered" | "no-edge" | "untested" | "stalled" | "idle";

/** The evidence half of a walk-forward: the window BEFORE the card's own,
    replayed with no knowledge of it (see `asOf` in lib/backtest.ts), plus the
    verdict of comparing the two. The card's own numbers are the "next" half —
    they're already on the card, so they're not duplicated here. */
export interface ForwardCheck {
  /** Prior-window bounds (ms epoch): [from, to], where `to` is where the
      card's own window starts. */
  from: number;
  to: number;
  /** Length of EACH window in days — both halves are the same length, so a 1D
      card is "yesterday → today" and a 7D card is "last week → this week". */
  days: number;
  /** The prior window's replay: what the strat did on the evidence day. */
  pnl: number;
  roi: number;
  trades: number;
  /** Prior vs. next. `ok` is the plain answer to "is it profitable for the
      next day, given the previous day" — true ONLY for "held". */
  verdict: ForwardVerdict;
  ok: boolean;
}

/** Prior window vs. the window after it — the whole walk-forward rule, kept as
    one pure function so the worker, the console and the tests can't disagree
    about what "confirmed" means. */
export function forwardVerdict(
  prior: { pnl: number; trades: number },
  next: { pnl: number; trades: number },
): ForwardVerdict {
  if (prior.trades === 0 && next.trades === 0) return "idle";
  if (prior.trades === 0) return "untested";
  if (next.trades === 0) return prior.pnl > 0 ? "stalled" : "no-edge";
  if (prior.pnl > 0) return next.pnl > 0 ? "held" : "faded";
  return next.pnl > 0 ? "recovered" : "no-edge";
}

/** How much price data an origination replay stood on — the JSON-safe half of
    `PriceTape` (its `series`/`resolved` are far too big for a card). */
export interface TapeCoverage {
  mode: "candles" | "query";
  /** Markets replayed, and how many the window contains. */
  markets: number;
  expected: number;
  /** Spacing of the price points the replay could see (60_000 = 1-min bars —
      a live 30s cycle sees moves between them that this replay cannot). */
  fidelityMs: number;
  /** Start of the covered span (ms) — later than the window start when the
      fetch budget clipped it. */
  fromMs: number;
  note?: string;
}

export interface HubBacktest {
  /** Net PnL of the replay ($) — costs modeled exactly as the live engine. */
  pnl: number;
  /** Polymarket taker fees the replay paid, and what they came to in basis
      points of the notional it traded. `pnl` is already net of these; they are
      carried so a card can say WHY a busy strat with a real edge still lost.
      Absent on snapshots written before fees were priced (which booked 0). */
  fees?: number;
  feeBps?: number;
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
  /** Why this replay is empty, when it is: a strat with no watchlist, one
      whose every candidate was gated, or one whose price tape was empty —
      that's an answer, not a $0 result, and the card must not print it as
      breaking even. */
  note?: string;
  /** How much of the exit value was looked up vs guessed — see `Settlement`
      in lib/backtest.ts. A result with a big `markedUsd` is a hypothesis:
      those legs were valued at the last price a leader printed, which is the
      entry price for anything that quietly expired worthless. Absent on
      snapshots written before resolutions were fetched. */
  settlement?: Settlement;
  /** WALK-FORWARD: the same strat replayed over the window immediately BEFORE
      this one, and the verdict of comparing the two. The card above is the
      "next day"; this is the "previous day" it's judged against. Absent when
      the caller asked for a bare replay (or on snapshots written before the
      check existed) — a card must then say "unchecked", never "held". */
  forward?: ForwardCheck;
  /** For an ORIGINATING strat: what price data the replay actually had. A
      candle tape capped by the fetch budget covers the window's tail, not the
      whole window, and a card that hides that is claiming a day of evidence it
      never had. Absent for pure copy strats (they need no tape). */
  tape?: TapeCoverage;
  /** When this replay ran, and what it ran on (params fingerprint). */
  at: number;
  sig: string;
  /** "worker" when it came from the background pass, "live" when the console
      replayed it itself. Cards show the difference. */
  by?: "worker" | "live";
  /** Watchlist traders whose history wasn't in the server's feed store when
      this ran, i.e. that were replayed as having done nothing. Set only by
      the worker, and only while the cache is still filling — a card carrying
      it is INCOMPLETE, not flat, and must say so. */
  warming?: number;
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
    // Gates the replay applies must all be in the signature, or editing one
    // leaves the hub serving a snapshot computed under the old gate.
    idx.maxTradeAgeSec ?? null,
    // Momentum params drive the ORIGINATION replay end to end — the markets it
    // watches, the rise it needs, the band it buys in. Editing them without
    // this in the signature leaves the card showing the old strat's trades.
    idx.momentum ?? null,
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

/** How a replay gets one trader's history. The browser passes nothing and
    gets `traderFeed` — fetch, memoized for the session. The background worker
    passes a loader backed by its on-disk feed store (server/feedFetcher.ts),
    so a pass replays over cached data and touches the network only for a
    trader it has never seen. Same replay either way; only the source of the
    bytes differs. */
export type FeedLoader = (
  addr: string,
  cache: Map<string, Promise<TraderFeed>>,
) => Promise<TraderFeed>;

/** How a replay learns what its markets PAID OUT — leg key → 0 or 1, for the
    condition ids the window touched (see lib/backtest.ts `Settlement`). The
    worker passes one backed by its resolution store; a caller that passes
    nothing gets a replay that marks its unsold inventory at the last observed
    price and reports how much of the result that is. */
export type LegResolver = (conditionIds: string[]) => Promise<Map<string, number>>;

export interface ReplayOpts {
  loader?: FeedLoader;
  resolve?: LegResolver;
  /** Template replays only: a roster the caller already resolved. */
  roster?: string[];
  /** Also replay the window BEFORE this one and attach the verdict (default
      true). Costs no extra fetching for a COPY strat — both windows read the
      same 30-day feed already in hand. An originating strat does pay for it:
      its price tape is per-window. */
  forward?: boolean;
  /** Markets an origination tape may fetch history for (lib/momentumTape.ts).
      The worker passes a bigger budget than the browser — it has nobody
      waiting and reads through the API's own disk cache. */
  tapeBudget?: number;
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
  opts: ReplayOpts = {},
): Promise<HubBacktest | null> {
  try {
    // `roster` lets the worker supply a roster it has already resolved (and
    // cached to disk) — templateRoster's own cache is localStorage, which
    // does nothing in Node, so every pass would otherwise re-query the
    // leaderboard for every template.
    const roster = opts.roster ?? (await templateRoster(t));
    // Pinned `now`: id/name never reach the replay, and letting them move
    // would churn the signature for nothing.
    return await backtestOne(templateIndex(t, roster, 0), days, cache, opts.loader, opts.resolve, {
      forward: opts.forward,
      tapeBudget: opts.tapeBudget,
    });
  } catch {
    return null;
  }
}

/** Replay ONE strat over the window — TWICE, unless told otherwise.
 *
 *  The card's headline is the window that just ended. The second replay covers
 *  the equal-length window before it, run with `asOf` pinned to that window's
 *  end so it knows nothing about the days after it. Comparing them is the only
 *  thing on a card that distinguishes "this strat makes money" from "this
 *  strat made money once": a strat can always be found that printed a good
 *  number over one window, and the one after it is where that claim dies.
 *
 *  Both windows read the SAME already-fetched 30-day feed, so the check costs
 *  CPU and no upstream requests.
 *
 *  A strat with nothing to copy still returns a result — carrying the REASON,
 *  so its card says WHY ("no traders to copy", "no price tape for this window")
 *  instead of printing a $0 that reads as breaking even. An ORIGINATING strat
 *  is not such a case any more: it has no watchlist by design and is replayed
 *  against its window's price tape instead. */
export async function backtestOne(
  idx: SavedIndex,
  days: number,
  cache: Map<string, Promise<TraderFeed>>,
  loader: FeedLoader = traderFeed,
  resolve?: LegResolver,
  opts: { forward?: boolean; tapeBudget?: number } = {},
): Promise<HubBacktest | null> {
  const watchlist = idx.traders.filter((t) => t.enabled !== false).map((t) => t.address);
  // An ORIGINATING strat has nothing to copy by design — that used to end the
  // replay right here with "originates its own trades", i.e. no backtest at
  // all for the two strats this deployment actually runs. It gets a real
  // replay now, off the historical price tape (see the origination pass in
  // `replay` below); only a strat with neither a watchlist NOR a signal of its
  // own is genuinely un-replayable.
  if (watchlist.length === 0 && !idx.momentum) {
    const p = stratBacktestParams(idx);
    return {
      pnl: 0, roi: 0, trades: 0, skipped: 0, capital: p.capital, days, traders: 0,
      curve: [],
      note: "no traders to copy",
      at: Date.now(),
      sig: signature(idx, days),
    };
  }

  const traderTrades = new Map<string, PolymarketTrade[]>();
  const traderPositions = new Map<string, PolymarketPosition[]>();
  for (let i = 0; i < watchlist.length; i += FETCH_CONCURRENCY) {
    const slice = watchlist.slice(i, i + FETCH_CONCURRENCY);
    const feeds = await Promise.all(slice.map((a) => loader(a, cache)));
    slice.forEach((addr, j) => {
      traderTrades.set(addr, feeds[j].trades);
      traderPositions.set(addr, feeds[j].positions);
    });
  }

  const traderWeights: Record<string, number> = {};
  for (const t of idx.traders) traderWeights[t.address] = Math.round(t.weight * 100);
  const traderBankrolls = await fetchTraderBankrolls(watchlist);
  const p = stratBacktestParams(idx);

  const now = Date.now();
  const windowMs = days * 86400_000;
  const wantForward = opts.forward !== false;
  // Ask about every market EITHER window touches, not just the ones the strat
  // ends up holding: which positions survive to settlement isn't known until
  // the replay has run, and the store answers cached ids for free. One lookup
  // covers both halves of the walk-forward.
  const oldest = now - (wantForward ? 2 * windowMs : windowMs);
  const touched = new Set<string>();
  for (const trades of traderTrades.values()) {
    for (const t of trades) {
      if (t.timestamp >= oldest && t.conditionId) touched.add(t.conditionId);
    }
  }
  const resolved = resolve ? await resolve([...touched]) : undefined;

  // The ORIGINATION tape, one per window half. A momentum strat's markets are
  // not the leaders' markets — they're whatever the price feed was tracking at
  // the time — so this is a separate fetch, made only for strats that
  // originate (undefined otherwise, and then the pass costs nothing).
  const tapes = new Map<number, PriceTape | undefined>();
  const windowEnds = wantForward ? [now, now - windowMs] : [now];
  for (const end of windowEnds) {
    tapes.set(end, await tapeFor(idx.momentum, idx.marketQuery, days, end, opts.tapeBudget));
  }

  // One window, one replay. `asOf` is what makes the second call honest: the
  // prior window is replayed as if the engine were standing at its end.
  const replay = (asOf: number) => runBacktest({
    tape: tapes.get(asOf),
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
    resolved,
    days,
    asOf,
    ...p,
  }).sim;

  const roiOf = (pnl: number) =>
    p.capital > 0 ? Math.round((pnl / p.capital) * 10_000) / 100 : 0;

  const tape = tapes.get(now);
  const sim = replay(now);
  let forward: ForwardCheck | undefined;
  if (wantForward) {
    const priorEnd = now - windowMs;
    const prior = replay(priorEnd);
    const next = { pnl: sim.netPnl, trades: sim.rows.length };
    const verdict = forwardVerdict({ pnl: prior.netPnl, trades: prior.rows.length }, next);
    forward = {
      from: priorEnd - windowMs,
      to: priorEnd,
      days,
      pnl: prior.netPnl,
      roi: roiOf(prior.netPnl),
      trades: prior.rows.length,
      verdict,
      ok: verdict === "held",
    };
  }

  return {
    pnl: sim.netPnl,
    roi: roiOf(sim.netPnl),
    fees: sim.fees,
    feeBps: Math.round(sim.costs.effectiveBps),
    trades: sim.rows.length,
    skipped: sim.skipped,
    funnel: sim.funnel,
    capital: p.capital,
    days,
    traders: watchlist.length,
    curve: thinCurve(sim.equityHistory),
    settlement: sim.settlement,
    forward,
    tape: tape && {
      mode: tape.mode,
      markets: tape.markets,
      expected: tape.expected,
      fidelityMs: tape.fidelityMs,
      fromMs: tape.fromMs,
      note: tape.note,
    },
    // Observed flow but zero executions is a real answer too — every
    // candidate was gated. Say WHICH gate: "225 blocked" with no name is the
    // complaint this replaced. For an originating strat with no tape the gate
    // isn't the strat's at all — it's missing price data, and the tape says so.
    note: sim.rows.length === 0
      ? (tape && tape.markets === 0 ? (tape.note ?? "no price tape for this window") : emptyNote(sim.funnel))
      : undefined,
    at: now,
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
