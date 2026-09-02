// THE BASKET — copy a SET of traders, with a different amount against each,
// and replay the whole thing over the last N days.
//
// The desk already answers "what would $N behind THIS trader have done"
// (components/CopySimPanel.tsx, one leader). What it could not answer is the
// question anyone actually allocating asks: I have five names and $2,000 —
// how do I split it, and what does the SPLIT do? That is not the sum of five
// profile pages, because the sizes interact with the gates:
//
//   • $50 behind a whale copies NOTHING. The proportional mirror lands under
//     the CLOB's order floor and every entry is refused, so a fifth of your
//     bankroll sits in a sleeve that never places. The basket has to say which
//     legs are below their own floor, by name, before you fund them.
//   • $2,000 behind a small leader copies them at ×40 and rides the MAX TRADE
//     cap on every fill — more money, same book, worse ratio.
//
// So a basket is not one number, it's a shape: which legs earned, which legs
// were structurally unable to trade, and whether the amounts you chose beat
// simply splitting the money evenly.
//
// ── SLEEVES, NOT A POOL ────────────────────────────────────────────────────
// Each leg is replayed on its OWN capital and the results are summed. That is
// not a modelling shortcut, it's what the deployment does: one allocation is
// one live session keyed (eoa, `copy-<address>`), with its own budget, its own
// ledger bucket and its own `maxOpenPositions` (api/src/live_engine.rs). A
// pooled replay — one wallet, five leaders competing for cash — would be a
// prettier model of a thing this console does not run, and its per-leg numbers
// would depend on the order the leaders happened to fill in.
//
// The one thing sleeves DON'T model is the shared USDC balance: five sessions
// draw on one wallet, so the desk can promise $2,000 of allocations against
// $500 of funds. That's a funding fact, not a strategy fact — `CopyBook.totals`
// already reports it, and `basketFunding()` below repeats it here.
//
// Nothing in this file touches React, the network or localStorage: the panel
// (components/BasketSim.tsx) and the server route (app/api/basket/route.ts)
// both call it with feeds already in hand, exactly like lib/hubReplay.ts.

import {
  runBacktest, stratFromIndex, stratBacktestParams, settlementConfidence,
  type BacktestResult,
} from "./backtest";
import { emptyNote } from "./hubReplay";
import { identityStrat, shortAddress, type Allocation, type AllocationParams } from "./identityStrat";
import type { EquityMarker, EquitySnapshot } from "../components/EquityChart";
import type { PolymarketPosition, PolymarketTrade, SavedIndex, TradeFilters } from "./types";

/** The sizes a floor search walks. Straddles the order floor deliberately:
    under ~$25 a proportional mirror of most leaders lands below
    `clobMinNotional` and simply doesn't place. Shared with CopySimPanel's
    ladder so the two screens agree on what "small" means. */
export const BASKET_LADDER = [10, 25, 50, 100, 250, 500, 1000, 2500];

/** Total-basket sizes the "how big does this basket need to be" ladder walks,
    holding your split fixed. */
export const BASKET_TOTALS = [100, 250, 500, 1000, 2500, 5000, 10000];

/** Merged portfolio curves are capped at this many points — five sleeves over
    30 days is tens of thousands of snapshots and the chart draws one line. */
const MAX_CURVE_POINTS = 1500;

// ── Inputs ─────────────────────────────────────────────────────────────────

/** One line of the basket: a trader, and the dollars behind THEM. `params`
    is the same per-allocation patch the copy book stores, so a leg can carry
    its own gates (a tighter MAX TRADE on the whale, a topic filter on the
    generalist) and the replay honours them per leg. */
export interface BasketLeg {
  address: string;
  allocationUsd: number;
  label?: string | null;
  /** Off legs stay in the roster (and in the URL you share) but are not
      replayed and contribute no capital. */
  enabled?: boolean;
  params?: AllocationParams;
}

/** Everything the replay needs that has to be fetched. Supplied by the caller
    so the same function runs in the browser (fetch) and in the worker (disk
    feed store). Addresses are matched case-insensitively. */
