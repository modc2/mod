// THE Strat class — the one, standard strategy class.
//
// A strategy IS this class. There is no registry and no subclass zoo: the
// live engine (copyEngine.ts) and the backtest (CopyIndex.tsx) both do
// `new Strat(params)` and drive everything through five hooks. Every hook
// receives the full `StratHistory` — the observed trade history across all
// watched traders, per-trader stats, open positions, and balance — so the
// strat can reason over ANY history of the data, not just the single trade
// in front of it.
//
// Everything a strategy can do is a PARAM on this one class:
//
//   maxPerCycle      per-cycle BUY budget (fee control)
//   marketQuery      free-text market-topic gate
//   tradeFilters     semantic per-trade gates (side/price/size/category)
//   mirror           false = never mirror per-trade (origination only)
//   slippageBps      limit-price widening toward the fillable side
//   flow { … }       opt-in flow-momentum ORIGINATION: buy watchlist
//                    consensus from history, exit when flow flips
//
// The five hooks (all have working defaults driven by those params):
//
//   maxPerCycle()                     per-cycle BUY budget
//   shouldMirror(trade, history)      pre-filter observed upstream trades
//   scoreCandidate(trade, s, history) rank candidates by expected $ edge
//   sizeAndPrice(trade, c, history)   final notional + limit price
//   propose(history, c)               ORIGINATE trades from history alone
//
// The engine handles plumbing — cycle loop, balance check, deposit wallet,
// CLOB submission, log persistence, ROI stats refresh. The strat only
// decides WHAT to trade, in WHAT size, at WHAT price.
//
// The class stays subclassable for power users (override any hook, hand
// the instance to CopyEngine's constructor), but the standard path is
// params on this one class. Mirrors src/strats/base/mod.py (sync → signal
// → execute) for users who author strats in Python.

import { PolymarketTrade, PolymarketPosition, TraderRoiStats, IndexTrader } from "../types";
import type { TradeFilters } from "../types";
import { marketMatchesQuery } from "../marketQuery";
import { tradeMatchesFilters } from "../tradeFilters";

// ── Shared per-trade context the strat sees ──────────────────────

/** A single observed upstream trade from a watched trader. The strat decides
    whether/how to mirror it. */
export interface TraderTrade extends PolymarketTrade {
  /** Address of the watched trader who placed this trade. */
  trader: string;
  /** Trader's weight from the strat's watchlist. */
  weight: number;
  /** Trader's total weight share (`weight / sum(weights)`). */
  weightFraction: number;
  /** Proportional copy ratio: `(capital × weightFraction) / max(buyVol, sellVol)`
      over the strat's backtest window. Multiply by trade.notional to get the
      raw mirror notional before clamping. */
  copyRatio: number;
  /** `price × size` — leader's dollar exposure on this trade. */
  notional: number;
}

/** Everything the engine knows, handed to every hook. This is the "any
    history of the data" contract: a strat can aggregate flow, detect
    momentum, compare against its own book — whatever its logic needs. */
export interface StratHistory {
  /** Observed upstream trades across ALL watched traders inside the
      strat's lookback window (`backtestDays`), newest-first. During the
      engine's collection phase this is complete for every trader already
      polled this cycle; by sizing (`sizeAndPrice`) and origination
      (`propose`) time it is complete for the whole watchlist. */
  trades: TraderTrade[];
  /** Per-trader ROI / Sharpe stats, keyed by lowercased address. */
  traderStats: Record<string, TraderRoiStats>;
  /** Open positions in the trading wallet. Fetched per-cycle only when the
      strat originates trades (`proposes()` true); empty otherwise. */
  positions: PolymarketPosition[];
  /** Usable USDC balance in the trading wallet. null = not yet read. */
  balance: number | null;
  /** Strat's allocated capital (USD). */
  capital: number;
  /** The strat's watchlist (enabled traders only). */
  watchlist: IndexTrader[];
  /** Engine cycle counter (0 in backtest). */
  cycle: number;
  /** Clock at history assembly (ms epoch). */
  now: number;
}

/** An empty history for contexts that have nothing better (helper probes,
    unit tests). Real engine cycles always build the full thing. */
