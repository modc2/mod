// THE STEADY SCORE — ranks the COPY STUDIO board by how CONSISTENT copying a
// trader would have been, not by how much they happened to make.
//
// Every input is read off the copy replay itself (the Sleeve from
// lib/basketSim.ts — your size, fees, order floor), never off the trader's
// own leaderboard P&L: a whale's +$400K means nothing if their fills land
// below the order floor at $100.
//
// Five parts, each 0–1, blended by user-set weights into 0–100:
//
//   SMOOTH   how straight the copied equity line climbed — R² of a line fit
//            to the curve, resampled to even time steps, 0 if it slopes down.
//            A staircase scores ~1, one lucky spike scores low.
//   SHALLOW  how small the worst peak-to-trough drop was (0% → 1, ≥30% → 0).
//   RETURN   the copied return, squashed: 0% → 0.5, +20% → 0.88, −20% → 0.12.
//   SAMPLE   how many copied trades it rests on (full credit at 20). Three
//            lucky fills are not a track record.
//   SETTLED  share of the result from markets that actually resolved, vs.
//            marked at the last price (an open mark can still go to $0).
//
// The default weights lean on SMOOTH and SHALLOW because those are the parts
// that carry forward — past return is the least repeatable thing on the
// board. The weights are shown and editable beside the board. Pure: no React.

import type { Sleeve } from "./basketSim";

export const SCORE_PARTS = ["smooth", "shallow", "ret", "sample", "settled"] as const;
export type ScorePart = (typeof SCORE_PARTS)[number];
export type ScoreWeights = Record<ScorePart, number>;

export const DEFAULT_SCORE_WEIGHTS: ScoreWeights = {
  smooth: 30, shallow: 25, ret: 20, sample: 15, settled: 10,
};

export const SCORE_PART_LABEL: Record<ScorePart, string> = {
  smooth: "SMOOTH", shallow: "SHALLOW", ret: "RETURN", sample: "SAMPLE", settled: "SETTLED",
};

export const SCORE_PART_HINT: Record<ScorePart, string> = {
  smooth: "How straight the copied equity line climbed (R² of a straight-line fit; 0 if it went down). Steady staircase = high, one lucky jump = low.",
  shallow: "How small the worst drop from a peak was. 0% drop = full marks, 30% or worse = none.",
  ret: "The copied return itself, squashed so it can't dominate: 0% = half marks, +20% ≈ 0.88, −20% ≈ 0.12.",
  sample: "How many trades you would have copied. Full marks at 20 — a few lucky fills aren't a track record.",
  settled: "How much of the result comes from markets that actually resolved, instead of open positions marked at the last price.",
};

export const SCORE_STORAGE_KEY = "poly:copyStudio:score:v1";

/** Full SAMPLE credit at this many copied trades. */
export const SAMPLE_FULL = 20;
/** A drop this deep (%) scores 0 on SHALLOW. */
export const DRAWDOWN_ZERO = 30;
/** RETURN = 0.5 + 0.5·tanh(pct / RETURN_SCALE). */
export const RETURN_SCALE = 20;
const RESAMPLE = 24;

export interface ScoreBreakdown {
  /** 0–100, or null when there's nothing to judge (idle / never traded). */
  score: number | null;
  parts: Record<ScorePart, number>;
}

const clamp01 = (v: number) => (Number.isFinite(v) ? Math.min(1, Math.max(0, v)) : 0);

/** Equity values at RESAMPLE even time steps (last value at-or-before each
    step) — trade-dense stretches must not dominate the fit. */
export function resampleEquity(equity: { t: number; liq: number; pos: number }[], n = RESAMPLE): number[] {
  if (equity.length < 2) return equity.map((p) => p.liq + p.pos);
  const t0 = equity[0].t;
  const t1 = equity[equity.length - 1].t;
  if (!(t1 > t0)) return [equity[0].liq + equity[0].pos, equity[equity.length - 1].liq + equity[equity.length - 1].pos];
  const out: number[] = [];
  let j = 0;
  for (let i = 0; i < n; i++) {
    const t = t0 + ((t1 - t0) * i) / (n - 1);
    while (j + 1 < equity.length && equity[j + 1].t <= t) j++;
    out.push(equity[j].liq + equity[j].pos);
  }
  return out;
}

