// The ORIGINATION replay — the half of the backtest that was missing.
//
// lib/backtest.ts replays LEADER FLOW: it walks the watchlist's fills and asks
// the strat which ones to mirror. That is the whole story for a copy strat and
// none of the story for an originating one. A momentum strat has no watchlist:
// it reads a market's own price tape and calls `strat.propose()` once per
// cycle. `backtest.ts` never calls `propose()`, so every such strat replayed as
// `{watchlist: []} → empty sim` and its card printed
//
//     "originates its own trades — no copied flow to replay"
//
// while the live engine placed orders all day. BTC 5-MIN DELTA and BTC MOMENTUM
// — the two strats this deployment actually runs — had NO backtest at all. That
// is the "backtest doesn't mimic live" gap, and this file closes it.
//
// The model is the live cycle loop, not a curve fit. For every cycle between
// the window's start and its end (at the strat's own poll cadence, clamped by
// the same MIN_POLL_MINUTES floor the engine enforces) it:
//
//   1. redeems anything that resolved since the last cycle    (live: auto_redeem)
//   2. checks take-profit then stop-loss on every hold        (live: check_stop_losses)
//   3. shows the strat ONLY the markets live at that instant  (live: fetch_candle_series /
//      with points only up to that instant                     assembleMarketPrices)
//   4. calls `strat.propose(history, constraints)`            (live: Phase 4)
//   5. executes exits first, then entries, under the same     (live: execute_momentum,
//      cooldown / order-cap / free-capital / share-rounding    copyEngine.executeProposals)
//      rules, at the strat's OWN limit price
//
// Fills book at the OBSERVED price, with the strat's limit deciding only
// whether the order was marketable at all. That is deliberate and it is the
// same assumption the copy replay makes (it books a mirror at the leader's
// print): a GTC buy limit 2¢ above the last trade executes against the resting
// ask, it does not pay the 2¢ — charging the chase would invent a ~3% cost per
// round trip that the deployment never pays, exactly as filling at the mid on
// an unmarketable limit would invent an edge it never gets. What neither side
// models is the SPREAD, and both are honest about that in the same place.
//
// What this still does NOT model, stated plainly because a backtest that
// overstates its own fidelity is worse than none:
//   • the bid/ask spread, on either side (the copy replay skips it too);
//   • partial and unfilled GTC orders — a marketable limit fills whole;
//   • sub-minute price action. The tape is 1-minute bars (the CLOB's finest);
//     live polls a 5-minute candle every ~30s and also reads the midpoint, so it
//     sees moves between bars that this replay cannot. On a 5-minute candle that
//     is a real difference — `PriceTape.fidelityMs` carries it so the surfaces
//     can say so.

import type { PolymarketPosition } from "./types";
import {
  Strat, clobMinNotional, stopLossTriggered, takeProfitTriggered, tickRoundPrice,
  MIN_POLL_MINUTES, POLYMARKET_MIN_SHARES, POLYMARKET_MIN_USD,
} from "./strats/strat";
import type { MarketPriceSeries, ProposedTrade, StratHistory } from "./strats/strat";
import { legKey, legOutcome } from "./leg";
// Costs come from the SAME model the copy replay books (lib/fees.ts) so both
// halves of a strat pay the same friction. One difference is structural: an
// origination replay has no leader fills to measure a market's fee rate off,
// so every rate here is inferred from the market's category — which is exactly
// why `CostBreakdown.coverage` reports how many rates were modelled.
import { emptyFunnel, emptySettlement,
  type BacktestSim, type EntryFunnel, type LinkedTrade, type Settlement,
} from "./backtest";
import {
  CostLedger, FeeBook, FALLBACK_GAS_QUOTE, NEW_DEPLOYMENT_GAS_OPS, mergeCostBreakdowns,
  sessionGasUsd, type GasOps, type GasQuote,
} from "./fees";
import type { EquityMarker, EquitySnapshot } from "../components/EquityChart";
import type { PerfPosition } from "../components/PerfPanel";

/** How long a (market, outcome, side) signal is muted after the engine acts on
    it — mirror of copyEngine.ts `PROPOSAL_COOLDOWN_MS` and live_engine.rs
    `PROPOSAL_COOLDOWN_MS`. Without it a persisting signal re-stacks the same
    order every cycle, live and here. */