export interface BasketFeeds {
  trades: Map<string, PolymarketTrade[]>;
  positions: Map<string, PolymarketPosition[]>;
  /** lowercased addr → bankroll USD; missing falls back to the volume model
      inside `copyRatioFor`, exactly as every other surface. */
  bankrolls: Map<string, number>;
  /** leg key → resolution price. Without it the replay values dead inventory
      at the last price a leader printed, which forgives losers. */
  resolved?: Map<string, number>;
}

export interface BasketOptions {
  days: number;
  /** Board-level side/price/size dimensions, applied to every leg. Per-leg
      topic gates live in `leg.params.marketQuery`. */
  tradeFilters?: TradeFilters | null;
  /** Window end (ms). Defaults to now; set it back for a walk-forward. */
  asOf?: number;
}

// ── One sleeve ─────────────────────────────────────────────────────────────

function feedFor<T>(map: Map<string, T[]>, address: string): T[] {
  return map.get(address) ?? map.get(address.toLowerCase()) ?? [];
}

/** The strat this leg materializes into — the object the live engine would
    run for it, plus the board's per-trade dimensions. Exported because the
    panel shows it and the COMMIT path writes the same params to the book. */
export function legStrat(leg: BasketLeg, tradeFilters?: TradeFilters | null): SavedIndex {
  const alloc: Allocation = {
    address: leg.address,
    label: leg.label ?? null,
    allocationUsd: leg.allocationUsd,
    enabled: leg.enabled !== false,
    params: leg.params,
    addedAt: Date.now(),
    updatedAt: Date.now(),
  };
  return {
    ...identityStrat(alloc),
    ...(tradeFilters ? { tradeFilters } : {}),
  };
}

/** Replay ONE leg at ONE size. `capital` overrides the leg's own amount —
    that is what the floor search and the total-ladder vary. */
export function replaySleeve(
  leg: BasketLeg,
  feeds: BasketFeeds,
  opts: BasketOptions,
  capital = leg.allocationUsd,
): BacktestResult | null {
  if (!(capital > 0)) return null;
  const trades = feedFor(feeds.trades, leg.address);
  if (trades.length === 0) return null;
  const idx = legStrat({ ...leg, allocationUsd: capital }, opts.tradeFilters);
  const p = stratBacktestParams(idx);
  return runBacktest({
    watchlist: [leg.address],
    traderTrades: new Map([[leg.address, trades]]),
    traderPositions: new Map([[leg.address, feedFor(feeds.positions, leg.address)]]),
    traderWeights: { [leg.address]: 1 },
    traderBankrolls: feeds.bankrolls,
    strat: stratFromIndex(idx),
    days: opts.days,
    capital,
    minTrade: p.minTrade,
    maxTrade: p.maxTrade,
    maxOpenPositions: p.maxOpenPositions,
    stopLossPct: p.stopLossPct,
    takeProfitFrac: p.takeProfitFrac,
    marketQuery: p.marketQuery,
    pollMinutes: p.pollMinutes,
    sizing: idx.sizing,
    turnover: idx.turnover,
    resolved: feeds.resolved,
    ...(opts.asOf ? { asOf: opts.asOf } : {}),
  });
}

/** One leg's result, flattened to what a table row needs. */
export interface Sleeve {
  address: string;
  label: string;
  allocationUsd: number;
  /** Share of the basket's funded capital this leg holds (0–1). */
  weight: number;
  net: number;
  /** Return ON THIS LEG — net / its own capital. Not the basket's return. */
  pct: number;
  endEquity: number;
  volume: number;
  /** Polymarket taker fees this sleeve paid, and what they came to in basis
      points of the notional it traded. A leader whose flow lives in 7% crypto
      markets is a more expensive sleeve than one in fee-free geopolitics at
      the same P&L — this is where that shows up. */
  fees: number;
  feeBps: number;
  trades: number;
  /** Leader BUYs the gate saw, and how many were actually mirrored. */
  observed: number;
  executed: number;
  skipped: number;
  /** Mirror size as a fraction of the leader's own trade (≥1 ⇒ capped by
      MAX TRADE on every fill). */
  ratio: number;
  drawdown: number;
  /** 0–1: how much of the exit value is a real resolution rather than a mark. */
  confidence: number;
  /** Set when the leg executed nothing — the dominant gate, in words. */
  note?: string;
  /** conditionIds this sleeve actually traded — the overlap check reads it. */
  markets: string[];
  /** Retained for the portfolio merge; thinned before it crosses the wire. */
  equity: EquitySnapshot[];
  markers: EquityMarker[];
}

