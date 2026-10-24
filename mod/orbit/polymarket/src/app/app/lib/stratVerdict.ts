// THE STRAT VERDICT — is this strat consistently making money, or not?
//
// The MY STRATS grid used to make the user read five ladder cells, two PnL
// columns and a curve per card to answer the only question they came with:
// "which of these is actually working?" ("i only have time to see the strats
// that are doing well"). This folds everything a card knows into ONE call:
//
//   CONSISTENT  green across (nearly) all the evidence — the ladder windows
//               that traded, the live book if it has traded — and not exposed
//               as a selection leak by the holdout check. The ones worth the
//               user's time.
//   MIXED       real evidence, pointing both ways.
//   BLEEDING    the evidence is mostly red.
//   UNKNOWN     not enough samples to say anything (new strat, empty roster,
//               worker hasn't replayed it). Never dressed up as a zero.
//
// Samples are SIGNS, not magnitudes: one lucky 10-bagger window must not buy
// back four red ones (that's the lottery-ticket shape WinRecord exists to
// catch). Magnitude only breaks ties inside a tier, via `score`.
//
// The holdout check outranks the headline. A +469%/7d strat with a negative
// holdout is a strat that picked its roster AFTER watching the week happen
// (see polymarket_consistent_bundle_1009) — it can never rank CONSISTENT, no
// matter how green the ladder looks.

import { HUB_WINDOWS } from "./hubReplay";
import type { WindowRow } from "./hubBacktest";
import type { StratMoney } from "./stratStats";

export type VerdictTier = "consistent" | "mixed" | "bleeding" | "unknown";

export interface StratVerdict {
  tier: VerdictTier;
  /** Sort key within and across tiers — higher is better. Tier sets the
      thousands digit so CONSISTENT always outranks MIXED outranks UNKNOWN
      outranks BLEEDING, whatever the magnitudes. */
  score: number;
  /** Evidence counted: green samples / total decided samples. */
  wins: number;
  samples: number;
  /** What the train/test split said about the widest window that has one.
      "leak" = headline green but the honestly-picked roster lost. */
  holdout: "confirmed" | "leak" | null;
  /** One line of why, for the chip's tooltip. */
  reason: string;
}

/** PnL within this band of flat is noise, not a loss or a win —
    a $0.40 drift must not flip a verdict. */
const FLAT_USD = 1;

/** A window that lost this much of its capital disqualifies CONSISTENT on
    its own, whatever the other windows say — "consistently good" cannot
    include a stretch that vaporized a quarter of the stake (the first cut of
    this verdict ranked a strat with a −91% 30D window CONSISTENT off four
    green short windows). */
const MAX_WINDOW_LOSS_ROI = -25;

/** The strat's own saved backtest — `lastPnl` on the card. When it was an
    out-of-sample run (score-fn strats: roster picked BEFORE the test window),
    it carries holdout weight: an OOS loss under a green ladder is the
    selection-leak signature. */
export interface LastBacktest {
  pnl: number;
  trades: number;
  oos: boolean;
}

const TIER_RANK: Record<VerdictTier, number> = {
  consistent: 3, mixed: 2, unknown: 1, bleeding: 0,
};

export function computeStratVerdict(
  row: WindowRow | undefined,
  live: StratMoney | undefined,
  last?: LastBacktest | null,
): StratVerdict {
  let wins = 0;
  let losses = 0;
  let roiSum = 0;
  let deepLoss = false;
  let holdout: "confirmed" | "leak" | null = null;

  // Ladder samples: every window that actually traded is one sign. Stale
  // cells (strat edited since the replay) and warming cells (feed cache still
  // filling) describe a different strat / partial data — skipped, not scored.
  for (const d of HUB_WINDOWS) {
    const bt = row?.[d];
    if (!bt || bt.trades === 0 || bt.stale || bt.warming) continue;
    if (bt.pnl > 0) wins++;
    else if (bt.pnl < 0) losses++;
    else continue; // exactly flat decides nothing
    if (bt.roi <= MAX_WINDOW_LOSS_ROI) deepLoss = true;
    roiSum += Math.max(-50, Math.min(50, bt.roi));
    // Widest decided window wins the holdout slot — the longest honest test.
    if (bt.holdout && bt.holdout.trades > 0) {
      holdout = bt.holdout.pnl > 0 ? "confirmed" : bt.pnl > 0 ? "leak" : holdout;
    }
  }

  // The card's own saved backtest is one more sample; an OOS run also
  // carries holdout weight — it IS a train/test split (roster picked before
  // the window it was graded on), so a red one under a green ladder is the
  // same leak the holdout check catches.
  if (last && last.trades > 0 && Math.abs(last.pnl) >= FLAT_USD) {
    if (last.pnl > 0) wins++; else losses++;
    if (last.oos) {
      if (last.pnl < 0) holdout = "leak";
      else if (holdout !== "leak") holdout = "confirmed";
    }
  }

  // The live book is the realest sample there is — it counts double.
  const liveTraded = (live?.fills ?? 0) > 0 || (live?.openPositions ?? 0) > 0;
  const livePnl = live?.totalPnl ?? 0;
  if (liveTraded && Math.abs(livePnl) >= FLAT_USD) {
    if (livePnl > 0) wins += 2; else losses += 2;
    roiSum += Math.max(-50, Math.min(50, (live?.pnlPct ?? 0) * 2));
  }

  const samples = wins + losses;
  const winFrac = samples > 0 ? wins / samples : 0;

  let tier: VerdictTier;
  if (samples < 2) tier = "unknown";
  else if (winFrac >= 0.75 && wins >= 2 && holdout !== "leak" && !deepLoss) tier = "consistent";
  else if (winFrac <= 1 / 3) tier = "bleeding";
  else tier = "mixed";

  const bits: string[] = [];
  if (samples >= 2) bits.push(`${wins}/${samples} samples green (live counts double)`);
  else bits.push("not enough data to judge — needs 2+ decided samples");
  if (liveTraded && Math.abs(livePnl) >= FLAT_USD) {
    bits.push(`live ${livePnl > 0 ? "+" : "−"}$${Math.abs(livePnl).toFixed(2)}`);
  }
  if (deepLoss) bits.push(`one window lost over ${-MAX_WINDOW_LOSS_ROI}% of its capital — blocked from CONSISTENT`);
  if (holdout === "confirmed") bits.push("out-of-sample confirmed — the edge survives an honest roster pick");
  if (holdout === "leak") bits.push("out-of-sample FAILED — green headline, but a roster picked without hindsight lost. Treat the gains as a leak.");

  return {
    tier,
    score: TIER_RANK[tier] * 1000
      + winFrac * 100
      + (holdout === "confirmed" ? 25 : holdout === "leak" ? -25 : 0)
      + (samples > 0 ? roiSum / samples : 0),
    wins,
    samples,
    holdout,
    reason: bits.join(" · "),
  };
}
