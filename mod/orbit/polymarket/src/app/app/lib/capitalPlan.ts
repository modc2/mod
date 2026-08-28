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
// And the number has to be spendable. Copying whales asks for capital nobody
// reading this has ("run it with $200k"), so the plan also answers the
// question with the money actually in the deposit wallet: `feasibleCapital` is
// the recommendation capped at what's on hand, and `minCapital` is the level
// below which the strat places NOTHING at all — the line between "small" and
// "dead".
//
// Everything here reads the SAME constants the engine sizes with
// (clobMinNotional, DEFAULT_MAX_UPSCALE) — if sizing changes, the plan moves
// with it instead of drifting into a marketing number.

import { clobMinNotional, DEFAULT_MAX_UPSCALE, DEFAULT_TURNOVER } from "./strats/strat";
import type { SizingModel } from "./strats/strat";

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
  /** Money actually on hand (deposit-wallet USDC, USD). The recommendation is
      capped at this — advice you can't fund is not advice. null/undefined ⇒
      unknown (no wallet read), and the plan stays uncapped. */
  available?: number | null;
  /** TRADE SIZE min / max (USD). */
  minTrade: number;
  maxTrade: number;
  /** Proportional-fidelity limit; undefined ⇒ 2, 0/null ⇒ unbounded. */
  maxUpscale?: number | null;
  /** Sizing model the strat runs — the plan must divide by the same
      denominator the engine will. Undefined ⇒ "bankroll". */
  sizing?: SizingModel;
  /** "flow" only — allocation redeployments per window. Undefined ⇒ 1. */
  turnover?: number;
  /** Window the trades were collected over (days) — turns counts into rates. */
  days: number;
}

export interface CapitalPlan {
  /** Leader trades the plan is built from. 0 ⇒ every field is a guess; the
      UI must say "not enough flow" rather than print a number. */
  sampleSize: number;
  /** Capital at which HALF the observed flow is copyable in proportion. */
  medianCapital: number;
  /** Capital at which ~3 of 4 trades are copyable — the IDEAL. */
  recommendedCapital: number;
  /** The recommendation capped at the money on hand — THE number the UI leads
      with, because it's the one that can be funded today. Equals
      `recommendedCapital` when funds are unknown or sufficient. */
  feasibleCapital: number;
  /** Fraction (0–1) of the flow copyable at `feasibleCapital`. */
  feasibleCoverage: number;
  /** True when `available` is what's holding the number down. */
  cappedByAvailable: boolean;
  /** Capital at which the FIRST trade in the flow becomes copyable. Below
      this the strat places nothing at all, at any odds. */
  minCapital: number;
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
  const vol = Math.max(input.traderVolume.get(t.trader) ?? 0, 1);
  // Flow sizing is linear in C too — capitalAlloc is C × wFrac — so the whole
  // plan still holds, it just divides by the leader's deployed capital. That
  // denominator is orders of magnitude smaller than a bankroll, which is
  // exactly why the plan's answer drops from "thousands" to "hundreds".
  if (input.sizing === "flow") {
    const turnover = input.turnover === undefined ? DEFAULT_TURNOVER : input.turnover;
    return (t.notional * wFrac * Math.max(turnover, 0)) / vol;
  }
  const bankroll = input.bankrolls.get(t.trader);
  if (typeof bankroll === "number" && bankroll >= 1) return (t.notional * wFrac) / bankroll;
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
    // Unbounded upscale (UPSCALE ∞) ⇒ nothing is ever skipped for being small:
    // a sub-floor mirror is rounded up and placed. Capital stops deciding
    // WHICH trades copy and only decides whether there's cash to pay for one,
    // so the requirement collapses to the order floor itself. (Reporting 0
    // here would be arithmetically true and useless — "RUN IT WITH $0".)
    const need = maxUpscale && maxUpscale > 0 ? floor / (maxUpscale * k) : floor;
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

  // Cap the advice at the money on hand. Kept to the cent rather than rounded
  // to a nice figure — this one is a balance, not a suggestion, and rounding
  // it UP would recommend spending money that isn't there.
  const available = typeof input.available === "number" && Number.isFinite(input.available) && input.available > 0
    ? Math.floor(input.available * 100) / 100
    : 0;
  const cappedByAvailable = available > 0 && available < recommended;
  const feasible = cappedByAvailable ? available : recommended;

  return {
    sampleSize: sortedReq.length,
    medianCapital: niceAmount(median),
    recommendedCapital: recommended,
    feasibleCapital: feasible,
    feasibleCoverage: coverageAt(feasible),
    cappedByAvailable,
    minCapital: sortedReq.length > 0 ? sortedReq[0] : 0,
    saturationCapital: niceAmount(quantile(sortedSat, 0.5)),
    coverage: coverageAt(input.capital),
    recommendedCoverage: coverageAt(recommended),
    ordersPerDay: copyableNow / days,
    deployPerDay: deployNow / days,
    medianFloor: quantile(sortedFloors, 0.5),
    ceilingBinds: recommended > 0 && quantile(sortedSat, 0.5) < recommended,
  };
}