const PROPOSAL_COOLDOWN_MS = 30 * 60_000;

/** The historical price tape an origination replay runs on: the same
 *  `MarketPriceSeries` shape the live engine feeds `history.marketPrices`,
 *  except spanning a past window instead of the last few hours. Built by
 *  lib/momentumTape.ts; kept as a type here so the sim stays pure.
 *
 *  `resolved` is what makes the result mean anything: a 5-minute candle settles
 *  at exactly $1 or $0 and gamma publishes which, so an origination replay never
 *  has to guess at the tail the way a copy replay does. */
export interface PriceTape {
  /** One entry per market the window covered, points ascending (ms). */
  series: MarketPriceSeries[];
  /** Leg key (lib/leg.ts) → payout, 0 or 1, for markets that have resolved. */
  resolved: Map<string, number>;
  /** Window the tape actually covers (ms epoch). */
  fromMs: number;
  toMs: number;
  /** Markets fetched, and how many the window contains — a candle window
      capped by the fetch budget covers its tail, and must say so. */
  markets: number;
  expected: number;
  /** Spacing of the price points, ms. 60_000 for candle tapes (the CLOB's
      finest fidelity); 300_000 for the search-mode 5-minute bars. */
  fidelityMs: number;
  mode: "candles" | "query";
  /** Set when the tape is empty or partial, in the user's vocabulary. */
  note?: string;
}

export function emptyTape(mode: PriceTape["mode"] = "query"): PriceTape {
  return {
    series: [], resolved: new Map(), fromMs: 0, toMs: 0,
    markets: 0, expected: 0, fidelityMs: 60_000, mode,
  };
}

export interface OriginationInput {
  /** The SAME Strat the live engine runs — origination goes through its
      `propose` hook, so what you backtest is what trades. */
  strat: Strat;
  tape: PriceTape;
  capital: number;
  /** User's TRADE SIZE band (USD) — the engine's minOrderSize/maxOrderSize. */
  minTrade: number;
  maxTrade: number;
  maxOpenPositions: number;
  /** Integer PERCENT loss from entry that triggers the exit; 0 = off. */
  stopLossPct: number;
  /** Absolute mark level that liquidates; 0 = off. */
  takeProfitFrac: number;
  /** Live poll cadence in minutes — one cycle of the engine loop. */
  pollMinutes: number;
  /** Minimum shares per order — the engine's `minShares` (default 5). */
  minShares?: number;
  days: number;
  /** Window end (ms epoch); the window is [asOf − days, asOf]. */
  asOf?: number;
  /** Live Polygon gas price + POL price; defaults to `FALLBACK_GAS_QUOTE`. */
  gasQuote?: GasQuote;
  /** On-chain ops this deployment pays gas for; defaults to a fresh one. */
  gasOps?: GasOps;
}

interface Hold {
  shares: number;
  avgPx: number;
  market: string;
  conditionId: string;
  outcome: string;
  /** Market close (ms) — when this leg redeems. */
  endMs?: number;
  openedAt: number;
}

const round2 = (v: number) => Math.round(v * 100) / 100;

function tally(f: EntryFunnel, reason: string, n = 1): void {
  f.reasons[reason] = (f.reasons[reason] ?? 0) + n;
}

/** The last price at/before `t` in an ascending series, or null when the
    series hasn't started yet. This is the only price the replay is allowed to
    see at cycle `t` — reading `points[points.length - 1]` would hand the strat
    the future, which is how a backtest invents an edge. */
function priceAt(points: { t: number; p: number }[], t: number): number | null {
  let lo = 0, hi = points.length - 1, found = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (points[mid].t <= t) { found = mid; lo = mid + 1; } else { hi = mid - 1; }
  }
  return found >= 0 ? points[found].p : null;
}

/** How many points of `series` are at/before `t` (the slice the strat sees). */
function visibleCount(points: { t: number; p: number }[], t: number): number {
  let lo = 0, hi = points.length - 1, found = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (points[mid].t <= t) { found = mid; lo = mid + 1; } else { hi = mid - 1; }
  }
  return found + 1;
}

/** The mark for a leg at `t`, from whichever outcome of the market the tape
    tracks (binary markets: outcome[1] is the complement). Returns null when
    the market isn't in the tape or hasn't printed yet. */