export function emptyHistory(capital = 0): StratHistory {
  return {
    trades: [],
    traderStats: {},
    positions: [],
    balance: null,
    capital,
    watchlist: [],
    cycle: 0,
    now: 0,
  };
}

/** Sizing constraints supplied by the strat config — the engine forwards
    these so the strat doesn't have to dig into config itself. */
export interface SizeConstraints {
  /** User's TRADE SIZE min (USD). Smaller mirror amounts → skip. */
  userFloor: number;
  /** User's TRADE SIZE max (USD). Larger mirror amounts → clamp down. */
  userCeiling: number;
  /** CLOB's hard floor for this trade's price (`max($1, 5 × price)`).
      Smaller mirror amounts → clamp up or skip. */
  clobFloor: number;
  /** Strat's allocated capital in USD. */
  capital: number;
}

/** Output of the strat's per-candidate decision. The engine consumes this
    rather than letting the strat call placeOrder itself — keeps order
    submission, retries, and logging in one place. */
export interface CandidateDecision {
  /** Mirror notional in USD. 0 = skip. */
  mirrorNotional: number;
  /** Limit price (0–1). Widened by `slippageBps` toward the fillable side. */
  limitPrice: number;
  /** Optional human-readable reason — surfaced in the BALANCE log row
      when a clamp/widening fired (e.g. "clamped up to CLOB floor"). */
  reason?: string;
}

/** A strat-ORIGINATED trade intent returned from `propose`. Unlike mirror
    candidates these are not tied to any upstream trade — the engine
    resolves the token id, clamps to the CLOB floor, and submits. */
export interface ProposedTrade {
  /** Market condition id (0x…). The engine resolves outcome → token id. */
  conditionId: string;
  /** "Yes" | "No". Defaults to "Yes". */
  outcome?: string;
  /** Market title — for the execution log only. */
  market: string;
  side: "BUY" | "SELL";
  /** Order size in USD. */
  notional: number;
  /** Limit price 0–1 (tick-rounded by the engine if off-grid). */
  limitPrice: number;
  /** Shown verbatim in the log next to the order. */
  reason?: string;
}

// ── Params ───────────────────────────────────────────────────────

/** Flow-momentum origination params. Setting `params.flow` (even `{}`)
    turns origination ON: the strat aggregates window flow across the
    watchlist and proposes entries where several traders pile into the
    same side, and exits when a held position's flow flips net-SELL. */
export interface FlowParams {
  /** How far back (minutes) to aggregate flow. Default 90. */
  lookbackMinutes?: number;
  /** Minimum distinct watched traders on the same side before the signal
      counts as consensus. Default 2. */
  minTraders?: number;
  /** Minimum aggregate BUY notional (USD) across those traders. Default 50. */
  minFlowUsd?: number;
  /** Max simultaneous open positions origination may hold. Default 5. */
  maxPositions?: number;
}

/** The one params surface. Every strategy is a value of this interface —
    saved strats, share bundles, and the engine config all reduce to it. */
export interface StratParams {
  /** Display name echoed in the UI / execution log. Default "strat". */
  name?: string;
  /** Max BUYs to act on per cycle. The single most important fee-control
      knob. Default 3. */
  maxPerCycle?: number;
  /** Free-text market-topic filter (e.g. "bitcoin"). When set, only trades
      in markets whose title matches pass `shouldMirror`. */
  marketQuery?: string;
  /** Semantic per-trade filters (side / price band / size band / category),
      AND-ed with `marketQuery` in `shouldMirror`. */
  tradeFilters?: TradeFilters;
  /** false = never mirror individual upstream trades (origination only).
      Default true. */
  mirror?: boolean;
  /** Limit-price widening in basis points toward the fillable side
      (BUY = up, SELL = down) so mirrors don't sit unfilled behind the
      market. 300 = 3¢ tolerance on a 100¢ market. Default 300. */
  slippageBps?: number;
  /** Opt-in flow-momentum origination — see FlowParams. Absent = pure
      mirror strat, `propose` returns []. */
  flow?: FlowParams;
}

// ── Flow aggregation internals ───────────────────────────────────

interface MarketFlow {
  conditionId: string;
  market: string;
  outcome: string;
  buyUsd: number;
  sellUsd: number;
  buyers: Set<string>;
  sellers: Set<string>;
  lastPrice: number;
  lastTs: number;
}