/** Peak-to-trough of an equity curve, as a % of the peak. */
export function maxDrawdown(history: { liq: number; pos: number }[]): number {
  let peak = 0;
  let worst = 0;
  for (const p of history) {
    const eq = p.liq + p.pos;
    if (eq > peak) peak = eq;
    if (peak > 0) worst = Math.min(worst, (eq - peak) / peak);
  }
  return worst * 100;
}

export function sleeveName(leg: BasketLeg): string {
  return (leg.label ?? "").trim() || shortAddress(leg.address);
}

/** Flatten a leg + its replay into a row. A leg with no feed (a trader the
    worker has never fetched) comes back as an all-zero sleeve carrying the
    reason, never as a $0 that reads like breaking even. */
export function summarizeSleeve(
  leg: BasketLeg,
  result: BacktestResult | null,
  totalCapital: number,
): Sleeve {
  const name = sleeveName(leg);
  const weight = totalCapital > 0 ? leg.allocationUsd / totalCapital : 0;
  if (!result) {
    return {
      address: leg.address, label: name, allocationUsd: leg.allocationUsd, weight,
      net: 0, pct: 0, endEquity: leg.allocationUsd, volume: 0, fees: 0, feeBps: 0, trades: 0,
      observed: 0, executed: 0, skipped: 0, ratio: 0, drawdown: 0, confidence: 1,
      note: leg.allocationUsd > 0 ? "no trade history for this leader yet" : "no dollars behind this leg",
      markets: [], equity: [], markers: [],
    };
  }
  const sim = result.sim;
  const markets = [...new Set(sim.rows.map((r) => r.conditionId).filter((c): c is string => !!c))];
  return {
    address: leg.address,
    label: name,
    allocationUsd: leg.allocationUsd,
    weight,
    net: sim.netPnl,
    pct: leg.allocationUsd > 0 ? (sim.netPnl / leg.allocationUsd) * 100 : 0,
    endEquity: sim.cash + sim.posValue,
    volume: sim.volume,
    fees: sim.fees,
    feeBps: sim.costs.effectiveBps,
    trades: sim.rows.length,
    observed: sim.funnel.observed,
    executed: sim.funnel.executed,
    skipped: sim.skipped,
    ratio: result.copyRatio.get(leg.address) ?? 0,
    drawdown: maxDrawdown(sim.equityHistory),
    confidence: settlementConfidence(sim.settlement),
    ...(sim.rows.length === 0 ? { note: emptyNote(sim.funnel) } : {}),
    markets,
    equity: sim.equityHistory,
    markers: sim.markers.map((m) => ({ ...m, label: m.label ? `${name} · ${m.label}` : name })),
  };
}

// ── The portfolio ──────────────────────────────────────────────────────────

/** Sum sleeve curves onto one timeline.
 *
 *  Each sleeve is sampled at its own trade times, so a naive concat would draw
 *  a saw: sleeve A's snapshot would be plotted against sleeve B's stale value.
 *  Instead every distinct timestamp becomes a portfolio point and each sleeve
 *  is STEP-HELD at its last known state — which is exactly what its wallet was
 *  doing between its own trades. Before a sleeve's first snapshot it holds its
 *  full allocation in cash, so the basket starts at the total you funded and
 *  the curve never begins below its own capital. */
