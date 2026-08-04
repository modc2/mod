// The backtest engine — one simulated-wallet replay, shared by every surface
// that asks "what would this strat have done?".
//
// It used to live inside CopyIndex as a chain of useMemos, which meant only the
// open strat could be backtested: the STRAT HUB needed the same numbers for
// every saved strat at once, and re-deriving them in a second place is exactly
// how backtest and live drifted apart before (see lib/strats/parity.fixture.json).
// So the pipeline moved here as plain functions over explicit inputs:
//
//   traderStats → copyRatio → history → keptBuyIds → sim
//
// `runBacktest` runs all five and hands back every stage, because the console
// renders the intermediates too (trader stats table, capital plan). Nothing in
// here touches React, the network or localStorage — feed it trades and it
// replays them.

import { PolymarketPosition, PolymarketTrade, SavedIndex, TraderRoiStats } from "./types";
import {
  Strat, clobMinNotional, statsFromReturns, stopLossTriggered, takeProfitTriggered,
  successProbability, copyRatioFor, REBALANCE_MARGIN_PCT, MIN_POLL_MINUTES,
  DEFAULT_STOP_LOSS, DEFAULT_TAKE_PROFIT,
} from "./strats/strat";
import type { TraderTrade as StratTraderTrade, StratHistory } from "./strats/strat";
import { marketMatchesQuery } from "./marketQuery";
import { computeFifoTrades, aggregateToRebalanceWindows } from "./pnlEngine";
// Type-only: the sim reports its wallet in the same row shapes the LIVE panel
// renders, so one component draws both (never fork the markup).
import type { EquitySnapshot, EquityMarker } from "../components/EquityChart";
import type { PerfPosition } from "../components/PerfPanel";

// Polymarket costs — kept at LIVE-engine parity. The CLOB charges no
// taker/maker fee on these markets (the engine places every order with
// fee_rate_bps 0) and proxy-wallet trades are relayer-paid, i.e. gasless for
// the user. The sim books the same zero costs so the backtest curve and a
// live session measure the same thing; real friction (spread, unfilled GTC
// limits) is unmodeled on BOTH sides. Bump these only if Polymarket starts
// charging AND the live engine models the same charge.
export const TAKER_FEE_BPS = 0;
export const GAS_PER_TRADE_USD = 0;
export const DEFAULT_CAPITAL = 1000;