/** R² of a least-squares line through `ys`, signed by slope: rising → R²,
    falling or flat → 0. */
export function smoothness(ys: number[]): number {
  const n = ys.length;
  if (n < 3) return 0;
  const mx = (n - 1) / 2;
  const my = ys.reduce((a, b) => a + b, 0) / n;
  let sxy = 0, sxx = 0, syy = 0;
  ys.forEach((y, x) => { sxy += (x - mx) * (y - my); sxx += (x - mx) ** 2; syy += (y - my) ** 2; });
  if (syy <= 1e-12 || sxy <= 0) return 0;
  return clamp01((sxy * sxy) / (sxx * syy));
}

export function scoreParts(s: Sleeve): Record<ScorePart, number> {
  return {
    smooth: smoothness(resampleEquity(s.equity ?? [])),
    shallow: clamp01(1 - Math.abs(s.drawdown) / DRAWDOWN_ZERO),
    ret: clamp01(0.5 + 0.5 * Math.tanh(s.pct / RETURN_SCALE)),
    sample: clamp01(s.trades / SAMPLE_FULL),
    settled: clamp01(s.confidence),
  };
}

/** Weighted blend of the parts, 0–100. All-zero weights fall back to the
    defaults (a board ranked by nothing is a bug, not a choice). */
export function steadyScore(s: Sleeve | null | undefined, w: ScoreWeights = DEFAULT_SCORE_WEIGHTS): ScoreBreakdown {
  const zero = { smooth: 0, shallow: 0, ret: 0, sample: 0, settled: 0 };
  if (!s || s.trades <= 0) return { score: null, parts: zero };
  const parts = scoreParts(s);
  let ww = cleanWeights(w);
  if (SCORE_PARTS.every((k) => ww[k] === 0)) ww = DEFAULT_SCORE_WEIGHTS;
  const sum = SCORE_PARTS.reduce((a, k) => a + ww[k], 0);
  const score = SCORE_PARTS.reduce((a, k) => a + ww[k] * parts[k], 0) / sum;
  return { score: Math.round(score * 1000) / 10, parts };
}

export function cleanWeights(w: unknown): ScoreWeights {
  const src = (w && typeof w === "object" ? w : {}) as Record<string, unknown>;
  const out = { ...DEFAULT_SCORE_WEIGHTS };
  for (const k of SCORE_PARTS) {
    if (k in src) {
      const n = Number(src[k]);
      out[k] = Number.isFinite(n) ? Math.min(100, Math.max(0, Math.round(n))) : DEFAULT_SCORE_WEIGHTS[k];
    }
  }
  return out;
}

export function parseWeights(raw: string | null | undefined): ScoreWeights {
  if (!raw) return { ...DEFAULT_SCORE_WEIGHTS };
  try { return cleanWeights(JSON.parse(raw)); } catch { return { ...DEFAULT_SCORE_WEIGHTS }; }
}

/** Order rows by score, best first. Unscored rows (still loading, idle) keep
    their incoming order at the bottom — a stable sort, so the board doesn't
    reshuffle among the rows it can't judge yet. */
export function rankByScore<T>(rows: T[], scoreOf: (r: T) => number | null): T[] {
  return rows
    .map((r, i) => ({ r, i, s: scoreOf(r) }))
    .sort((a, b) => {
      if (a.s == null && b.s == null) return a.i - b.i;
      if (a.s == null) return 1;
      if (b.s == null) return -1;
      return b.s - a.s || a.i - b.i;
    })
    .map((x) => x.r);
}

/** One line for a row's tooltip: the score and what it's made of. */
export function explainScore(b: ScoreBreakdown, w: ScoreWeights): string {
  if (b.score == null) return "no copied trades to judge";
  return `${b.score.toFixed(0)}/100 = ` + SCORE_PARTS
    .filter((k) => w[k] > 0)
    .map((k) => `${SCORE_PART_LABEL[k]} ${Math.round(b.parts[k] * 100)}×${w[k]}`)
    .join(" · ");
}