export function mergeEquity(
  sleeves: { equity: EquitySnapshot[]; allocationUsd: number }[],
  maxPoints = MAX_CURVE_POINTS,
): EquitySnapshot[] {
  const live = sleeves.filter((s) => s.equity.length > 0);
  if (live.length === 0) return [];

  const times = [...new Set(live.flatMap((s) => s.equity.map((p) => p.t)))].sort((a, b) => a - b);
  const cursor = live.map(() => 0);
  const out: EquitySnapshot[] = [];
  for (const t of times) {
    let liq = 0;
    let pos = 0;
    live.forEach((s, i) => {
      while (cursor[i] + 1 < s.equity.length && s.equity[cursor[i] + 1].t <= t) cursor[i]++;
      const p = s.equity[cursor[i]];
      // Not started yet ⇒ still all cash, at its own allocation.
      if (p.t > t) { liq += s.allocationUsd; return; }
      liq += p.liq;
      pos += p.pos;
    });
    out.push({ t, liq, pos });
  }
  // Cash sleeves that never traded (no snapshots at all) are flat capital for
  // the whole window — add them once rather than materializing a curve.
  const idle = sleeves.filter((s) => s.equity.length === 0)
    .reduce((sum, s) => sum + s.allocationUsd, 0);
  if (idle > 0) for (const p of out) p.liq += idle;

  return thin(out, maxPoints);
}

/** Keep the first, the last and an even sample between — the extremes of the
    curve survive because the endpoints carry the P&L the header reports. */
function thin(points: EquitySnapshot[], maxPoints: number): EquitySnapshot[] {
  if (points.length <= maxPoints) return points;
  const step = points.length / maxPoints;
  const out: EquitySnapshot[] = [];
  for (let i = 0; i < maxPoints - 1; i++) out.push(points[Math.floor(i * step)]);
  out.push(points[points.length - 1]);
  return out;
}

export interface BasketPortfolio {
  capital: number;
  net: number;
  /** Return on the WHOLE basket — net / total funded capital. */
  pct: number;
  endEquity: number;
  volume: number;
  /** Taker fees across every sleeve, and what they came to in basis points of
      the basket's traded notional. */
  fees: number;
  feeBps: number;
  trades: number;
  observed: number;
  executed: number;
  skipped: number;
  drawdown: number;
  /** Capital-weighted settlement confidence: how much of the basket's result
      is a real resolution rather than a last-observed mark. */
  confidence: number;
  /** Legs that placed at least one order. The gap between this and the roster
      size is the headline honesty number. */
  legsTrading: number;
  legs: number;
  /** Capital sitting in legs that executed nothing — money the basket did not
      put to work, in dollars. */
  idleUsd: number;
  equity: EquitySnapshot[];
  markers: EquityMarker[];
}

/** How much of the basket rode on how few names. Herfindahl over the legs'
    P&L contributions — 1 means one leg made all of it. */
export function contributionConcentration(sleeves: Sleeve[]): number {
  const gross = sleeves.reduce((s, x) => s + Math.abs(x.net), 0);
  if (gross <= 0) return 0;
  return sleeves.reduce((s, x) => s + (Math.abs(x.net) / gross) ** 2, 0);
}

/** Markets more than one leg traded. Two sleeves on the same market are two
    real positions — the basket is not as diversified as its name count, and
    the live desk would place both orders. */
export function basketOverlap(sleeves: Sleeve[]): {
  markets: number;
  legsPaired: number;
  worst: { market: string; legs: string[] } | null;
} {
  const by = new Map<string, string[]>();
  for (const s of sleeves) {
    for (const m of s.markets) {
      const arr = by.get(m) ?? [];
      arr.push(s.label);
      by.set(m, arr);
    }
  }
  let markets = 0;
  let worst: { market: string; legs: string[] } | null = null;
  const paired = new Set<string>();
  for (const [market, legs] of by) {
    if (legs.length < 2) continue;
    markets++;
    for (const l of legs) paired.add(l);
    if (!worst || legs.length > worst.legs.length) worst = { market, legs };
  }
  return { markets, legsPaired: paired.size, worst };
}

