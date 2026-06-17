// CopyTrader — the reference Strat implementation.
//
// Mirrors each watched trader's fills, weighted by their 30d Sharpe
// (consistency × edge) and capped per-cycle to control fees. This file
// is the editable template — fork it (`class MyStrat extends CopyTrader`)
// or replace it wholesale to tune copy logic without touching the engine.
//
// Behavior in one paragraph:
//   For each observed upstream BUY, score = trader_sharpe_30d × notional.
//   Sort candidates by score, keep the top `maxPerCycle`. For each kept
//   trade, mirror at `notional × copyRatio` USD, clamped to user floor,
//   user ceiling, and the CLOB's per-price min (max($1, 5 × price)).
//   SELLs are always honored to close existing positions. Limit price
//   widens by `slippageBps` toward the fillable side.
//
// To customize: override any single method below. The engine calls them
// in a documented order so partial overrides compose cleanly.

import {
  Strat,
  TraderTrade,
  TraderRoiStats,
  SizeConstraints,
  CandidateDecision,
  tickRoundPrice,
  clobMinNotional,
} from "./base";

export interface CopyTraderOpts {
  /** Max BUYs to copy per cycle. Default 3 — at 5s polling that's 36/min,
      well within Polymarket rate limits and far below the fee-burn cliff. */
  maxPerCycle?: number;
  /** Limit-price widening in basis points toward the fillable side
      (BUY = up, SELL = down). 300 = 3¢ tolerance on a 100¢ market. */
  slippageBps?: number;
  /** Minimum 30d closed-trade count before a trader's Sharpe is trusted.
      Below this the score = 0 and the engine skips with NO_SHARPE. */
  minSampleSize?: number;
}

export class CopyTrader implements Strat {
  readonly name: string = "copytrader";

  private readonly _maxPerCycle: number;
  private readonly slippageBps: number;
  private readonly minSampleSize: number;

  constructor(opts: CopyTraderOpts = {}) {
    this._maxPerCycle = opts.maxPerCycle ?? 3;
    this.slippageBps = opts.slippageBps ?? 300;
    this.minSampleSize = opts.minSampleSize ?? 3;
  }

  // ── Per-cycle cap ──────────────────────────────────────────────

  maxPerCycle(): number {
    return this._maxPerCycle;
  }

  // ── Pre-filter ────────────────────────────────────────────────
  // Override to skip by market, outcome, time-of-day, etc.

  shouldMirror(_trade: TraderTrade): boolean {
    return true;
  }

  // ── Ranking ────────────────────────────────────────────────────
  // Expected profit in DOLLARS = trader's window ROI × the dollars we'd
  // actually deploy on this mirror (raw proportional notional). roi is
  // fractional (0.12 = +12% over the window), so roi × mirror$ is a real
  // dollar expectation, not a unitless score. The engine ranks BUYs by
  // this and copies the top `maxPerCycle`; the same number drives the
  // EP-based capital rotation (sell held positions whose forward EP is
  // below a new buy's EP). A trader with no loaded stats scores 0 — we
  // can't estimate edge, so they never win the budget. Negative-ROI
  // traders score negative and sort to the bottom (engine skips EP ≤ 0).

  scoreCandidate(trade: TraderTrade, stats: TraderRoiStats | null): number {
    if (!stats) return 0;
    const rawMirrorNotional = trade.notional * trade.copyRatio;
    return stats.roi * rawMirrorNotional;
  }

  // ── Sizing + limit price ──────────────────────────────────────
  // Order of clamping is significant:
  //   1. Ceiling can't accommodate CLOB floor          → skip (no legal order)
  //   2. Raw mirror below effective floor + leader real → clamp up
  //   3. Raw mirror below effective floor + leader dust → skip (no signal)
  //   4. Raw mirror above ceiling                       → clamp down
  // The effective floor is max(user TRADE SIZE floor, CLOB per-price min).
  // Proportional dust is clamped UP to that floor (not skipped) so small-
  // but-real leader trades still copy. Returning `mirrorNotional: 0`
  // signals SKIP to the engine; `reason` is shown verbatim in the log.

  sizeAndPrice(trade: TraderTrade, c: SizeConstraints): CandidateDecision {
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

  // ── Limit-price widening ───────────────────────────────────────
  // Override for tighter/aggressive fills. Default widens by
  // slippageBps toward whichever side is fillable from the leader's
  // price. Caller will tick-round to the 1¢ grid.

  protected adjustPrice(trade: TraderTrade): number {
    const bps = this.slippageBps / 10_000;
    if (trade.side === "BUY") return Math.min(trade.price * (1 + bps), 0.99);
    return Math.max(trade.price * (1 - bps), 0.01);
  }
}

// ── Why this lives in one file ──────────────────────────────────
// The class is the entire strategy. To run a different rule:
//   1. Save copytrader.ts as my_strat.ts
//   2. Edit / subclass / replace the methods
//   3. In copyEngine.ts change `new CopyTrader()` → `new MyStrat()`
// No engine changes required. Same shape exists in Python at
// src/strats/copytrader/mod.py for users who prefer to author there.

// Re-export the CLOB floor so the engine can pass it into SizeConstraints
// without depending on base.ts indirectly.
export { clobMinNotional };
