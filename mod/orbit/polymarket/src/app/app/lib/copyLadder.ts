// ONE LEADER, SEVERAL SIZES — the desk's "what would $N on them have done?"
//
// The copy desk already shows, per row, a backtest at the dollars the row
// holds (the worker replays the book, lib/hubBacktest.ts paints it). What it
// could not do was answer the next question on the same screen: "and at
// $N instead?" — you had to walk to the trader's profile (CopySimPanel) or
// the basket. This module is that answer, reusable from any surface.
//
// It is the SAME replay the cards run — `backtestOne` in lib/hubReplay.ts —
// unrolled so the expensive parts happen once per leader, not once per size:
//
//   feed (30-day fill walk)  →  fetched once, memoized per address
//   bankroll                 →  fetched once
//   resolutions              →  looked up once for every market the window
//                               touched (dead losers book, instead of marking
//                               at their last print)
//   replay                   →  once PER SIZE, synchronous, through
//                               `identityStrat` → `runBacktest`, so each rung
//                               is the row the engine would run at that size
//
// Every rung carries the walk-forward verdict too, computed the way the cards
// compute it: the window before this one is replayed at the same size, and
// "held" is the only pass. A $50 rung and a $500 rung of the same leader can
// disagree — copying is not linear in N (see [[polymarket_copy_blockers]]) —
// and that disagreement is what the ladder exists to show.

import type { EquityMarker, EquitySnapshot } from "../components/EquityChart";
import {
  runBacktest, stratBacktestParams, stratFromIndex, settlementConfidence,
  type EntryFunnel, type Settlement,
} from "./backtest";
import { fetchResolvedLegs } from "./hubCache";
import {
  emptyNote, forwardVerdict, thinCurve, traderFeed,
  type ForwardVerdict, type TraderFeed,
} from "./hubReplay";
import { identityStrat, type Allocation } from "./identityStrat";
import { fetchTraderBankrolls } from "./liveSessions";

/** The rungs every ladder reports. They straddle the order floor: below ~$25
    a proportional mirror of most leaders lands under `clobMinNotional` and
    simply doesn't place. Same list CopySimPanel uses on the profile. */
export const LADDER_SIZES = [10, 25, 50, 100, 250, 500, 1000, 2500];

/** One rung: the leader replayed at `capital`. */
export interface SizedReplay {
  capital: number;
  /** Net P&L ($), costs modeled exactly as the live engine. */
  pnl: number;
  /** pnl as a % of `capital`. */
  roi: number;
  /** Rows the sim filled — entries and exits, like the cards' `trades`. */
  trades: number;
  /** Entries filled out of the leader's observed BUYs. */
  executed: number;
  skipped: number;
  funnel: EntryFunnel;
  /** Full equity history for the chart, plus a thinned copy for sparklines. */
  history: EquitySnapshot[];
  markers: EquityMarker[];
  curve: number[];
  /** What the sim ended with: cash + marked positions. */
  endEquity: number;
  settlement: Settlement;
  /** Share of settled value that came from a looked-up resolution (0–1). */
  confidence: number;
  /** Walk-forward against the window before this one, at the same size. */
  forward: { pnl: number; trades: number; verdict: ForwardVerdict; ok: boolean };
  /** Why nothing was copied, when nothing was. */
  note?: string;
  days: number;
  at: number;
}

/** Everything a replay needs from the network, gathered once per leader. */
export interface LadderInputs {
  feed: TraderFeed;
  bankrolls: Map<string, number>;
  resolved: Map<string, number>;
  days: number;
  at: number;
}

/** Fetch the inputs for one leader over `days`. Feeds are memoized in
    `cache`; bankroll and resolutions are small and fetched fresh. */
export async function ladderInputs(
  address: string,
  days: number,
  cache: Map<string, Promise<TraderFeed>>,
): Promise<LadderInputs> {
  const feed = await traderFeed(address, cache);
  const bankrolls = await fetchTraderBankrolls([address]);
  const at = Date.now();
  // Both walk-forward halves, so one lookup serves every rung.
  const oldest = at - 2 * days * 86400_000;
  const touched = new Set<string>();
  for (const t of feed.trades) {
    if (t.timestamp >= oldest && t.conditionId) touched.add(t.conditionId);
  }
  let resolved = new Map<string, number>();
  if (touched.size > 0) {
    try {
      resolved = await fetchResolvedLegs([...touched].sort());
    } catch {
      // No resolutions is survivable — the rung's `confidence` says so.
    }
  }
  return { feed, bankrolls, resolved, days, at };
}

/** Replay one leader at one size. Pure and synchronous once the inputs are in
    hand — callers loop over sizes and yield between them. */
export function replayAtSize(
  alloc: Allocation,
  capital: number,
  inputs: LadderInputs,
): SizedReplay {
  const { feed, bankrolls, resolved, days, at } = inputs;
  const address = alloc.address;
  const idx = identityStrat({ ...alloc, allocationUsd: capital });
  const p = stratBacktestParams(idx);
  const windowMs = days * 86400_000;

  const replay = (asOf: number) => runBacktest({
    watchlist: [address],
    traderTrades: new Map([[address, feed.trades]]),
    traderPositions: new Map([[address, feed.positions]]),
    traderWeights: { [address]: 100 },
    traderBankrolls: bankrolls,
    strat: stratFromIndex(idx),
    sizing: idx.sizing,
    turnover: idx.turnover,
    resolved,
    days,
    asOf,
    ...p,
    capital,
  }).sim;

  const sim = replay(at);
  const prior = replay(at - windowMs);
  const verdict = forwardVerdict(
    { pnl: prior.netPnl, trades: prior.rows.length },
    { pnl: sim.netPnl, trades: sim.rows.length },
  );

  return {
    capital,
    pnl: sim.netPnl,
    roi: capital > 0 ? Math.round((sim.netPnl / capital) * 10_000) / 100 : 0,
    trades: sim.rows.length,
    executed: sim.funnel.executed,
    skipped: sim.skipped,
    funnel: sim.funnel,
    history: sim.equityHistory,
    markers: sim.markers,
    curve: thinCurve(sim.equityHistory),
    endEquity: sim.cash + sim.posValue,
    settlement: sim.settlement,
    confidence: settlementConfidence(sim.settlement),
    forward: { pnl: prior.netPnl, trades: prior.rows.length, verdict, ok: verdict === "held" },
    note: sim.rows.length === 0 ? emptyNote(sim.funnel) : undefined,
    days,
    at,
  };
}

/** The rungs a ladder shows for a row: the standard sizes, plus the row's own
    allocation and whatever the user typed, deduplicated and sorted. */
export function ladderSizes(allocationUsd: number, typed: number | null): number[] {
  const extra = [allocationUsd, typed ?? 0].filter((n) => Number.isFinite(n) && n > 0);
  return [...new Set([...LADDER_SIZES, ...extra])].sort((a, b) => a - b);
}
