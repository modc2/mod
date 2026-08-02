// HOW MUCH MONEY TO RUN A STRAT WITH.
//
// Proportional copying makes this a solvable arithmetic problem rather than a
// vibe. Every mirror is `leaderNotional × copyRatio`, and every copyRatio is
// LINEAR in the account value (`copyRatioFor`: accountValue × weightFraction /
// leaderBankroll, or the capital/volume fallback). So for one observed leader
// trade there is exactly one capital level at which our mirror of it stops
// being too small to place:
//
//     mirror(C) = C × k       k = leaderNotional × weightFraction / bankroll
//     placeable ⇔ mirror(C) ≥ minOrder / maxUpscale
//     ⇒ C_required = minOrder / (maxUpscale × k)
//
// `minOrder` is the same effective floor `sizeAndPrice` clamps to —
// max(TRADE SIZE min, CLOB's max($1, 5 × price)) — and `maxUpscale` is the
// proportional-fidelity limit (2× by default: a mirror may be rounded up to
// the floor, but only so far, everything smaller is skipped as SUB_SCALE).
//
// Run that over the strat's own recent, already-filtered flow and the
// distribution of C_required answers the question directly:
//
//   • median  → the capital that copies HALF the flow in proportion
//   • p75     → the capital that copies MOST of it  (the recommendation)
//   • current → what fraction of the flow the current allocation copies
//
// The upper bound matters too: past `saturationCapital` the proportional size
// exceeds the TRADE SIZE ceiling on most trades, so extra dollars stop buying
// bigger mirrors and just sit as idle cash.
//
// Everything here reads the SAME constants the engine sizes with
// (clobMinNotional, DEFAULT_MAX_UPSCALE) — if sizing changes, the plan moves
// with it instead of drifting into a marketing number.

import { clobMinNotional, DEFAULT_MAX_UPSCALE } from "./strats/strat";

/** One observed leader BUY, already through the strat's filters. */
export interface PlanTrade {
  /** Lowercased trader address — picks the sizing denominator. */
  trader: string;
  /** Leader's USD notional (price × size). */
  notional: number;
  /** Leader's fill price (0–1) — sets the CLOB per-price floor. */
  price: number;
  timestamp: number;
}

export interface CapitalPlanInput {
  trades: PlanTrade[];
  /** Trader address (lowercase) → weight fraction of the watchlist. */
  weightFraction: Map<string, number>;
  /** Trader address (lowercase) → bankroll (positions + cash). Missing ⇒ the
      volume fallback below is used, exactly like `copyRatioFor`. */
  bankrolls: Map<string, number>;
  /** Trader address (lowercase) → window volume, the fallback denominator. */
  traderVolume: Map<string, number>;
  /** Strat's current allocation (USD). */
  capital: number;
  /** TRADE SIZE min / max (USD). */
  minTrade: number;
  maxTrade: number;
  /** Proportional-fidelity limit; undefined ⇒ 2, 0/null ⇒ unbounded. */
  maxUpscale?: number | null;
  /** Window the trades were collected over (days) — turns counts into rates. */
  days: number;
}

export interface CapitalPlan {
  /** Leader trades the plan is built from. 0 ⇒ every field is a guess; the
      UI must say "not enough flow" rather than print a number. */
  sampleSize: number;
  /** Capital at which HALF the observed flow is copyable in proportion. */
  medianCapital: number;
  /** Capital at which ~3 of 4 trades are copyable — THE recommendation. */
  recommendedCapital: number;
  /** Above this, the proportional size of the median trade exceeds the TRADE
      SIZE ceiling: more capital no longer means bigger mirrors. */
  saturationCapital: number;
  /** Fraction (0–1) of observed trades copyable at `capital` today. */
  coverage: number;
  /** …and the same at `recommendedCapital`. */
  recommendedCoverage: number;
  /** Trades/day the strat would actually place at `capital`. */
  ordersPerDay: number;
  /** USD/day it would deploy at `capital` (clamped mirror sizes). */
  deployPerDay: number;
  /** Median effective order floor across the flow — the smallest legal order
      this strat can place, in practice. */
  medianFloor: number;
  /** True when the TRADE SIZE ceiling is below what proportionality asks for
      on most trades even at the recommendation — the strat is capped by
      `maxTrade`, not by capital. */
  ceilingBinds: boolean;
}

