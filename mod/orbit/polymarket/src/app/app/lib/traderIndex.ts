// THE TRADER INDEXER — a bench of traders, copied at YOUR scale.
//
// This is the console's DEFAULT strategy, and the whole idea fits in one line:
//
//     they staked X% of their money on it  →  you stake X% of yours
//
// Nobody sets a dollar amount per trade. You put an amount of capital behind
// the index; the index divides it across its traders; and every trade any of
// them makes is re-sized by the ratio between YOUR slice and THEIR net worth.
// A whale's $50,000 conviction bet and their $200 punt arrive on your book
// 250× apart, which is the only thing worth copying about a whale.
//
//     mirror$  =  their$  ×  (myCapital × weight) / theirBankroll
//
// That expression is `copyRatioFor(..., "bankroll")` in lib/strats/strat.ts —
// the parity-pinned function the Rust live engine (`copy_ratio_for`) and the
// backtest both already size with. This module does NOT re-derive it. It is
// the layer above: naming the model, materializing a strat that uses it,
// and — the part that was missing — being able to SAY, before any money
// moves, what the ratio does to a real trade.
//
// Everything here is pure. No React, no fetch, no localStorage. That is
// deliberate: the strats board, the scale card, the backtest preview and the
// (node) smoke test all need this arithmetic, and a number the UI computes
// differently from the engine is worse than no number.
//
// Companion modules:
//   lib/strats/strat.ts   — copyRatioFor / clobMinNotional (the shared math)
//   lib/defaultStrats.ts  — the TRADER INDEX template that turns this on
//   lib/liveSessions.ts   — fetchTraderBankrolls (the denominators, live)
//   components/IndexScaleCard.tsx — this module, rendered

import type { IndexTrader, SavedIndex } from "./types";
import {
  DEFAULT_MAX_UPSCALE,
  clobMinNotional,
  copyRatioFor,
} from "./strats/strat";

/** Slug of the built-in template this module powers. `forkedFrom` carries it,
    so anything can ask "is this strat a trader index?" without guessing from
    the params. */
export const TRADER_INDEX_SLUG = "trader-index";

/** Traders a fresh index seeds itself with. Eight is the width where the
    per-trader weight (12.5%) still clears the order floor on a four-figure
    account, and where one leader going cold can't sink the index. */
export const TRADER_INDEX_SIZE = 8;

/** Leaderboard window the seed ranks on, in days. */
export const TRADER_INDEX_SEED_DAYS = 7;

/** True when this strat sizes off the capital ratio — i.e. it IS an indexer.
    `sizing` defaults to "bankroll" everywhere downstream (strat.ts,
    live_engine.rs), so an absent value counts. */
export function isTraderIndex(strat: Pick<SavedIndex, "sizing" | "momentum">): boolean {
  if (strat.momentum) return false; // originates its own trades; copies nobody
  return (strat.sizing ?? "bankroll") === "bankroll";
}

// ── The scale of one trader, resolved ───────────────────────────────

/** Why a trader has no usable ratio yet. `null` reason ⇒ the ratio is real. */
export type ScaleGap = "no-capital" | "no-bankroll";

/** One trader's line in the index: what we know about their size, and what
    that makes of a trade of theirs. */
export interface TraderScale {
  address: string;
  /** Their share of the index (0–1). Weights across an index sum to 1. */
  weight: number;
  /** Capital standing behind THIS trader = myCapital × weight. */
  mySlice: number;
  /** Their net worth on Polymarket: positions at mark + free collateral.
      `null` = their book couldn't be read (see fetchTraderBankrolls). */
  bankroll: number | null;
  /** mirror$ / their$. 0 when `gap` is set. */
  ratio: number;
  /** How many times bigger they are than our slice — the human form of the
      ratio (`1 / ratio`). `null` when the ratio is unknown. */
  timesBigger: number | null;
  gap: ScaleGap | null;
}

/** Resolve every trader's scale against one pot of capital.
 *
 *  `weights` come straight off the strat (`SavedIndex.traders[].weight`) and
 *  are RE-NORMALIZED over the enabled traders only — a strat whose weights
 *  were built for ten traders and then had two disabled must still deploy all
 *  of its capital, not 80% of it. Disabled traders are dropped, not zeroed. */