// Deterministic per-trade sample filter. Same (samplePct, key) always returns
// the same boolean — keeps the chart + feed in lockstep without re-sampling
// every render. Uses FNV-1a so a one-char tweak to the key reshuffles cleanly.
export function keepInSample(samplePct: number, key: string): boolean {
  if (samplePct >= 100) return true;
  if (samplePct <= 0) return false;
  let h = 2166136261;
  for (let i = 0; i < key.length; i++) {
    h ^= key.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return ((h >>> 0) % 100) < samplePct;
}

export interface LinkedTrade {
  ts: number;
  market: string;
  conditionId: string;
  trader: string;
  side: "BUY" | "SELL";
  amount: number;       // executed notional at user scale ($)
  price: number;        // trade price (0-1)
  fee: number;          // trading fee ($)
  realized: number;     // realized PnL on SELL vs avg entry (user scale)
  runningPnl: number;   // simulated equity − starting capital (MTM, net of costs)
  pnlDelta: number;     // equity change caused by this trade (mark move + costs)
  cash: number;         // simulated free cash after this trade
  pos: number;          // simulated open-position value after this trade
  /** Sharpe-weighted EV score the live engine would have assigned this
      candidate when it was eligible. Undefined for SELLs (always honored)
      and for BUYs from traders without enough 30d closed trades. */
  score?: number;
  sharpe?: number;
  /** P(success) the playbook priced this BUY at — the trader's smoothed
      30d win rate (0.5 = coin-flip prior). Undefined on exits. */
  successProb?: number;
}

export interface BacktestSim {
  rows: LinkedTrade[];
  equityHistory: EquitySnapshot[];
  markers: EquityMarker[];
  /** Trades dropped by the MIN size gate, the cash budget, or missing
      inventory — surfaced in the fee row so 0 TXS is explainable. */
  skipped: number;
  netPnl: number;    // final equity − capital (fees/gas already paid)
  grossPnl: number;  // netPnl before fees/gas
  fees: number;
  gas: number;
  volume: number;    // total executed notional
  /** Simulated wallet at the end of the replay — the same three numbers
      the LIVE panel reads off the real wallet, so both render identically
      through <PerfPanel>. */
  cash: number;
  posValue: number;
  unrealized: number;  // open sim positions marked vs avg entry
  costBasis: number;   // basis behind `unrealized`
  open: PerfPosition[];
}

export interface BacktestInput {
  /** Enabled trader addresses, in the strat's own order/casing. */
  watchlist: string[];
  /** addr → that trader's fetched trade history (≥ the backtest window). */
  traderTrades: Map<string, PolymarketTrade[]>;
  /** addr → that trader's current positions (supplies today's marks). */
  traderPositions: Map<string, PolymarketPosition[]>;
  /** addr → integer weight 0–100, the console's units. */
  traderWeights: Record<string, number>;
  /** lowercased addr → bankroll USD from the engine cache; missing entries
      fall back to the volume model inside `copyRatioFor`. */
  traderBankrolls: Map<string, number>;
  /** The SAME Strat class the live engine runs — what you backtest is what trades. */
  strat: Strat;
  days: number;
  capital: number;
  minTrade: number;
  maxTrade: number;
  maxOpenPositions: number;
  /** Integer PERCENT loss from entry that triggers the exit; 0 = off. */
  stopLossPct: number;
  /** Absolute mark level that triggers liquidation; 0 = off. */
  takeProfitFrac: number;
  marketQuery: string;
  /** Live poll cadence in minutes — one cycle = one top-N rank race. */
  pollMinutes: number;
  /** Legacy window aggregation; 0 = per-trade replay (the only mode now). */
  rebalancePeriod?: number;
  rebalanceHour?: number;
  /** Deterministically thin in-window trades to this % (100 = keep all). */
  samplePct?: number;
  /** Preview the unconstrained mirror — lifts every sizing/position gate. */
  showAllTrades?: boolean;
  /** True while trader data is still loading — the sim returns empty. */
  loading?: boolean;
}

export interface BacktestResult {
  traderStats: Map<string, TraderRoiStats>;
  copyRatio: Map<string, number>;
  history: StratHistory;
  keptBuyIds: Set<string>;
  sim: BacktestSim;
}

/** Sum of the weights of the traders actually being copied. */
function sumWeights(watchlist: string[], traderWeights: Record<string, number>): number {
  return watchlist.reduce((s, a) => s + (traderWeights[a] || 0), 0);
}

// ── Per-trader 30d ROI stats (drives top-N sampling) ──
// Computed from the same trade cache the backtest already loaded — no extra
// fetches. The shape MUST match what the live engine builds in
// fetchTraderRoiStats so the strat's scoreCandidate behaves identically in
// live + backtest.
export function computeTraderStats(
  watchlist: string[],
  traderTrades: Map<string, PolymarketTrade[]>,
  traderPositions: Map<string, PolymarketPosition[]>,
  marketQuery: string,
): Map<string, TraderRoiStats> {
  const out = new Map<string, TraderRoiStats>();
  const sharpeCutoffMs = Date.now() - 30 * 86400_000;
  for (const addr of watchlist) {
    const trades = (traderTrades.get(addr) || [])
      .filter((t) => marketMatchesQuery(t.market, marketQuery));
    const positions = traderPositions.get(addr) || [];
    const annotated = computeFifoTrades(trades, positions, sharpeCutoffMs);
    const inWin = annotated.filter((t) => t.timestamp >= sharpeCutoffMs);
    let cashDeployed = 0;
    const returns: number[] = [];
    for (const t of inWin) {
      if (t.side === "BUY") cashDeployed += t.price * t.size;
      if (t.side !== "SELL" || !t.hasBasis || !t.buyPrice) continue;
      const entryNotional = t.buyPrice * t.size;
      if (entryNotional <= 0) continue;
      returns.push(t.realized / entryNotional);
    }
    out.set(addr, {
      address: addr.toLowerCase(),
      windowDays: 30,
      ...statsFromReturns(returns),
      cashDeployed,
      syncedAt: Date.now(),
    });
  }
  return out;
}

// ── Per-trader copy ratio — the same `copyRatioFor` the live engine sizes
// with: the leader risked `notional / bankroll` of their net worth, so the
// strat risks that fraction of its capital, split by watchlist weight.
// Bankrolls come from the engine's own cache (`/live/bankroll`), so the
// preview and the live session divide by the same denominator; traders
// whose book can't be read fall back to the volume model.
//
// (This is also why copyRatio must never be 0 here: the backtest used to
// pass 0 into scoreCandidate, making every BUY score roi × notional × 0 = 0
// — and the top-N filter only keeps scores > 0, so NO buy ever survived and
// every backtest executed 0 trades.)
export function computeCopyRatios(input: BacktestInput): Map<string, number> {
  const { watchlist, traderTrades, traderWeights, traderBankrolls, days, capital, marketQuery } = input;
  const totalWeight = sumWeights(watchlist, traderWeights);
  const out = new Map<string, number>();
  const cutoff = Date.now() - days * 86400_000;
  for (const addr of watchlist) {
    let buyVol = 0, sellVol = 0;
    for (const t of traderTrades.get(addr) || []) {
      if (t.timestamp < cutoff || !marketMatchesQuery(t.market, marketQuery)) continue;
      const v = t.price * t.size;
      if (t.side === "BUY") buyVol += v; else sellVol += v;
    }
    const wFrac = totalWeight > 0 ? (traderWeights[addr] || 0) / totalWeight : 1 / watchlist.length;
    out.set(addr, copyRatioFor(
      capital,
      wFrac,
      traderBankrolls.get(addr.toLowerCase()),
      capital * wFrac,
      Math.max(buyVol, sellVol),
    ));
  }
  return out;
}

// ── Backtest StratHistory — same shape the live engine hands hooks ──
// Every strat hook takes the full observed history, so history-aware
// strats score identically in backtest and live. Positions/balance are
// unknowable in a backtest preview — empty/null by contract.
export function buildStratHistory(
  input: BacktestInput,
  traderStats: Map<string, TraderRoiStats>,
  copyRatio: Map<string, number>,
): StratHistory {
  const { watchlist, traderTrades, traderWeights, days, capital } = input;
  const totalW = sumWeights(watchlist, traderWeights) || 1;
  const windowCutoffMs = Date.now() - days * 86400_000;
  const trades: StratTraderTrade[] = [];
  const stats: Record<string, TraderRoiStats> = {};
  for (const addr of watchlist) {
    const weight = traderWeights[addr] || 0;
    const st = traderStats.get(addr);
    if (st) stats[addr.toLowerCase()] = st;
    for (const t of traderTrades.get(addr) || []) {
      if (t.timestamp < windowCutoffMs) continue;
      trades.push({
        ...t, trader: addr, weight, weightFraction: weight / totalW,
        copyRatio: copyRatio.get(addr) ?? 0, notional: t.price * t.size,
      });
    }
  }
  trades.sort((a, b) => b.timestamp - a.timestamp);
  return {
    trades,
    traderStats: stats,
    positions: [],
    balance: null,
    capital,
    watchlist: watchlist.map((a) => ({ address: a, weight: traderWeights[a] || 0 })),
    cycle: 0,
    now: Date.now(),
  };
}

// ── Top-N sampling: which BUY IDs survive the strat's filter ──
// Goes through the SAME strat class the live engine uses. Swapping the strat
// updates BOTH live behavior AND this backtest preview — no separate inline
// math to keep in sync.
export function computeKeptBuyIds(
  input: BacktestInput,
  traderStats: Map<string, TraderRoiStats>,
  copyRatio: Map<string, number>,
  history: StratHistory,
): Set<string> {
  const { watchlist, traderTrades, traderWeights, strat, days, pollMinutes } = input;
  if (watchlist.length === 0) return new Set<string>();
  // One bucket = one live engine cycle. Uses the strat's LIVE poll cadence
  // (what the engine actually runs at), which shares the engine's 60s
  // minIntervalMs floor — so a 5s UI setting still buckets at the 1-min clamp
  // live enforces.
  const cycleBucketMs = Math.max(60_000, Math.round((pollMinutes || 1) * 60_000));
  const windowCutoffMs = Date.now() - days * 86400_000;
  const totalW = sumWeights(watchlist, traderWeights) || 1;

  type Cand = { id: string; ts: number; score: number };
  const buys: Cand[] = [];
  for (const addr of watchlist) {
    const trades = traderTrades.get(addr) || [];
    const stats = traderStats.get(addr) ?? null;
    const weight = traderWeights[addr] || 0;
    const weightFraction = weight / totalW;
    for (const t of trades) {
      if (t.timestamp < windowCutoffMs) continue;
      if (t.side !== "BUY") continue;
      const stratTrade: StratTraderTrade = {
        ...t, trader: addr, weight, weightFraction,
        copyRatio: copyRatio.get(addr) ?? 0, notional: t.price * t.size,
      };
      // Same pre-filter the live engine applies — a filtered BUY never
      // enters the rank race, so it drops out of the backtest curve too.
      if (!strat.shouldMirror(stratTrade, history)) continue;
      const score = strat.scoreCandidate(stratTrade, stats, history);
      buys.push({ id: t.id, ts: t.timestamp, score });
    }
  }
  const buckets = new Map<number, Cand[]>();
  for (const c of buys) {
    const b = Math.floor(c.ts / cycleBucketMs);
    let arr = buckets.get(b);
    if (!arr) { arr = []; buckets.set(b, arr); }
    arr.push(c);
  }
  const kept = new Set<string>();
  const cap = strat.maxPerCycle();
  for (const cands of buckets.values()) {
    cands.sort((a, b) => b.score - a.score);
    for (let i = 0; i < cands.length && i < cap; i++) {
      if (cands[i].score > 0) kept.add(cands[i].id);
    }
  }
  return kept;
}

// ── Backtest portfolio simulation — the ONE source of truth ──
// Replays the constrained trade set through a simulated wallet: cash starts
// at CAPITAL, BUYs must fit the remaining cash (the live engine can't spend
// money it doesn't have), SELLs can only close inventory the sim actually
// holds, fees + gas come out of cash. Every surface on the BACKTEST tab —
// trade feed, equity curve, P&L/ROI header, fee row — derives from this one
// replay, so the numbers can never disagree with each other again (the old
// header mixed three different trade sets and showed "-$3.64 P&L, 0 TXS,
// GROSS +$1.56"). The equity history is the same {t, liq, pos} snapshot
// shape the LIVE portfolio records, rendered through the same EquityChart.
export function runBacktestSim(
  input: BacktestInput,
  traderStats: Map<string, TraderRoiStats>,
  copyRatio: Map<string, number>,
  history: StratHistory,
  keptBuyIds: Set<string>,
): BacktestSim {
  const {
    watchlist, traderTrades, traderPositions, traderWeights, strat, days, capital,
    minTrade, maxTrade, maxOpenPositions, stopLossPct, takeProfitFrac, marketQuery,
    rebalancePeriod = 0, rebalanceHour = 0, samplePct = 100, showAllTrades = false, loading = false,
  } = input;

  const empty: BacktestSim = {
    rows: [], equityHistory: [], markers: [],
    skipped: 0, netPnl: 0, grossPnl: 0, fees: 0, gas: 0, volume: 0,
    cash: capital, posValue: 0, unrealized: 0, costBasis: 0, open: [],
  };
  if (watchlist.length === 0 || loading) return empty;
  const cutoffMs = Date.now() - days * 86400_000;

  // Build from raw trades directly (not curve points) to use conditionId for filtering
  type RawEntry = {
    ts: number; market: string; conditionId: string; trader: string;
    side: "BUY" | "SELL"; size: number; price: number; realized: number;
    /** Full strat-shaped trade — sizing goes through the SAME
        sizeAndPrice hook the live engine calls, for both sides. */
    stratTrade: StratTraderTrade;
    score?: number; sharpe?: number; successProb?: number;
  };
  const allEntries: RawEntry[] = [];

  for (const addr of watchlist) {
    // Same market-topic gate as the curve — the feed must only ever show
    // trades the strat would actually mirror.
    const rawTrades = (traderTrades.get(addr) || [])
      .filter((t) => marketMatchesQuery(t.market, marketQuery));
    const positions = traderPositions.get(addr) || [];
    if (rawTrades.length === 0) continue;

    const replayTrades = rebalancePeriod > 0
      ? aggregateToRebalanceWindows(rawTrades, rebalancePeriod, rebalanceHour)
      : rawTrades;
    // Strict-in-window FIFO: a copy-trader starting at cutoffMs has no
    // pre-window inventory, so SELLs that would consume pre-window basis
    // shouldn't appear in the feed (they're not actually replicable).
    const inWindowAll = replayTrades.filter((t) => t.timestamp >= cutoffMs);
    // Apply SAMPLE % so the feed matches the chart's sampled trade set.
    const inWindowSampled = samplePct >= 100
      ? inWindowAll
      : inWindowAll.filter((t, i) =>
          keepInSample(samplePct, `${addr}:${t.timestamp}:${i}`),
        );
    // Same top-N Sharpe filter the curve uses — keeps the feed in sync.
    const inWindowTrades = rebalancePeriod > 0
      ? inWindowSampled
      : inWindowSampled.filter((t) =>
          t.side === "SELL" || keptBuyIds.has(t.id),
        );
    if (inWindowTrades.length === 0) continue;
    const annotated = computeFifoTrades(inWindowTrades, positions, cutoffMs);
    const windowAnnotated = annotated.filter((t) => t.side === "BUY" || t.hasBasis);
    if (windowAnnotated.length === 0) continue;

    // Score via the SAME strat instance used live + by keptBuyIds — so
    // dropping in a new strat changes BUY scores in the chart tooltip
    // and feed without any extra wiring. copyRatio comes from the shared
    // copyRatio map — the same proportional ratio the live engine hands
    // hooks — so sizing below scales exactly like a deployment.
    const stats = traderStats.get(addr) ?? null;
    const sharpe = stats?.sharpe ?? 0;
    const totalW = sumWeights(watchlist, traderWeights) || 1;
    const weight = traderWeights[addr] || 0;
    const weightFraction = weight / totalW;
    for (const t of windowAnnotated) {
      const stratTrade: StratTraderTrade = {
        ...t, trader: addr, weight, weightFraction,
        copyRatio: copyRatio.get(addr) ?? 0, notional: t.price * t.size,
      };
      let score: number | undefined;
      if (t.side === "BUY") {
        const s = strat.scoreCandidate(stratTrade, stats, history);
        if (s > 0) score = s;
      }
      allEntries.push({
        ts: t.timestamp, market: t.market, conditionId: t.conditionId || t.market,
        trader: addr, side: t.side, size: t.size, price: t.price,
        realized: t.realized, stratTrade,
        score,
        sharpe: sharpe > 0 ? sharpe : undefined,
        successProb: t.side === "BUY" ? successProbability(stats) : undefined,
      });
    }
  }

  allEntries.sort((a, b) => a.ts - b.ts);

  // Last time ANY leader trade touched each market — sorted ascending, so
  // the final write per key is its max. Drives the settlement pass below.
  const lastSeen = new Map<string, number>();
  for (const e of allEntries) lastSeen.set(e.conditionId, e.ts);

  // Current market prices for the final mark-to-market point — same source
  // the live portfolio uses (position currentPrice), falling back to the
  // last traded price the replay observed.
  const curPx = new Map<string, number>();
  for (const addr of watchlist) {
    for (const p of traderPositions.get(addr) || []) {
      const key = p.conditionId || p.market;
      if (key && p.currentPrice > 0) curPx.set(key, p.currentPrice);
    }
  }

  // Replay through the simulated wallet. TRADE SIZE constraints match the
  // live engine: amount < MIN → dust, not placed; amount > MAX → clamped;
  // a SELL only closes shares the sim actually holds. A BUY that doesn't
  // fit the remaining cash triggers the SAME capital-aware rebalance the
  // live engine runs (live_engine.rs `free_capital_via_sells`): sell the
  // weakest-score holds the candidate out-scores by the rebalance margin,
  // then place — only skip when no eligible hold can free enough. New
  // positions past `maxOpenPositions` are skipped exactly like live's
  // MAX_POSITIONS gate. SHOW ALL lifts every gate to preview the
  // unconstrained mirror.
  const round2 = (v: number) => Math.round(v * 100) / 100;
  // Live defaults (live_engine.rs): rebalancing ON, candidate must
  // out-score a hold by the shared playbook margin before that hold is
  // sold to fund it.
  const REBALANCE_MARGIN = 1 + REBALANCE_MARGIN_PCT;
  let cash = capital;
  const book = new Map<string, { shares: number; avgPx: number; entryScore: number; market: string }>();
  const lastPx = new Map<string, number>();
  const rows: LinkedTrade[] = [];
  const equityHistory: EquitySnapshot[] = [];
  const markers: EquityMarker[] = [];
  let skipped = 0;
  let fees = 0;
  let volume = 0;

  const posValue = (marks: Map<string, number>) => {
    let v = 0;
    for (const [k, b] of book) {
      if (b.shares > 1e-9) v += b.shares * (marks.get(k) ?? lastPx.get(k) ?? b.avgPx);
    }
    return v;
  };
  const openCount = () => {
    let n = 0;
    for (const b of book.values()) if (b.shares > 1e-9) n++;
    return n;
  };

  // Seed: all cash at the window start, so the curve starts at CAPITAL
  // exactly like a fresh live deployment.
  equityHistory.push({ t: Math.min(cutoffMs, allEntries[0]?.ts ?? cutoffMs), liq: capital, pos: 0 });

  // Mark-to-market snapshots BETWEEN executed trades. Every observed leader
  // trade moves a mark whether or not we act on it — without recording
  // those moves the curve froze at the last fill and drew a dead-straight
  // horizontal line from there to "now" (the sim kept skipping once cash
  // ran out, so hours of real mark movement rendered as one flat segment).
  // Bucketed so a 30d window doesn't emit tens of thousands of points.
  const SNAP_MS = Math.max(15 * 60_000, (days * 86400_000) / 400);
  let lastSnapT = equityHistory[0].t;
  const snapshotMark = (ts: number) => {
    if (ts - lastSnapT < SNAP_MS) return;
    lastSnapT = ts;
    equityHistory.push({ t: ts, liq: cash, pos: posValue(lastPx) });
  };

  // ── Settlement pass — the live engine's auto-redeem, simulated ──
  // Short-lived markets (5-min "Bitcoin Up or Down", hourly, daily) RESOLVE
  // mid-window. Live, the engine's 5-min auto-redeem pass converts those
  // positions to cash; the sim had no such model, so the book filled up with
  // dead resolved markets held at their last traded price FOREVER. Capital
  // never came back, `maxOpenPositions` was permanently saturated, and every
  // later BUY skipped — a 3d run on HFT leaders executed 42 trades in the
  // first 20 minutes then drew a flat line with "1032 SKIPPED".
  //
  // A held market settles at its LAST OBSERVED price once (a) its leader
  // feed has been quiet for the debounce window and (b) it has no live
  // current price (leaders hold none of it today) — i.e. it resolved, or
  // every leader exited and a copier would have exited with them. Settling
  // at the last mark is expectation-neutral (the position was already
  // valued there); what it fixes is freeing the cash and the position slot
  // so the replay keeps trading like a live deployment. Gas is charged like
  // any redeem; no taker fee (redeems aren't CLOB fills). Rendered as the
  // same amber REDEEM markers the LIVE chart uses — not as feed rows.
  const STALE_SETTLE_MS = 30 * 60_000;
  let settles = 0;
  const settleDead = (now: number) => {
    for (const [k, b] of [...book]) {
      if (b.shares <= 1e-9) { book.delete(k); continue; }
      if (curPx.has(k)) continue; // market is alive today — keep marking
      if (now - (lastSeen.get(k) ?? 0) < STALE_SETTLE_MS) continue;
      const px = lastPx.get(k) ?? b.avgPx;
      const proceeds = b.shares * px;
      cash += proceeds - GAS_PER_TRADE_USD;
      settles++;
      book.delete(k);
      markers.push({ t: now, side: "REDEEM", label: `SETTLE · ${b.market}`, usd: proceeds });
      equityHistory.push({ t: now, liq: cash, pos: posValue(lastPx) });
      lastSnapT = now;
    }
  };

  for (const t of allEntries) {
    settleDead(t.ts);
    const key = t.conditionId;
    const prevEquity = cash + posValue(lastPx);
    // Observed price is real market information whether or not we trade on
    // it — the mark moves either way (that's what MTM means).
    lastPx.set(key, t.price);
    // Live stop-loss parity: the engine sells a hold once its price decays
    // to ≤ entry × stopLoss (whole position, at the mark — same shape as
    // its bid-priced exit), via the SAME `stopLossTriggered` helper the
    // playbook exports (mirror of live_engine.rs `stop_loss_hit`). Checked
    // on every mark move of a held market so the backtest fires at the
    // same decay point live would. The tick that tripped the stop is then
    // consumed — live never re-enters a market in the same cycle it just
    // stopped out of.
    // Live take-profit parity: the engine liquidates a hold once its bid
    // runs to ≥ takeProfit (0.99 default — the market ran to ~100%, it's
    // decided), via the SAME `takeProfitTriggered` helper the playbook
    // exports (mirror of live_engine.rs `take_profit_hit`). Checked before
    // the stop so the labels match live's precedence.
    if (!showAllTrades && takeProfitFrac > 0) {
      const topped = book.get(key);
      if (topped && topped.shares > 1e-9 && takeProfitTriggered(t.price, takeProfitFrac)) {
        const proceeds = topped.shares * t.price;
        const sellFee = topped.shares * Math.min(t.price, 1 - t.price) * (TAKER_FEE_BPS / 10_000);
        const tpRealized = (t.price - topped.avgPx) * topped.shares;
        cash += proceeds - sellFee - GAS_PER_TRADE_USD;
        fees += sellFee;
        volume += proceeds;
        book.delete(key);
        const pos = posValue(lastPx);
        const equity = cash + pos;
        rows.push({
          ts: t.ts,
          market: topped.market,
          conditionId: key,
          trader: t.trader,
          side: "SELL",
          amount: proceeds,
          price: t.price,
          fee: round2(sellFee),
          realized: round2(tpRealized),
          runningPnl: round2(equity - capital),
          pnlDelta: round2(equity - prevEquity),
          cash: round2(cash),
          pos: round2(pos),
        });
        equityHistory.push({ t: t.ts, liq: cash, pos });
        lastSnapT = t.ts;
        markers.push({ t: t.ts, side: "SELL", label: `TAKE PROFIT · ${topped.market}`, usd: proceeds });
        skipped++;
        continue;
      }
    }
    if (!showAllTrades && stopLossPct > 0) {
      const stopped = book.get(key);
      if (stopped && stopped.shares > 1e-9 && stopLossTriggered(stopped.avgPx, t.price, (100 - stopLossPct) / 100)) {
        const proceeds = stopped.shares * t.price;
        const sellFee = stopped.shares * Math.min(t.price, 1 - t.price) * (TAKER_FEE_BPS / 10_000);
        const stopRealized = (t.price - stopped.avgPx) * stopped.shares;
        cash += proceeds - sellFee - GAS_PER_TRADE_USD;
        fees += sellFee;
        volume += proceeds;
        book.delete(key);
        const pos = posValue(lastPx);
        const equity = cash + pos;
        rows.push({
          ts: t.ts,
          market: stopped.market,
          conditionId: key,
          trader: t.trader,
          side: "SELL",
          amount: proceeds,
          price: t.price,
          fee: round2(sellFee),
          realized: round2(stopRealized),
          runningPnl: round2(equity - capital),
          pnlDelta: round2(equity - prevEquity),
          cash: round2(cash),
          pos: round2(pos),
        });
        equityHistory.push({ t: t.ts, liq: cash, pos });
        lastSnapT = t.ts;
        markers.push({ t: t.ts, side: "SELL", label: `STOP LOSS · ${stopped.market}`, usd: proceeds });
        skipped++;
        continue;
      }
    }
    // Size EXACTLY like the live engine: through the strat's sizeAndPrice
    // hook, which clamps proportional dust UP to max(MIN, CLOB floor) and
    // only skips true leader dust / no-legal-size cases. The old inline
    // gate here (`rawAmount < minTrade → skip`) silently killed EVERY
    // trade at small SIM funds — $100 spread over high-volume traders
    // mirrors to pennies, so 100% of the replay was "N SKIPPED · 0 TXS"
    // while a live deployment with identical params would have traded.
    // SHOW ALL still previews the unconstrained proportional mirror.
    const rawAmount = t.stratTrade.notional * t.stratTrade.copyRatio;
    let gatedAmount: number;
    if (showAllTrades) {
      gatedAmount = rawAmount;
    } else {
      const decision = strat.sizeAndPrice(t.stratTrade, {
        userFloor: minTrade,
        userCeiling: maxTrade,
        clobFloor: clobMinNotional(t.price),
        capital,
      }, history);
      if (decision.mirrorNotional <= 0) { skipped++; snapshotMark(t.ts); continue; }
      gatedAmount = decision.mirrorNotional;
    }

    let amount: number;
    let fee: number;
    let realized = 0;
    if (t.side === "BUY") {
      amount = gatedAmount;
      const shares = t.price > 0 ? amount / t.price : 0;
      fee = shares * Math.min(t.price, 1 - t.price) * (TAKER_FEE_BPS / 10_000);
      const cost = amount + fee + GAS_PER_TRADE_USD;
      // Live MAX_POSITIONS gate: opening a NEW market past the cap is
      // skipped; topping up an already-held one always passes.
      const held = book.get(key);
      if (!showAllTrades && !(held && held.shares > 1e-9) && openCount() >= maxOpenPositions) {
        skipped++; snapshotMark(t.ts); continue;
      }
      // Live capital-aware rebalance (free_capital_via_sells): when the BUY
      // doesn't fit the remaining cash, sell the weakest-score holds this
      // candidate out-scores by ≥ the margin — weakest first, full position
      // at its current mark — until the shortfall is covered. Without this
      // the sim froze the moment cash ran out while a live deployment kept
      // rotating capital into better-scoring trades.
      if (!showAllTrades && cost > cash) {
        const candScore = t.score ?? 0;
        const sellable = [...book.entries()]
          .filter(([k, b]) => k !== key && b.shares > 1e-9 && candScore >= b.entryScore * REBALANCE_MARGIN)
          .sort((a, b) => a[1].entryScore - b[1].entryScore);
        for (const [k, b] of sellable) {
          if (cost <= cash) break;
          const mark = lastPx.get(k) ?? b.avgPx;
          const proceeds = b.shares * mark;
          const sellFee = b.shares * Math.min(mark, 1 - mark) * (TAKER_FEE_BPS / 10_000);
          cash += proceeds - sellFee - GAS_PER_TRADE_USD;
          fees += sellFee;
          volume += proceeds;
          book.delete(k);
          const pos = posValue(lastPx);
          const equity = cash + pos;
          rows.push({
            ts: t.ts,
            market: b.market,
            conditionId: k,
            trader: t.trader,
            side: "SELL",
            amount: proceeds,
            price: mark,
            fee: round2(sellFee),
            realized: round2((mark - b.avgPx) * b.shares),
            runningPnl: round2(equity - capital),
            pnlDelta: round2(equity - prevEquity),
            cash: round2(cash),
            pos: round2(pos),
          });
          equityHistory.push({ t: t.ts, liq: cash, pos });
          markers.push({ t: t.ts, side: "SELL", label: `REBALANCE · ${b.market}`, usd: proceeds });
        }
        if (cost > cash) { skipped++; snapshotMark(t.ts); continue; } // nothing left to rotate out
      }
      cash -= cost;
      const b = book.get(key) ?? { shares: 0, avgPx: 0, entryScore: 0, market: t.market };
      const newShares = b.shares + shares;
      b.avgPx = newShares > 0 ? (b.avgPx * b.shares + t.price * shares) / newShares : 0;
      b.shares = newShares;
      // Freeze the strongest score seen at entry — the bar a future
      // candidate must clear (× margin) to rotate this hold out.
      b.entryScore = Math.max(b.entryScore, t.score ?? 0);
      book.set(key, b);
    } else {
      const b = book.get(key);
      const held = b?.shares ?? 0;
      if (!b || held <= 1e-9) { skipped++; snapshotMark(t.ts); continue; } // nothing to close at our scale
      const shares = Math.min(t.price > 0 ? gatedAmount / t.price : 0, held);
      amount = shares * t.price;
      fee = shares * Math.min(t.price, 1 - t.price) * (TAKER_FEE_BPS / 10_000);
      cash += amount - fee - GAS_PER_TRADE_USD;
      realized = (t.price - b.avgPx) * shares;
      b.shares -= shares;
      if (b.shares <= 1e-9) book.delete(key);
    }

    fees += fee;
    volume += amount;
    const pos = posValue(lastPx);
    const equity = cash + pos;
    rows.push({
      ts: t.ts,
      market: t.market,
      conditionId: key,
      trader: t.trader,
      side: t.side,
      amount,
      price: t.price,
      fee: round2(fee),
      realized: round2(realized),
      runningPnl: round2(equity - capital),
      pnlDelta: round2(equity - prevEquity),
      cash: round2(cash),
      pos: round2(pos),
      score: t.score,
      sharpe: t.sharpe,
      successProb: t.successProb,
    });
    equityHistory.push({ t: t.ts, liq: cash, pos });
    lastSnapT = t.ts; // fills reset the mark-snapshot bucket
    markers.push({
      t: t.ts,
      side: t.side,
      usd: round2(amount),
      label: `${t.market}${t.stratTrade.outcome ? ` · ${t.stratTrade.outcome}` : ""} @ ${Math.round(t.price * 100)}¢`,
    });
  }

  // Final settlement sweep — anything still held in a dead market settles
  // before the NOW mark, so resolved inventory shows as cash (what a live
  // deployment would actually be holding today), not as phantom positions.
  settleDead(Date.now());

  // Final NOW point — open inventory re-marked at current market prices so
  // the curve ends where a live deployment's portfolio would sit today.
  const nowPos = posValue(curPx);
  equityHistory.push({ t: Date.now(), liq: cash, pos: nowPos });

  const gas = (rows.length + settles) * GAS_PER_TRADE_USD;
  const netPnl = round2(cash + nowPos - capital);

  // What the simulated wallet is still holding, in the same row shape the
  // LIVE panel builds from real positions — so one table renders both.
  const open: PerfPosition[] = [];
  let unrealized = 0;
  let costBasis = 0;
  for (const [k, b] of book) {
    if (b.shares <= 1e-9) continue;
    const mark = curPx.get(k) ?? lastPx.get(k) ?? b.avgPx;
    const basis = b.avgPx * b.shares;
    const pnlUsd = round2(b.shares * (mark - b.avgPx));
    unrealized += pnlUsd;
    costBasis += basis;
    open.push({
      key: k,
      market: b.market,
      // The sim books by conditionId, not by outcome leg — every mirrored
      // fill follows the leader's side, so the held leg is theirs.
      outcome: "COPY",
      size: b.shares,
      avgPrice: b.avgPx,
      curPrice: mark,
      value: round2(b.shares * mark),
      pnlUsd,
      rowTitle: `${b.market} · simulated hold · entry ${Math.round(b.avgPx * 100)}¢`,
    });
  }
  // Same rotation order the live panel sorts by: worst first — that's what
  // the engine sells to free cash.
  open.sort((a, b2) => a.pnlUsd - b2.pnlUsd);

  return {
    rows,
    equityHistory,
    markers,
    skipped,
    netPnl,
    grossPnl: round2(netPnl + fees + gas),
    fees: round2(fees),
    gas: round2(gas),
    volume: round2(volume),
    cash: round2(cash),
    posValue: round2(nowPos),
    unrealized: round2(unrealized),
    costBasis: round2(costBasis),
    open,
  };
}

/** The whole pipeline in one call — every stage returned, because the console
    renders the intermediates (trader stats, capital plan) alongside the sim. */
export function runBacktest(input: BacktestInput): BacktestResult {
  const traderStats = computeTraderStats(
    input.watchlist, input.traderTrades, input.traderPositions, input.marketQuery,
  );
  const copyRatio = computeCopyRatios(input);
  const history = buildStratHistory(input, traderStats, copyRatio);
  const keptBuyIds = computeKeptBuyIds(input, traderStats, copyRatio, history);
  const sim = runBacktestSim(input, traderStats, copyRatio, history, keptBuyIds);
  return { traderStats, copyRatio, history, keptBuyIds, sim };
}

/** Every backtest knob a SavedIndex carries, resolved to the same defaults the
    console hydrates its inputs with (CopyIndex's "load from active strategy"
    effect). The HUB backtests strats it never opens, so the defaults have to
    live somewhere both can read — here. */
export function stratBacktestParams(idx: SavedIndex): {
  capital: number; minTrade: number; maxTrade: number; maxOpenPositions: number;
  stopLossPct: number; takeProfitFrac: number; marketQuery: string; pollMinutes: number;
} {
  const stopFrac = idx.stopLoss ?? DEFAULT_STOP_LOSS;
  return {
    // Backtests are always paper — a strat carrying capital 0 (persisted from
    // an unfunded wallet or imported that way) would zero every simulated
    // trade, so fall back to the $1K default.
    capital: idx.capital && idx.capital > 0 ? idx.capital : DEFAULT_CAPITAL,
    minTrade: idx.minTrade ?? 5,
    maxTrade: idx.maxTrade ?? 100,
    maxOpenPositions: idx.maxOpenPositions ?? 10,
    stopLossPct: stopFrac > 0 && stopFrac < 1 ? Math.round((1 - stopFrac) * 100) : 0,
    takeProfitFrac: idx.takeProfit ?? DEFAULT_TAKE_PROFIT,
    marketQuery: idx.marketQuery ?? "",
    // Same clamp the console applies: the legacy 1-minute default reads as
    // "unset", and sub-floor values from the old 5s era run at the floor.
    pollMinutes: idx.livePollMinutes
      ?? (idx.rebalanceMinutes && idx.rebalanceMinutes !== 1
        ? Math.max(MIN_POLL_MINUTES, idx.rebalanceMinutes)
        : MIN_POLL_MINUTES),
  };
}