export function assemblePortfolio(sleeves: Sleeve[]): BasketPortfolio {
  const capital = sleeves.reduce((s, x) => s + x.allocationUsd, 0);
  const net = sleeves.reduce((s, x) => s + x.net, 0);
  const equity = mergeEquity(sleeves);
  const traded = sleeves.filter((s) => s.trades > 0);
  // Weight each leg's confidence by the value it actually settled — a leg that
  // traded nothing is neither honest nor dishonest about its tail.
  const confWeight = traded.reduce((s, x) => s + Math.abs(x.net) + x.allocationUsd, 0);
  return {
    capital,
    net,
    pct: capital > 0 ? (net / capital) * 100 : 0,
    endEquity: capital + net,
    volume: sleeves.reduce((s, x) => s + x.volume, 0),
    fees: sleeves.reduce((s, x) => s + x.fees, 0),
    feeBps: (() => {
      const vol = sleeves.reduce((s, x) => s + x.volume, 0);
      const fee = sleeves.reduce((s, x) => s + x.fees, 0);
      return vol > 0 ? (fee / vol) * 10_000 : 0;
    })(),
    trades: sleeves.reduce((s, x) => s + x.trades, 0),
    observed: sleeves.reduce((s, x) => s + x.observed, 0),
    executed: sleeves.reduce((s, x) => s + x.executed, 0),
    skipped: sleeves.reduce((s, x) => s + x.skipped, 0),
    drawdown: maxDrawdown(equity),
    confidence: confWeight > 0
      ? traded.reduce((s, x) => s + x.confidence * (Math.abs(x.net) + x.allocationUsd), 0) / confWeight
      : 1,
    legsTrading: traded.length,
    legs: sleeves.length,
    idleUsd: sleeves.filter((s) => s.trades === 0).reduce((s, x) => s + x.allocationUsd, 0),
    equity,
    markers: sleeves.flatMap((s) => s.markers).sort((a, b) => a.t - b.t),
  };
}

// ── The whole run, in one call ─────────────────────────────────────────────

export interface BasketRun {
  days: number;
  portfolio: BasketPortfolio;
  sleeves: Sleeve[];
  overlap: ReturnType<typeof basketOverlap>;
  concentration: number;
}

/** Replay every enabled leg and assemble the basket. Synchronous and pure —
    the panel drives it a leg at a time (so a ten-name basket paints as it
    goes) via `replaySleeve` + `summarizeSleeve`; the server route and the
    tests call this. */
export function runBasketSim(
  legs: BasketLeg[],
  feeds: BasketFeeds,
  opts: BasketOptions,
): BasketRun {
  const active = legs.filter((l) => l.enabled !== false && l.allocationUsd > 0);
  const total = active.reduce((s, l) => s + l.allocationUsd, 0);
  const sleeves = active.map((leg) =>
    summarizeSleeve(leg, replaySleeve(leg, feeds, opts), total));
  return {
    days: opts.days,
    portfolio: assemblePortfolio(sleeves),
    sleeves,
    overlap: basketOverlap(sleeves),
    concentration: contributionConcentration(sleeves),
  };
}

// ── Splits ─────────────────────────────────────────────────────────────────

/** Round to cents, never below zero. */
function money(v: number): number {
  return Math.max(0, Math.round(v * 100) / 100);
}

/** Same dollars to every enabled leg. */
export function equalSplit(legs: BasketLeg[], total: number): BasketLeg[] {
  const n = legs.filter((l) => l.enabled !== false).length;
  if (n === 0) return legs;
  const each = money(total / n);
  return legs.map((l) => (l.enabled === false ? l : { ...l, allocationUsd: each }));
}

/** Rescale the amounts you already chose to a new total — conviction kept,
    size changed. This is `POST /copy/rebalance weighted` on a draft. */
export function weightedSplit(legs: BasketLeg[], total: number): BasketLeg[] {
  const active = legs.filter((l) => l.enabled !== false);
  const sum = active.reduce((s, l) => s + l.allocationUsd, 0);
  if (sum <= 0) return equalSplit(legs, total);
  return legs.map((l) =>
    l.enabled === false ? l : { ...l, allocationUsd: money((l.allocationUsd / sum) * total) });
}

/** Split by a per-leg score (leader P&L, a previous run's net, anything the
    caller can defend). Negative and missing scores get nothing but stay in the
    roster at $0 — a leg you can see is worth more than a leg silently dropped. */