export function scaleIndex(
  traders: IndexTrader[],
  myCapital: number,
  bankrolls: Map<string, number> | Record<string, number>,
): TraderScale[] {
  const get = (a: string): number | null => {
    const key = a.toLowerCase();
    const v = bankrolls instanceof Map ? bankrolls.get(key) : bankrolls[key];
    return typeof v === "number" && v >= 1 ? v : null;
  };

  const live = traders.filter((t) => t.enabled !== false);
  const total = live.reduce((s, t) => s + (t.weight ?? 0), 0);

  return live.map((t) => {
    // A watchlist saved with no weights at all (or all zeros) is an EQUAL
    // index, not an empty one — otherwise adding traders by hand silently
    // produces a strat that trades nothing.
    const weight = total > 0 ? (t.weight ?? 0) / total : 1 / Math.max(live.length, 1);
    const mySlice = Math.max(myCapital, 0) * weight;
    const bankroll = get(t.address);

    if (mySlice <= 0) {
      return { address: t.address, weight, mySlice, bankroll, ratio: 0, timesBigger: null, gap: "no-capital" as const };
    }
    if (bankroll === null) {
      return { address: t.address, weight, mySlice, bankroll, ratio: 0, timesBigger: null, gap: "no-bankroll" as const };
    }
    // ONE definition of the ratio, borrowed from the engine. `weightFraction`
    // is already folded into `mySlice`, so pass 1 — passing both would square
    // the weight and quietly under-size every multi-trader index.
    const ratio = copyRatioFor(mySlice, 1, bankroll, mySlice, 0, "bankroll", 1);
    return {
      address: t.address,
      weight,
      mySlice,
      bankroll,
      ratio,
      timesBigger: ratio > 0 ? 1 / ratio : null,
      gap: null,
    };
  });
}

// ── What the ratio does to an actual trade ──────────────────────────

/** Verdict on one projected mirror. Mirrors the live engine's own vocabulary
    so a preview and a session log read the same. */
export type MirrorVerdict = "placed" | "upscaled" | "sub-scale" | "capped" | "unknown";

export interface ProjectedMirror {
  /** What proportionality asked for, before any floor or cap. */
  proportional: number;
  /** What would actually be sent to the CLOB. 0 ⇒ nothing is placed. */
  notional: number;
  /** The CLOB's own hard floor at this price: max($1, 5 shares × price). */
  floor: number;
  verdict: MirrorVerdict;
  /** One sentence, in the second person, for the UI. */
  note: string;
}

const money = (v: number) =>
  `$${v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

/** Project ONE of their trades onto our book.
 *
 *  This is the honest preview: it applies the same three constraints the
 *  engine applies, in the same order.
 *
 *   1. proportional = their notional × ratio
 *   2. the CLOB floor — Polymarket refuses orders under max($1, 5×price).
 *      A mirror below it is either rounded UP (if that stays within
 *      `maxUpscale` of what proportionality wanted) or REFUSED as SUB_SCALE.
 *      `maxUpscale: 0 | null` ⇒ unbounded: every mirror is rounded up and
 *      placed, coverage is 100% and proportionality is gone.
 *   3. our own per-order ceiling (`maxTrade`), which caps and never skips.
 *
 *  The SUB_SCALE case is the one worth showing a user before they fund: it is
 *  exactly how a small account "copies" a whale and places nothing at all. */
export function projectMirror(
  theirNotional: number,
  scale: TraderScale,
  opts: { price?: number; minTrade?: number; maxTrade?: number; maxUpscale?: number | null } = {},
): ProjectedMirror {
  const price = opts.price ?? 0.5;
  const floor = Math.max(clobMinNotional(price), Math.max(opts.minTrade ?? 0, 0));
  const ceiling = opts.maxTrade && opts.maxTrade > 0 ? opts.maxTrade : Infinity;
  const upscale = opts.maxUpscale === undefined ? DEFAULT_MAX_UPSCALE : opts.maxUpscale;

  if (scale.gap === "no-capital") {
    return { proportional: 0, notional: 0, floor, verdict: "unknown", note: "Put capital behind the index and this fills in." };
  }
  if (scale.gap === "no-bankroll") {
    return { proportional: 0, notional: 0, floor, verdict: "unknown", note: "We couldn't read this trader's book yet — their size is unknown." };
  }

  const proportional = Math.max(theirNotional, 0) * scale.ratio;

  if (proportional >= floor) {
    const notional = Math.min(proportional, ceiling);
    return notional < proportional
      ? { proportional, notional, floor, verdict: "capped", note: `Proportional size is ${money(proportional)}; your per-order cap holds it to ${money(notional)}.` }
      : { proportional, notional, floor, verdict: "placed", note: `Their ${money(theirNotional)} becomes ${money(notional)} on your book.` };
  }

  // Under the floor. Unbounded upscale places it anyway; a bounded one only
  // places it when the floor is within `maxUpscale` × the honest size.
  const unbounded = upscale === null || upscale === 0;
  if (unbounded || (typeof upscale === "number" && floor <= proportional * upscale)) {
    const notional = Math.min(floor, ceiling);
    return {
      proportional,
      notional,
      floor,
      verdict: "upscaled",
      note: `Proportionality asked for ${money(proportional)} — under Polymarket's ${money(floor)} minimum, so it goes in at ${money(notional)}.`,
    };
  }
  return {
    proportional,
    notional: 0,
    floor,
    verdict: "sub-scale",
    note: `Proportionality asked for ${money(proportional)}, and Polymarket won't take an order under ${money(floor)} — this trade is skipped.`,
  };
}

