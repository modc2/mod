// Canonical Strat base class for the live + backtest engines.
//
// A strategy IS a class. The engine is parameterized by it: pick a class in
// the registry (registry.ts), construct it with its params, and the engine
// drives everything through five hooks. Every hook receives the full
// `StratHistory` — the observed trade history across all watched traders,
// per-trader stats, open positions, and balance — so a strat can reason
// over ANY history of the data, not just the single trade in front of it.
//
// The five hooks (all have working defaults — override any subset):
//
//   maxPerCycle()                     per-cycle BUY budget (fee control)
//   shouldMirror(trade, history)      pre-filter observed upstream trades
//   scoreCandidate(trade, s, history) rank candidates by expected $ edge
//   sizeAndPrice(trade, c, history)   final notional + limit price
//   propose(history, c)               ORIGINATE trades from history alone —
//                                     not tied to any upstream trade. This
//                                     is how non-copy strats (momentum,
//                                     mean-reversion, market making) plug
//                                     into the same engine.
//
// The engine handles plumbing — cycle loop, balance check, deposit wallet,
// CLOB submission, log persistence, ROI stats refresh. The strat only
// decides WHAT to trade, in WHAT size, at WHAT price.
//
// Mirrors src/strats/base/mod.py (sync → signal → execute) so a strat can
// be authored in TS or Python against the same idea: history in, trade
// intents out.

import { PolymarketTrade, PolymarketPosition, TraderRoiStats, IndexTrader } from "../types";
import type { TradeFilters } from "../types";
import { marketMatchesQuery } from "../marketQuery";
import { tradeMatchesFilters } from "../tradeFilters";

// ── Shared per-trade context the strat sees ──────────────────────

/** A single observed upstream trade from a watched trader. Strats decide
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
      strat class overrides `propose` (extra API call); empty otherwise. */
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

/** Output of a strat's per-candidate decision. The engine consumes this
    rather than letting the strat call placeOrder itself — keeps order
    submission, retries, and logging in one place. */
export interface CandidateDecision {
  /** Mirror notional in USD. 0 = skip. */
  mirrorNotional: number;
  /** Limit price (0–1). Strats can widen for slippage tolerance. */
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

/** Common tunables every strat understands. Strat-specific params extend
    this — the class's generic parameter `P` declares them, the registry
    passes them through, and `this.params` carries them at runtime. */
export interface StratParams {
  /** Max BUYs to act on per cycle. The single most important fee-control
      knob. Default 3. */
  maxPerCycle?: number;
  /** Free-text market-topic filter (e.g. "bitcoin"). When set, only trades
      in markets whose title matches pass `shouldMirror`. */
  marketQuery?: string;
  /** Semantic per-trade filters (side / price band / size band / category),
      AND-ed with `marketQuery` in `shouldMirror`. */
  tradeFilters?: TradeFilters;
}

// ── The Strat class ──────────────────────────────────────────────

/** Abstract base every strategy extends, parameterized by its params type.
    All hooks have working defaults, so a custom strat overrides only what
    it changes — a pure originator overrides `propose` and nothing else;
    a copy-flavored strat tweaks `scoreCandidate` or `adjustPrice`. */
export abstract class Strat<P extends StratParams = StratParams> {
  /** Display name in the UI / log. */
  abstract readonly name: string;

  /** The params the class was constructed with — echoed for the UI and
      logs so a running engine can always show its exact configuration. */
  readonly params: P;

  constructor(params?: P) {
    this.params = (params ?? {}) as P;
  }

  // ── Hook 1: per-cycle BUY cap ──
  // Top-N sampling keeps only this many BUYs by score (SELLs are always
  // honored). Lower = fewer fees, less churn.
  maxPerCycle(): number {
    return this.params.maxPerCycle ?? 3;
  }

  // ── Hook 2: pre-filter ──
  // Return false to skip an observed trade entirely (before scoring,
  // sizing, or any side-effects). Default applies the two generic gates:
  //   1. market-topic query (`params.marketQuery`)
  //   2. semantic trade filters (`params.tradeFilters`)
  // Override to add dimensions (time-of-day, flow context from history, …).
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  shouldMirror(trade: TraderTrade, history: StratHistory): boolean {
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
  // caps to `maxPerCycle` proposals, and logs each with your `reason`.
  // Default: originate nothing (pure copy strat).
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  propose(history: StratHistory, c: SizeConstraints): ProposedTrade[] {
    return [];
  }

  // ── Overridable price shaping used by the default sizeAndPrice ──
  // Default: the leader's own price. Override to widen for slippage
  // tolerance (see CopyTrader) or quote your own level. Caller tick-rounds.
  protected adjustPrice(trade: TraderTrade): number {
    return trade.price;
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

// Re-exports so a strat file only has to `import from "./base"`.
export type { PolymarketTrade, PolymarketPosition, TraderRoiStats, IndexTrader };
export type { TradeFilters } from "../types";
export { marketMatchesQuery } from "../marketQuery";
export { tradeMatchesFilters, tradeFiltersActive, describeTradeFilters } from "../tradeFilters";