/** Nearest sensible dollar figure at or above `n` — 1 / 2 / 5 × 10ⁿ. Capital
    advice with 3 significant figures reads like false precision. */
export function niceAmount(n: number): number {
  if (!Number.isFinite(n) || n <= 0) return 0;
  const mag = Math.pow(10, Math.floor(Math.log10(n)));
  for (const step of [1, 2, 5, 10]) {
    if (n <= step * mag) return step * mag;
  }
  return 10 * mag;
}

function quantile(sorted: number[], q: number): number {
  if (sorted.length === 0) return 0;
  const i = Math.min(sorted.length - 1, Math.max(0, Math.floor(q * (sorted.length - 1))));
  return sorted[i];
}

/** The per-dollar copy coefficient `k`: what fraction of ONE dollar of account
    value this trade would mirror. Mirrors `copyRatioFor` — bankroll model
    first, volume fallback when the leader's book can't be read. */
function coefficient(t: PlanTrade, input: CapitalPlanInput): number {
  const wFrac = input.weightFraction.get(t.trader) ?? 0;
  if (wFrac <= 0) return 0;
  const bankroll = input.bankrolls.get(t.trader);
  if (typeof bankroll === "number" && bankroll >= 1) return (t.notional * wFrac) / bankroll;
  const vol = Math.max(input.traderVolume.get(t.trader) ?? 0, 1);
  return (t.notional * wFrac) / vol;
}

/** Build the plan. Pure arithmetic over the flow the caller already filtered —
    no fetches, no clock. */
export function planCapital(input: CapitalPlanInput): CapitalPlan {
  const maxUpscale = input.maxUpscale === undefined ? DEFAULT_MAX_UPSCALE : input.maxUpscale;
  const days = Math.max(input.days, 1 / 24);

  const required: number[] = [];   // C at which each trade becomes copyable
  const saturation: number[] = []; // C at which each trade hits the ceiling
  const floors: number[] = [];
  let copyableNow = 0;
  let deployNow = 0;

  for (const t of input.trades) {
    const k = coefficient(t, input);
    if (!(k > 0)) continue;
    const floor = Math.max(input.minTrade, clobMinNotional(t.price));
    floors.push(floor);
    // A leader trade under the CLOB floor can never be copied at any size —
    // it is dust, not a capital problem (`LEADER_DUST` in sizeAndPrice).
    if (t.notional < clobMinNotional(t.price)) continue;
    // Unbounded upscale ⇒ any capital clamps up to the floor, so the trade is
    // copyable from dollar one and contributes no capital requirement.
    const need = maxUpscale && maxUpscale > 0 ? floor / (maxUpscale * k) : 0;
    required.push(need);
    saturation.push(input.maxTrade / k);
    if (input.capital >= need) {
      copyableNow++;
      deployNow += Math.min(Math.max(input.capital * k, floor), input.maxTrade);
    }
  }

  const sortedReq = [...required].sort((a, b) => a - b);
  const sortedSat = [...saturation].sort((a, b) => a - b);
  const sortedFloors = [...floors].sort((a, b) => a - b);
  const median = quantile(sortedReq, 0.5);
  const p75 = quantile(sortedReq, 0.75);
  const recommended = niceAmount(p75);
  const coverageAt = (c: number) =>
    sortedReq.length === 0 ? 0 : sortedReq.filter((r) => c >= r).length / sortedReq.length;

  return {
    sampleSize: sortedReq.length,
    medianCapital: niceAmount(median),
    recommendedCapital: recommended,
    saturationCapital: niceAmount(quantile(sortedSat, 0.5)),
    coverage: coverageAt(input.capital),
    recommendedCoverage: coverageAt(recommended),
    ordersPerDay: copyableNow / days,
    deployPerDay: deployNow / days,
    medianFloor: quantile(sortedFloors, 0.5),
    ceilingBinds: recommended > 0 && quantile(sortedSat, 0.5) < recommended,
  };
}