function legMark(
  tape: PriceTape,
  byCondition: Map<string, MarketPriceSeries>,
  conditionId: string,
  outcome: string,
  t: number,
): number | null {
  const s = byCondition.get(conditionId.toLowerCase());
  if (!s) return null;
  const p = priceAt(s.points, t);
  if (p === null) return null;
  const idx = s.outcomes.findIndex((o) => o.toLowerCase() === outcome.trim().toLowerCase());
  return idx === 1 ? 1 - p : p;
}

/** Replay an ORIGINATING strat over a historical price tape.
 *
 *  Returns the same `BacktestSim` the copy replay returns — same rows, same
 *  equity snapshots, same markers, same settlement accounting — so every
 *  surface (PerfPanel, EquityChart, the hub card, the walk-forward check)
 *  renders an originated result and a copied one through one code path. */
export function runOriginationSim(input: OriginationInput): BacktestSim {
  const {
    strat, tape, capital, minTrade, maxTrade, maxOpenPositions,
    stopLossPct, takeProfitFrac, pollMinutes, days,
  } = input;
  const minShares = input.minShares ?? POLYMARKET_MIN_SHARES;
  const endMs = input.asOf && input.asOf > 0 ? input.asOf : Date.now();
  const startMs = endMs - days * 86400_000;
  const funnel = emptyFunnel();

  const rows: LinkedTrade[] = [];
  const equityHistory: EquitySnapshot[] = [];
  const markers: EquityMarker[] = [];
  const settlement: Settlement = emptySettlement();
  const book = new Map<string, Hold>();
  const cooldown = new Map<string, number>();
  let cash = capital;
  const gasQuote: GasQuote = input.gasQuote ?? FALLBACK_GAS_QUOTE;
  const gasOps: GasOps = input.gasOps ?? NEW_DEPLOYMENT_GAS_OPS;
  // No fills to measure rates off (see the import note) — the ledger's fee
  // book is empty, so every market prices off its category.
  const ledger = new CostLedger(new FeeBook(), gasQuote);
  let volume = 0;
  let skipped = 0;
  let settles = 0;

  const empty: BacktestSim = {
    rows: [], equityHistory: [], markers: [], skipped: 0, funnel,
    netPnl: 0, grossPnl: 0, fees: 0, gas: 0,
    costs: ledger.breakdown({}, 0), volume: 0,
    cash: capital, posValue: 0, unrealized: 0, costBasis: 0, open: [],
    settlement,
  };
  if (tape.series.length === 0) return empty;

  // One series per market, for marking held legs whose market has already
  // scrolled out of the strat's view (live keeps reading a held token's book
  // long after momentum stopped tracking its market).
  const byCondition = new Map<string, MarketPriceSeries>();
  for (const s of tape.series) byCondition.set(s.conditionId.toLowerCase(), s);

  // The candle period, for deciding which candle is LIVE at each cycle. Candle
  // mode shows the strat exactly one market — the candle running right now —
  // because that is all `fetch_candle_series` resolves (a deterministic slug
  // off the clock). Handing it every candle in the window at once would let it
  // trade markets a live session never sees.
  const candlePeriodMs = strat.params.momentum?.candles
    ? (strat.params.momentum.candles.periodMinutes ?? 5) * 60_000
    : 0;

  const posValue = (t: number): number => {
    let v = 0;
    for (const [k, b] of book) {
      const mark = legMark(tape, byCondition, b.conditionId, b.outcome, t) ?? b.avgPx;
      v += b.shares * (tape.resolved.get(k) ?? mark);
    }
    return v;
  };

  equityHistory.push({ t: startMs, liq: capital, pos: 0 });

  // Mark-to-market snapshots between fills, bucketed so a 30d window doesn't
  // emit a point per cycle (a 30s cadence over 30 days is 86k cycles).
  const SNAP_MS = Math.max(5 * 60_000, (days * 86400_000) / 400);
  let lastSnapT = startMs;

  const record = (
    t: number, side: "BUY" | "SELL", hold: Pick<Hold, "market" | "conditionId" | "outcome">,
    amount: number, price: number, realized: number, prevEquity: number, label: string,
    fee = 0,
  ) => {
    const pos = posValue(t);
    const equity = cash + pos;
    rows.push({
      ts: t,
      market: hold.market,
      conditionId: hold.conditionId,
      // Origination has no leader — the console renders this column as the
      // source of the trade, and the source is the strat itself.
      trader: strat.name || "STRAT",
      side,
      amount: round2(amount),
      price,
      fee: round2(fee),
      realized: round2(realized),
      runningPnl: round2(equity - capital),
      pnlDelta: round2(equity - prevEquity),
      cash: round2(cash),
      pos: round2(pos),
    });
    equityHistory.push({ t, liq: cash, pos });
    lastSnapT = t;
    markers.push({ t, side, usd: round2(amount), label });
  };

  /** Live's auto-redeem, replayed: a resolved market pays $1 or $0 per share
      and the cash comes back. Without this the book fills with dead candles,
      `maxOpenPositions` saturates and the replay flatlines — the same failure
      the copy sim's `settleDead` exists to prevent, except here the payout is
      FACT (gamma publishes the candle's outcome) rather than a last mark. */
  const redeem = (t: number) => {
    for (const [k, b] of [...book]) {
      if (b.shares <= 1e-9) { book.delete(k); continue; }
      if (b.endMs === undefined || b.endMs > t) continue;
      const truth = tape.resolved.get(k);
      const px = truth ?? legMark(tape, byCondition, b.conditionId, b.outcome, t) ?? b.avgPx;
      const proceeds = b.shares * px;
      if (truth === undefined) {
        settlement.marked++;
        settlement.markedUsd += proceeds;
      } else {
        settlement.resolved++;
        settlement.resolvedUsd += proceeds;
      }
      // Relayer-paid, and a resolution is not a CLOB fill — no gas, no fee.
      cash += proceeds;
      settles++;
      book.delete(k);
      markers.push({
        t,
        side: "REDEEM",
        label: `${truth === undefined ? "SETTLE" : truth > 0 ? "REDEEM WIN" : "EXPIRED WORTHLESS"} · ${b.market}`,
        usd: round2(proceeds),
      });
      equityHistory.push({ t, liq: cash, pos: posValue(t) });
      lastSnapT = t;
    }
  };

  /** Sell a whole hold at `px` — the shape both protective exits take live
      (the engine sells the full position at the book's bid). */
  const liquidate = (t: number, k: string, b: Hold, px: number, label: string) => {
    const prevEquity = cash + posValue(t);
    const proceeds = b.shares * px;
    const realized = (px - b.avgPx) * b.shares;
    const fee = ledger.charge({
      conditionId: b.conditionId, market: b.market,
      shares: b.shares, price: px, notional: proceeds,
    });
    cash += proceeds - fee;
    volume += proceeds;
    book.delete(k);
    record(t, "SELL", b, proceeds, px, realized, prevEquity, `${label} · ${b.market}`, fee);
  };

  const cycleMs = Math.max(MIN_POLL_MINUTES, pollMinutes || MIN_POLL_MINUTES) * 60_000;
  // Cap the cycle count so a 30-day window at a 30s cadence doesn't run 86k
  // iterations in a browser render pass. Coarsening the cadence models a
  // SLOWER engine, which can only miss signals — never invent them.
  const MAX_CYCLES = 20_000;
  const span = Math.max(0, endMs - startMs);
  const step = Math.max(cycleMs, Math.ceil(span / MAX_CYCLES / 1000) * 1000);

  for (let t = startMs; t <= endMs; t += step) {
    redeem(t);

    // ── Protective exits (live: check_stop_losses, every scan) ──
    if (takeProfitFrac > 0 || stopLossPct > 0) {
      for (const [k, b] of [...book]) {
        const mark = legMark(tape, byCondition, b.conditionId, b.outcome, t);
        if (mark === null) continue;
        if (takeProfitFrac > 0 && takeProfitTriggered(mark, takeProfitFrac)) {
          liquidate(t, k, b, mark, "TAKE PROFIT");
          continue;
        }
        if (stopLossPct > 0 && stopLossTriggered(b.avgPx, mark, (100 - stopLossPct) / 100)) {
          liquidate(t, k, b, mark, "STOP LOSS");
        }
      }
    }

    // ── What the strat can see at this instant ──
    const visible: MarketPriceSeries[] = [];
    for (const s of tape.series) {
      // A market that has already closed is not in the live feed: candle mode
      // resolves the slug of the candle running NOW, search mode filters
      // `active`. Either way a strat never sees a settled market.
      if (s.endDateMs !== undefined && s.endDateMs <= t) continue;
      if (candlePeriodMs > 0 && s.endDateMs !== undefined && s.endDateMs - candlePeriodMs > t) {
        continue; // this candle hasn't started yet
      }
      const n = visibleCount(s.points, t);
      if (n < 2) continue;
      visible.push({ ...s, points: s.points.slice(0, n) });
    }
    if (visible.length === 0) {
      if (t - lastSnapT >= SNAP_MS) { lastSnapT = t; equityHistory.push({ t, liq: cash, pos: posValue(t) }); }
      continue;
    }

    // Positions as the engine reports them — the strat's exit logic reads
    // `size`, `conditionId`, `outcome`, `currentPrice` and `value` off this.
    const positions: PolymarketPosition[] = [];
    for (const [, b] of book) {
      const mark = legMark(tape, byCondition, b.conditionId, b.outcome, t) ?? b.avgPx;
      positions.push({
        conditionId: b.conditionId,
        tokenId: "",
        market: b.market,
        outcome: b.outcome,
        size: b.shares,
        avgPrice: b.avgPx,
        currentPrice: mark,
        value: round2(b.shares * mark),
        pnlUsd: round2((mark - b.avgPx) * b.shares),
        negRisk: false,
        redeemable: false,
      });
    }

    // Live sizes proposals against FREE CAPITAL — the allocation minus the
    // basis already deployed — not against the wallet's cash (live_engine
    // `execute_momentum`). Mirroring that here keeps the entry gate identical.
    let freeCapital = capital;
    for (const b of book.values()) freeCapital -= b.shares * b.avgPx;
    freeCapital = Math.max(0, freeCapital);

    const history: StratHistory = {
      trades: [],
      traderStats: {},
      positions,
      balance: cash,
      capital,
      watchlist: [],
      cycle: Math.round((t - startMs) / step),
      now: t,
      marketPrices: visible,
    };

    let proposals: ProposedTrade[] = [];
    try {
      proposals = strat.propose(history, {
        userFloor: minTrade,
        userCeiling: maxTrade > 0 ? maxTrade : Number.POSITIVE_INFINITY,
        clobFloor: POLYMARKET_MIN_USD,
        capital,
      });
    } catch {
      proposals = []; // a throwing hook idles the cycle live too
    }
    if (proposals.length === 0) {
      if (t - lastSnapT >= SNAP_MS) { lastSnapT = t; equityHistory.push({ t, liq: cash, pos: posValue(t) }); }
      continue;
    }

    funnel.observed += proposals.length;
    // Exits first — freed capital funds this cycle's entries (live sorts the
    // same way before placing anything).
    const ordered = [...proposals].sort((a, b) => (a.side === "SELL" ? 0 : 1) - (b.side === "SELL" ? 0 : 1));
    const capped = ordered.slice(0, Math.max(1, strat.maxPerCycle()));
    funnel.outranked += ordered.length - capped.length;
    if (ordered.length > capped.length) {
      tally(funnel, `MAX/CYCLE cap (${strat.maxPerCycle()})`, ordered.length - capped.length);
    }

    for (const p of capped) {
      const dedupKey = `${p.conditionId.toLowerCase()}:${(p.outcome || "Yes").toLowerCase()}:${p.side}`;
      if (t - (cooldown.get(dedupKey) ?? -Infinity) < PROPOSAL_COOLDOWN_MS) {
        funnel.gated++;
        tally(funnel, "signal cooldown");
        continue;
      }
      const limitPrice = tickRoundPrice(p.limitPrice);
      if (!(p.notional > 0) || !(limitPrice > 0)) continue;
      const key = legKey(p.conditionId, p.outcome || "Yes");
      const prevEquity = cash + posValue(t);
      // The price the tape says this outcome was trading at when the order
      // went in — the fill, when the limit is marketable against it.
      const mark = legMark(tape, byCondition, p.conditionId, p.outcome || "Yes", t);
      if (mark === null) {
        skipped++;
        tally(funnel, "no price at order time");
        continue;
      }

      if (p.side === "SELL") {
        const b = book.get(key);
        if (!b || b.shares <= 1e-9) {
          skipped++;
          tally(funnel, "exit with nothing held");
          continue;
        }
        if (limitPrice > mark + 1e-9) {
          // Asking above the market — the order rests unfilled, which live
          // would also do (and re-propose after the cooldown).
          skipped++;
          tally(funnel, "exit limit above market");
          continue;
        }
        cooldown.set(dedupKey, t);
        const proceeds = b.shares * mark;
        const realized = (mark - b.avgPx) * b.shares;
        const exitFee = ledger.charge({
          conditionId: b.conditionId, market: b.market,
          shares: b.shares, price: mark, notional: proceeds,
        });
        cash += proceeds - exitFee;
        volume += proceeds;
        book.delete(key);
        record(t, "SELL", b, proceeds, mark, realized, prevEquity,
          `${p.reason ?? "EXIT"} · ${b.market}`, exitFee);
        continue;
      }

      // ENTRY. Live rounds UP to whole-ish shares with a `minShares` floor, so
      // the SIZE is set by the limit price — at a $1 proposal and 60¢ that is 5
      // shares (the floor), not $1 worth. A sim that spent the proposal's
      // number would under-report both the risk and the position count. What
      // those shares COST is the fill price, which is the market's.
      if (limitPrice < mark - 1e-9) {
        skipped++;
        tally(funnel, "entry limit below market");
        continue;
      }
      const notionalFloor = Math.max(p.notional, clobMinNotional(limitPrice));
      const size = round2(Math.max(Math.ceil(notionalFloor / limitPrice), minShares));
      const notional = size * mark;
      const held = book.get(key);
      if (!held && book.size >= maxOpenPositions) {
        skipped++; funnel.skipped++;
        tally(funnel, `MAX POS cap (${maxOpenPositions})`);
        continue;
      }
      if (notional > freeCapital + 1e-6) {
        skipped++; funnel.skipped++;
        tally(funnel, "no free capital");
        cooldown.set(dedupKey, t); // live marks the signal acted-on either way
        continue;
      }
      // The matcher debits `notional + taker fee`, so the fee has to fit too.
      const fee = ledger.quote(p.conditionId, size, mark, p.market, undefined);
      if (notional + fee > cash + 1e-6) {
        skipped++; funnel.skipped++;
        tally(funnel, "out of cash");
        continue;
      }
      cooldown.set(dedupKey, t);
      ledger.charge({
        conditionId: p.conditionId, market: p.market,
        shares: size, price: mark, notional,
      });
      cash -= notional + fee;
      freeCapital -= notional;
      volume += notional;
      const b = held ?? {
        shares: 0, avgPx: 0, market: p.market || p.conditionId,
        conditionId: p.conditionId, outcome: p.outcome || "Yes",
        endMs: byCondition.get(p.conditionId.toLowerCase())?.endDateMs,
        openedAt: t,
      };
      const newShares = b.shares + size;
      b.avgPx = newShares > 0 ? (b.avgPx * b.shares + mark * size) / newShares : 0;
      b.shares = newShares;
      book.set(key, b);
      record(t, "BUY", b, notional, mark, 0, prevEquity,
        `${p.reason ?? "ENTRY"} · ${b.market} @ ${Math.round(mark * 100)}¢`, fee);
    }
  }

  // Final sweep: every candle in the window has closed by now, so anything
  // still held redeems at its resolution (or, if gamma hasn't published one,
  // at its last mark — counted as a guess in `settlement`).
  redeem(endMs);

  const nowPos = posValue(endMs);
  equityHistory.push({ t: endMs, liq: cash, pos: nowPos });

  const open: PerfPosition[] = [];
  let unrealized = 0;
  let costBasis = 0;
  for (const [k, b] of book) {
    if (b.shares <= 1e-9) continue;
    const mark = tape.resolved.get(k)
      ?? legMark(tape, byCondition, b.conditionId, b.outcome, endMs)
      ?? b.avgPx;
    const pnlUsd = round2(b.shares * (mark - b.avgPx));
    unrealized += pnlUsd;
    costBasis += b.avgPx * b.shares;
    open.push({
      key: k,
      market: b.market,
      outcome: legOutcome(k).toUpperCase() || "ORIGINATED",
      size: b.shares,
      avgPrice: b.avgPx,
      curPrice: mark,
      value: round2(b.shares * mark),
      pnlUsd,
      rowTitle: `${b.market} · simulated hold · entry ${Math.round(b.avgPx * 100)}¢`,
    });
  }
  open.sort((a, b) => a.pnlUsd - b.pnlUsd);

  // Per DEPLOYMENT, not per trade — see the note in lib/fees.ts. `settles`
  // stays counted because every one of them was a relayer-paid redeem.
  const gas = sessionGasUsd(gasOps, gasQuote);
  const fees = ledger.fees;
  const netPnl = round2(cash + nowPos - capital);
  funnel.executed = rows.filter((r) => r.side === "BUY").length;

  return {
    rows,
    equityHistory,
    markers,
    skipped,
    funnel,
    netPnl,
    grossPnl: round2(netPnl + fees + gas),
    fees: round2(fees),
    gas,
    costs: ledger.breakdown(gasOps, netPnl + fees + gas),
    volume: round2(volume),
    cash: round2(cash),
    posValue: round2(nowPos),
    unrealized: round2(unrealized),
    costBasis: round2(costBasis),
    open,
    settlement: {
      resolved: settlement.resolved,
      resolvedUsd: round2(settlement.resolvedUsd),
      marked: settlement.marked,
      markedUsd: round2(settlement.markedUsd),
    },
  };
}