export function scoreSplit(
  legs: BasketLeg[],
  total: number,
  scoreOf: (leg: BasketLeg) => number,
): BasketLeg[] {
  const active = legs.filter((l) => l.enabled !== false);
  const scores = new Map(active.map((l) => [l.address, Math.max(0, scoreOf(l) || 0)]));
  const sum = [...scores.values()].reduce((s, v) => s + v, 0);
  if (sum <= 0) return equalSplit(legs, total);
  return legs.map((l) =>
    l.enabled === false ? l : { ...l, allocationUsd: money((scores.get(l.address) ?? 0) / sum * total) });
}

export function basketTotal(legs: BasketLeg[]): number {
  return legs.filter((l) => l.enabled !== false).reduce((s, l) => s + l.allocationUsd, 0);
}

// ── The two questions a split has to survive ───────────────────────────────

/** The smallest ladder rung at which this leg copies ANYTHING.
 *
 *  This is the number that makes a basket different from a list of profiles.
 *  Below it the leg is not a small position, it is no position: every mirror
 *  lands under the order floor and the money sits in cash for the whole
 *  window. Returns null when even the top of the ladder executes nothing (the
 *  leader is un-copyable under this gate, at any size) and 0 when the leg
 *  already trades at its current amount. */
export function sleeveFloor(
  leg: BasketLeg,
  feeds: BasketFeeds,
  opts: BasketOptions,
  ladder: number[] = BASKET_LADDER,
): number | null {
  for (const rung of [...ladder].sort((a, b) => a - b)) {
    const r = replaySleeve(leg, feeds, opts, rung);
    if (r && r.sim.funnel.executed > 0) return rung;
  }
  return null;
}

/** Replay the WHOLE basket at a different total, holding your split fixed.
    Copying is not linear in N on one leader and it is not linear on five —
    this is where "$500 across these five does nothing, $2,500 works" shows up. */
export function basketAtTotal(
  legs: BasketLeg[],
  feeds: BasketFeeds,
  opts: BasketOptions,
  total: number,
): BasketRun {
  return runBasketSim(weightedSplit(legs, total), feeds, opts);
}

/** Did choosing different amounts beat splitting evenly?
 *
 *  The counterfactual is the same total, same legs, same window, same gates —
 *  only the split changes. If it does not beat EQUAL, the conviction in your
 *  sizing did not pay for itself in this window, and the panel says so rather
 *  than letting a good total launder a bad split. */
export interface SplitComparison {
  chosen: number;
  equal: number;
  /** chosen − equal, in dollars. */
  edge: number;
  /** Legs whose amount differs from the equal-split amount by >5%. */
  differs: number;
}

export function compareToEqualSplit(
  legs: BasketLeg[],
  feeds: BasketFeeds,
  opts: BasketOptions,
  chosenNet: number,
): SplitComparison {
  const total = basketTotal(legs);
  const even = equalSplit(legs, total);
  const equalNet = runBasketSim(even, feeds, opts).portfolio.net;
  const each = even.find((l) => l.enabled !== false)?.allocationUsd ?? 0;
  return {
    chosen: chosenNet,
    equal: equalNet,
    edge: chosenNet - equalNet,
    differs: legs.filter((l) =>
      l.enabled !== false && each > 0 && Math.abs(l.allocationUsd - each) / each > 0.05).length,
  };
}

// ── Funding ────────────────────────────────────────────────────────────────

/** The basket promises `total` of allocations against whatever the wallet
    actually holds. Sleeves are budgeted independently, so five funded sessions
    can collectively try to spend more USDC than exists — the first ones to
    fill win and the rest fail on balance. Purely informational; the replay
    does not model it (nor does the live desk prevent it). */
export function basketFunding(total: number, walletUsdc: number | null): {
  total: number;
  wallet: number | null;
  shortfall: number;
  overcommitted: boolean;
} {
  const wallet = walletUsdc === null || !Number.isFinite(walletUsdc) ? null : walletUsdc;
  const shortfall = wallet === null ? 0 : Math.max(0, total - wallet);
  return { total, wallet, shortfall, overcommitted: shortfall > 0 };
}