// ── The Strat class ──────────────────────────────────────────────

/** The standard strategy class. Construct it with `StratParams` and hand
    it to the engine — mirroring, scoring, sizing, and (opt-in) origination
    are all param-driven defaults. Subclassing still works for behavior no
    param expresses: override any hook and pass the instance to CopyEngine. */
export class Strat {
  /** Display name in the UI / log. */
  readonly name: string;

  /** The params the strat was constructed with — echoed for the UI and
      logs so a running engine can always show its exact configuration. */
  readonly params: StratParams;

  constructor(params: StratParams = {}) {
    this.params = params;
    this.name = params.name ?? "strat";
  }

  // ── Hook 1: per-cycle BUY cap ──
  // Top-N sampling keeps only this many BUYs by score (SELLs are always
  // honored). Lower = fewer fees, less churn.
  maxPerCycle(): number {
    return this.params.maxPerCycle ?? 3;
  }

  // ── Hook 2: pre-filter ──
  // Return false to skip an observed trade entirely (before scoring,
  // sizing, or any side-effects). Default applies three generic gates:
  //   1. `params.mirror === false` → never mirror (origination-only strat)
  //   2. market-topic query (`params.marketQuery`)
  //   3. semantic trade filters (`params.tradeFilters`)
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  shouldMirror(trade: TraderTrade, history: StratHistory): boolean {
    if (this.params.mirror === false) return false;
    return (
      marketMatchesQuery(trade.market, this.params.marketQuery ?? "") &&
      tradeMatchesFilters(trade, this.params.tradeFilters ?? {})
    );
  }

  // ── Hook 3: ranking ──
  // Returns dollars of expected edge. The engine sorts BUY candidates by
  // this and copies the top `maxPerCycle`; the same number drives EP-based
  // capital rotation. Default: trader's window ROI × the dollars we'd
  // deploy (`roi × mirror$` = a real dollar expectation). No stats → 0
  // (can't estimate edge, never wins the budget). Negative ROI sorts to
  // the bottom (engine skips EP ≤ 0). Return 0 to drop a candidate
  // without an explicit shouldMirror=false.
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  scoreCandidate(trade: TraderTrade, stats: TraderRoiStats | null, history: StratHistory): number {
    if (!stats) return 0;
    return stats.roi * trade.notional * trade.copyRatio;
  }

  // ── Hook 4: sizing + limit price ──
  // Called AFTER score-ranking, so the candidate is already a "we want
  // this one". Order of clamping is significant:
  //   1. Ceiling can't accommodate CLOB floor          → skip (no legal order)
  //   2. Raw mirror below effective floor + leader real → clamp up
  //   3. Raw mirror below effective floor + leader dust → skip (no signal)
  //   4. Raw mirror above ceiling                       → clamp down
  // The effective floor is max(user TRADE SIZE floor, CLOB per-price min).
  // Proportional dust is clamped UP to that floor (not skipped) so small-
  // but-real leader trades still copy. Return `mirrorNotional: 0` to SKIP
  // with `reason` shown verbatim in the log.
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  sizeAndPrice(trade: TraderTrade, c: SizeConstraints, history: StratHistory): CandidateDecision {
    const rawMirrorNotional = trade.notional * trade.copyRatio;
    const limitPrice = tickRoundPrice(this.adjustPrice(trade));

    // (1) No legal size at the user's ceiling for this trade's price.
    if (c.userCeiling < c.clobFloor) {
      return {
        mirrorNotional: 0,
        limitPrice,
        reason: `CEILING_BELOW_CLOB_FLOOR · ceiling $${c.userCeiling.toFixed(2)} < CLOB min $${c.clobFloor.toFixed(2)} (5 shares × ${(trade.price * 100).toFixed(0)}¢)`,
      };
    }
    // Effective minimum order: the larger of the user's TRADE SIZE floor
    // and the CLOB per-price hard floor (max($1, 5 × price)).
    const minNotional = Math.max(c.userFloor, c.clobFloor);
    let mirrorNotional = rawMirrorNotional;
    let reason: string | undefined;
    // (2)/(3) Below the effective floor — clamp UP unless the leader's own
    //     trade is itself below the CLOB floor (then it's not real signal).
    if (rawMirrorNotional < minNotional) {
      if (trade.notional < c.clobFloor) {
        return {
          mirrorNotional: 0,
          limitPrice,
          reason: `LEADER_DUST · leader $${trade.notional.toFixed(2)} < Polymarket $${c.clobFloor.toFixed(2)} hard floor (5 shares × ${(trade.price * 100).toFixed(0)}¢)`,
        };
      }
      mirrorNotional = minNotional;
      reason = `clamped up: proportional $${rawMirrorNotional.toFixed(2)} → $${minNotional.toFixed(2)} (min order: max floor $${c.userFloor.toFixed(2)}, CLOB $${c.clobFloor.toFixed(2)} = 5 shares × ${(trade.price * 100).toFixed(0)}¢)`;
    }
    // (4) Above ceiling — clamp down.
    if (mirrorNotional > c.userCeiling) {
      mirrorNotional = c.userCeiling;
      reason = `clamped down: proportional $${rawMirrorNotional.toFixed(2)} → ceiling $${c.userCeiling.toFixed(2)}`;
    }
    return { mirrorNotional, limitPrice, reason };
  }

