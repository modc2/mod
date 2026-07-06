// CopyTrader — the reference Strat subclass.
//
// Mirrors each watched trader's fills, weighted by their window ROI and
// capped per-cycle to control fees. Almost everything is inherited from
// the base class — the ONLY behavior this class adds is slippage-widened
// limit pricing. That's the point: a strategy is a class, and a subclass
// overrides only the hooks it changes.
//
// Behavior in one paragraph:
//   For each observed upstream BUY, score = trader_roi × mirror$ (base
//   default). Sort candidates by score, keep the top `maxPerCycle`. For
//   each kept trade, mirror at `notional × copyRatio` USD, clamped to user
//   floor, user ceiling, and the CLOB's per-price min (base default).
//   SELLs are always honored to close existing positions. Limit price
//   widens by `slippageBps` toward the fillable side (this class).
//
// For a strat that ORIGINATES trades from history instead of mirroring,
// see flowmomentum.ts.

import { Strat, StratParams, TraderTrade } from "./base";

export interface CopyTraderOpts extends StratParams {
  /** Limit-price widening in basis points toward the fillable side
      (BUY = up, SELL = down). 300 = 3¢ tolerance on a 100¢ market. */
  slippageBps?: number;
  /** Minimum 30d closed-trade count before a trader's Sharpe is trusted.
      Below this the score = 0 and the engine skips with NO_SHARPE. */
  minSampleSize?: number;
}

export class CopyTrader extends Strat<CopyTraderOpts> {
  readonly name: string = "copytrader";

  // ── Limit-price widening ───────────────────────────────────────
  // The one override. Base sizeAndPrice calls this then tick-rounds.
  // Widens by slippageBps toward whichever side is fillable from the
  // leader's price so mirrors don't sit unfilled behind the market.

  protected adjustPrice(trade: TraderTrade): number {
    const bps = (this.params.slippageBps ?? 300) / 10_000;
    if (trade.side === "BUY") return Math.min(trade.price * (1 + bps), 0.99);
    return Math.max(trade.price * (1 - bps), 0.01);
  }
}

// ── Why this lives in one file ──────────────────────────────────
// The class is the entire strategy. To run a different rule:
//   1. Copy this file (or flowmomentum.ts) to my_strat.ts
//   2. Override any subset of the five hooks (base.ts documents them)
//   3. Register the class in registry.ts
// No engine changes required. Same shape exists in Python at
// src/strats/copytrader/mod.py for users who prefer to author there.

// Re-export the CLOB floor so the engine can pass it into SizeConstraints
// without depending on base.ts indirectly.
export { clobMinNotional } from "./base";
