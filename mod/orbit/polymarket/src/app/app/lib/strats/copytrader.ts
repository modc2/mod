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
  // Sharpe-weighted expected edge. Sharpe = roi_30d / stdev_30d
  // (unitless, rewards consistency over lucky single hits). Multiplying
  // by notional gives "dollars of expected profit if the trader behaves
  // like their 30d Sharpe predicts".

  scoreCandidate(trade: TraderTrade, stats: TraderRoiStats | null): number {
    if (!stats) return 0;
    if (stats.sampleSize < this.minSampleSize) return 0;
    if (stats.sharpe <= 0) return 0;
    return stats.sharpe * trade.notional;
  }

  // ── Sizing + limit price ──────────────────────────────────────
  // Order of clamping is significant:
  //   1. Ceiling can't accommodate CLOB floor       → skip (no legal order)
  //   2. Raw mirror below user floor                → skip (dust)
  //   3. Raw mirror below CLOB floor + leader real  → clamp up
  //   4. Raw mirror above ceiling                   → clamp down
  // Returning `mirrorNotional: 0` signals SKIP to the engine; `reason`
  // is shown verbatim in the log.

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
    // (2) Below the user's TRADE SIZE floor — strat-level dust gate.
    if (rawMirrorNotional < c.userFloor) {
      return {
        mirrorNotional: 0,
        limitPrice,
        reason: `BELOW_MIN_SIZE · proportional $${rawMirrorNotional.toFixed(2)} < floor $${c.userFloor.toFixed(2)}`,
      };
    }
    // (3) Below CLOB floor — bump up unless the leader's own trade is
    //     also below the floor (then it's not real signal, skip).
    let mirrorNotional = rawMirrorNotional;
    let reason: string | undefined;
    if (rawMirrorNotional < c.clobFloor) {
      if (trade.notional < c.clobFloor) {
        return {
          mirrorNotional: 0,
          limitPrice,
          reason: `LEADER_DUST · leader $${trade.notional.toFixed(2)} < Polymarket $${c.clobFloor.toFixed(2)} hard floor (5 shares × ${(trade.price * 100).toFixed(0)}¢)`,
        };
      }
      mirrorNotional = c.clobFloor;
      reason = `clamped up: proportional $${rawMirrorNotional.toFixed(2)} → $${c.clobFloor.toFixed(2)} (CLOB min: 5 shares × ${(trade.price * 100).toFixed(0)}¢)`;
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