  // ── Hook 5: origination ──
  // Propose trades from the history alone — no upstream trade required.
  // Runs once per cycle AFTER the mirror pass, with the COMPLETE cycle
  // history. The engine tick-rounds prices, clamps to the CLOB floor,
  // caps to `maxPerCycle` proposals, and logs each with the `reason`.
  // Default: flow-momentum origination when `params.flow` is set —
  // ENTRIES on watchlist consensus (≥ minTraders distinct buyers,
  // aggregate ≥ minFlowUsd, net flow positive) in markets not already
  // held, ranked by net flow, sized by conviction and clamped into the
  // user's trade band; EXITS on held positions whose window flow flipped
  // net-SELL. Without `params.flow`: originate nothing (pure mirror).
  propose(history: StratHistory, c: SizeConstraints): ProposedTrade[] {
    const flow = this.params.flow;
    if (!flow) return [];

    const minTraders = flow.minTraders ?? 2;
    const minFlowUsd = flow.minFlowUsd ?? 50;
    const maxPositions = flow.maxPositions ?? 5;
    const flows = this.aggregateFlow(history);
    const proposals: ProposedTrade[] = [];

    const held = new Map(
      history.positions
        .filter((p) => p.size > 0)
        .map((p) => [`${p.conditionId.toLowerCase()}:${(p.outcome || "Yes").toLowerCase()}`, p]),
    );

    // EXITS first — freed capital funds the entries below.
    for (const [key, pos] of held) {
      const f = flows.get(key);
      if (!f) continue;
      const netUsd = f.buyUsd - f.sellUsd;
      if (netUsd < 0 && f.sellers.size >= minTraders) {
        proposals.push({
          conditionId: pos.conditionId,
          outcome: pos.outcome,
          market: pos.market,
          side: "SELL",
          notional: pos.value,
          limitPrice: tickRoundPrice(pos.currentPrice * 0.97),
          reason: `FLOW FLIPPED · ${f.sellers.size} traders net -$${Math.abs(netUsd).toFixed(0)} in window`,
        });
      }
    }

    // ENTRIES — strongest net-BUY consensus first.
    const openSlots = Math.max(0, maxPositions - held.size);
    const entries = [...flows.entries()]
      .filter(([key, f]) =>
        !held.has(key) &&
        f.buyers.size >= minTraders &&
        f.buyUsd >= minFlowUsd &&
        f.buyUsd > f.sellUsd &&
        f.lastPrice > 0.02 && f.lastPrice < 0.95,
      )
      .sort((a, b) => (b[1].buyUsd - b[1].sellUsd) - (a[1].buyUsd - a[1].sellUsd))
      .slice(0, openSlots);

    for (const [, f] of entries) {
      // Conviction sizing: share of capital proportional to this market's
      // slice of total net flow, clamped into the user's trade band.
      const netUsd = f.buyUsd - f.sellUsd;
      const raw = Math.min(c.capital / Math.max(maxPositions, 1), netUsd);
      const notional = Math.min(Math.max(raw, c.userFloor, c.clobFloor), c.userCeiling);
      proposals.push({
        conditionId: f.conditionId,
        outcome: f.outcome,
        market: f.market,
        side: "BUY",
        notional,
        // Chase up to 2¢ past the last observed print so the entry fills.
        limitPrice: tickRoundPrice(f.lastPrice + 0.02),
        reason: `CONSENSUS · ${f.buyers.size} traders +$${netUsd.toFixed(0)} net BUY in ${flow.lookbackMinutes ?? 90}m`,
      });
    }

    return proposals;
  }