/** Merge a copy replay and an origination replay into one result.
 *
 *  A strat can do both (a watchlist AND `momentum`). Live they share one
 *  wallet; here they are two passes, so the merge sums the P&L and interleaves
 *  the rows rather than pretending a single book. Each sleeve was sized against
 *  the full capital, so a strat that really runs both would deploy more than a
 *  live session could — the note says so rather than hiding it. Strats that do
 *  only one of the two (every shipped template) never take this path. */
export function mergeSims(copy: BacktestSim, orig: BacktestSim, capital: number): BacktestSim {
  if (orig.rows.length === 0 && orig.markers.length === 0) return copy;
  if (copy.rows.length === 0 && copy.markers.length === 0) return orig;
  const rows = [...copy.rows, ...orig.rows].sort((a, b) => a.ts - b.ts);
  const markers = [...copy.markers, ...orig.markers].sort((a, b) => a.t - b.t);
  // Equity: both curves start at `capital`, so the combined equity at any
  // instant is capital + (copy − capital) + (orig − capital).
  const equityHistory = [...copy.equityHistory, ...orig.equityHistory]
    .sort((a, b) => a.t - b.t);
  const funnel: EntryFunnel = {
    observed: copy.funnel.observed + orig.funnel.observed,
    gated: copy.funnel.gated + orig.funnel.gated,
    outranked: copy.funnel.outranked + orig.funnel.outranked,
    executed: copy.funnel.executed + orig.funnel.executed,
    skipped: copy.funnel.skipped + orig.funnel.skipped,
    reasons: { ...copy.funnel.reasons },
  };
  for (const [k, v] of Object.entries(orig.funnel.reasons)) {
    funnel.reasons[k] = (funnel.reasons[k] ?? 0) + v;
  }
  return {
    rows,
    equityHistory,
    markers,
    skipped: copy.skipped + orig.skipped,
    funnel,
    netPnl: round2(copy.netPnl + orig.netPnl),
    grossPnl: round2(copy.grossPnl + orig.grossPnl),
    fees: round2(copy.fees + orig.fees),
    gas: copy.gas + orig.gas,
    costs: mergeCostBreakdowns(copy.costs, orig.costs, copy.grossPnl + orig.grossPnl),
    volume: round2(copy.volume + orig.volume),
    cash: round2(copy.cash + orig.cash - capital),
    posValue: round2(copy.posValue + orig.posValue),
    unrealized: round2(copy.unrealized + orig.unrealized),
    costBasis: round2(copy.costBasis + orig.costBasis),
    open: [...copy.open, ...orig.open].sort((a, b) => a.pnlUsd - b.pnlUsd),
    settlement: {
      resolved: copy.settlement.resolved + orig.settlement.resolved,
      resolvedUsd: round2(copy.settlement.resolvedUsd + orig.settlement.resolvedUsd),
      marked: copy.settlement.marked + orig.settlement.marked,
      markedUsd: round2(copy.settlement.markedUsd + orig.settlement.markedUsd),
    },
  };
}