/** The size of THEIR trade at which our mirror first clears the floor: below
    this, their trades are invisible to us. The single most useful number on
    the scale card — it says "you only see their big ones", with a number. */
export function visibilityThreshold(
  scale: TraderScale,
  opts: { price?: number; minTrade?: number; maxUpscale?: number | null } = {},
): number | null {
  if (scale.gap !== null || scale.ratio <= 0) return null;
  const floor = Math.max(clobMinNotional(opts.price ?? 0.5), Math.max(opts.minTrade ?? 0, 0));
  const upscale = opts.maxUpscale === undefined ? DEFAULT_MAX_UPSCALE : opts.maxUpscale;
  // With upscaling allowed, a mirror survives down to floor / maxUpscale.
  if (upscale === null || upscale === 0) return 0;
  return floor / (upscale * scale.ratio);
}

/** Capital at which EVERY trade of theirs down to `theirSmallest` survives —
    the answer to "so how much do I need to actually track this person?".
    Returns null when their bankroll is unknown. */
export function capitalToTrack(
  scale: TraderScale,
  theirSmallest: number,
  opts: { price?: number; minTrade?: number; maxUpscale?: number | null } = {},
): number | null {
  if (scale.bankroll === null || scale.weight <= 0 || theirSmallest <= 0) return null;
  const floor = Math.max(clobMinNotional(opts.price ?? 0.5), Math.max(opts.minTrade ?? 0, 0));
  const upscale = opts.maxUpscale === undefined ? DEFAULT_MAX_UPSCALE : opts.maxUpscale;
  const effectiveFloor = upscale === null || upscale === 0 ? 0 : floor / upscale;
  if (effectiveFloor <= 0) return 0;
  // theirSmallest × (capital × weight) / bankroll ≥ effectiveFloor
  return (effectiveFloor * scale.bankroll) / (theirSmallest * scale.weight);
}

// ── Formatting (one vocabulary for every screen that shows a ratio) ──

/** "1 : 2,400" — how our slice compares to their book. */
export function formatScale(scale: TraderScale): string {
  if (scale.timesBigger === null) return "—";
  const n = scale.timesBigger;
  if (n < 1) return `${(1 / n).toFixed(1)} : 1 (you're bigger)`;
  if (n < 1000) return `1 : ${n.toFixed(n < 10 ? 1 : 0)}`;
  return `1 : ${Math.round(n).toLocaleString("en-US")}`;
}

/** "$2.4k" / "$183" — bankrolls span five orders of magnitude, so a plain
    toFixed(2) makes the column unreadable. */
export function formatCompactUsd(v: number | null): string {
  if (v === null || !Number.isFinite(v)) return "—";
  const abs = Math.abs(v);
  if (abs >= 1_000_000) return `$${(v / 1_000_000).toFixed(1)}M`;
  if (abs >= 1_000) return `$${(v / 1_000).toFixed(abs >= 100_000 ? 0 : 1)}k`;
  if (abs >= 1) return `$${v.toFixed(0)}`;
  return `$${v.toFixed(2)}`;
}

/** Roll-up for a whole index — what the board shows on one line. */
export interface IndexScaleSummary {
  /** Traders whose bankroll we could read. */
  known: number;
  /** Traders we're still missing a denominator for. */
  unknown: number;
  /** Combined net worth of the readable traders. */
  benchBankroll: number;
  /** myCapital / benchBankroll, restricted to readable traders — the index's
      headline ratio. `null` when nothing is readable. */
  ratio: number | null;
  /** Their smallest trade that still reaches our book, worst case across the
      bench (the LEAST visible trader sets it). `null` when unknown. */
  worstThreshold: number | null;
}

export function summarizeIndex(
  scales: TraderScale[],
  opts: { price?: number; minTrade?: number; maxUpscale?: number | null } = {},
): IndexScaleSummary {
  const known = scales.filter((s) => s.bankroll !== null);
  const benchBankroll = known.reduce((s, x) => s + (x.bankroll ?? 0), 0);
  const mine = known.reduce((s, x) => s + x.mySlice, 0);
  const thresholds = known
    .map((s) => visibilityThreshold(s, opts))
    .filter((v): v is number => v !== null);
  return {
    known: known.length,
    unknown: scales.length - known.length,
    benchBankroll,
    ratio: benchBankroll > 0 && mine > 0 ? mine / benchBankroll : null,
    worstThreshold: thresholds.length > 0 ? Math.max(...thresholds) : null,
  };
}