  // ── Capability probe ──
  // True when this strat originates trades — gates the engine's per-cycle
  // positions fetch and Phase 4. Param-driven (`params.flow`), with a
  // prototype check so a subclass that overrides `propose` still counts.
  proposes(): boolean {
    return !!this.params.flow || this.propose !== Strat.prototype.propose;
  }

  // ── Overridable price shaping used by the default sizeAndPrice ──
  // Widens the leader's price by `slippageBps` toward whichever side is
  // fillable (BUY = up, SELL = down) so mirrors don't sit unfilled behind
  // the market. slippageBps: 0 quotes the leader's exact price. Caller
  // tick-rounds.
  protected adjustPrice(trade: TraderTrade): number {
    const bps = (this.params.slippageBps ?? 300) / 10_000;
    if (trade.side === "BUY") return Math.min(trade.price * (1 + bps), 0.99);
    return Math.max(trade.price * (1 - bps), 0.01);
  }

  // Aggregate observed flow per market+outcome over the flow lookback
  // window. Shared by propose()'s entry and exit passes.
  private aggregateFlow(history: StratHistory): Map<string, MarketFlow> {
    const lookbackMs = (this.params.flow?.lookbackMinutes ?? 90) * 60_000;
    const cutoff = history.now - lookbackMs;
    const flows = new Map<string, MarketFlow>();
    for (const t of history.trades) {
      if (t.timestamp < cutoff) continue;
      if (!marketMatchesQuery(t.market, this.params.marketQuery ?? "")) continue;
      const outcome = t.outcome || "Yes";
      const key = `${t.conditionId.toLowerCase()}:${outcome.toLowerCase()}`;
      let f = flows.get(key);
      if (!f) {
        f = {
          conditionId: t.conditionId,
          market: t.market,
          outcome,
          buyUsd: 0,
          sellUsd: 0,
          buyers: new Set(),
          sellers: new Set(),
          lastPrice: t.price,
          lastTs: t.timestamp,
        };
        flows.set(key, f);
      }
      if (t.side === "BUY") {
        f.buyUsd += t.notional;
        f.buyers.add(t.trader.toLowerCase());
      } else {
        f.sellUsd += t.notional;
        f.sellers.add(t.trader.toLowerCase());
      }
      // trades are newest-first; keep the newest price we've seen.
      if (t.timestamp > f.lastTs) {
        f.lastTs = t.timestamp;
        f.lastPrice = t.price;
      }
    }
    return flows;
  }
}

// ── Helpers strats commonly need ─────────────────────────────────

/** Polymarket binary markets use a 0.01 (1¢) tick size. Round limit
    prices so the resulting maker/taker amount ratio lands on the grid —
    CLOB rejects off-tick orders with "Invalid order payload". */
export function tickRoundPrice(p: number): number {
  if (!Number.isFinite(p)) return 0;
  const clamped = Math.max(0.01, Math.min(0.99, p));
  return Math.round(clamped * 100) / 100;
}

/** CLOB hard floors. The matcher rejects any order below either:
    - $1 notional ("Invalid order payload" generic), or
    - 5 shares ("Size (N) lower than the minimum: 5").
    So the effective floor is per-price: max($1, 5 × price). */
export const POLYMARKET_MIN_USD = 1.0;
export const POLYMARKET_MIN_SHARES = 5;
export function clobMinNotional(price: number): number {
  return Math.max(POLYMARKET_MIN_USD, POLYMARKET_MIN_SHARES * Math.max(price, 1e-9));
}

// Re-exports so a strat consumer only has to `import from "./strat"`.
export type { PolymarketTrade, PolymarketPosition, TraderRoiStats, IndexTrader };
export type { TradeFilters } from "../types";
export { marketMatchesQuery } from "../marketQuery";
export { tradeMatchesFilters, tradeFiltersActive, describeTradeFilters } from "../tradeFilters";
