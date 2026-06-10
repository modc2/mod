// Canonical Strat interface for the live + backtest engines.
//
// One class per file. To replace the strategy, drop a new class in this
// directory that implements `Strat`, then point the engine at it (a single
// line in copyEngine.ts: `this.strat = new MyStrat()`).
//
// The engine handles plumbing — cycle loop, balance check, deposit wallet,
// CLOB submission, log persistence, ROI stats refresh. The strat only
// decides: WHICH trades to copy, in WHAT size, at WHAT price.
//
// Mirrors src/strats/base/mod.py so a strat can be authored in TS or
// Python (or eventually Rust) against the same shape.

import { PolymarketTrade, TraderRoiStats, IndexTrader } from "../types";

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

// ── Strat interface ──────────────────────────────────────────────

export interface Strat {
  /** Display name in the UI / log. */
  readonly name: string;

  /** Per-cycle BUY cap — top-N sampling keeps only this many BUYs by
      score (SELLs are always honored). Lower = fewer fees, less churn. */
  maxPerCycle(): number;

  /** Pre-filter — return false to skip a trade entirely (before scoring,
      sizing, or any side-effects). Default: true. */
  shouldMirror(trade: TraderTrade): boolean;

  /** Rank function. Returns dollars of expected edge. The engine sorts
      BUY candidates by this and copies the top `maxPerCycle`. Return 0
      to drop a candidate without an explicit shouldMirror=false. */
  scoreCandidate(trade: TraderTrade, stats: TraderRoiStats | null): number;

  /** Compute the final mirror notional + limit price for one candidate.
      The engine calls this AFTER score-ranking, so the candidate is
      already a "we want to copy this one". The strat handles user floor /
      ceiling / CLOB floor. Return mirrorNotional=0 to skip with a
      reason in the log. */
  sizeAndPrice(trade: TraderTrade, c: SizeConstraints): CandidateDecision;
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
export type { PolymarketTrade, TraderRoiStats, IndexTrader };
