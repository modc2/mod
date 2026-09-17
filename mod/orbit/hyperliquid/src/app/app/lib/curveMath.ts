// Summing window curves — shared by the strat page's big chart and the
// board cards' sparklines, so both draw a basket the same way.
//
// Every curve from `/traders/curves` is already rebased to zero at the window
// start, so a weighted sum reads as "what this basket made this window". Legs
// sample at different instants, so the sum is taken on the union of their time
// grids with each leg step-held at its last known value.

import type { TraderCurve } from "./api";

export type Pt = { t: number; v: number };

/** Value of a cumulative curve at `t`: the last sample at or before `t`, and 0
 *  before the first one (window-rebased curves start at zero). `cursor` walks
 *  forward across calls, so a whole grid costs one pass per curve. */
export function stepAt(points: [number, number][], t: number, cursor: { i: number }): number {
  while (cursor.i < points.length && points[cursor.i][0] <= t) cursor.i++;
  return cursor.i === 0 ? 0 : points[cursor.i - 1][1];
}

/** Weight-sum curves onto one grid. Weights are keyed by lowercase address;
 *  a curve with no weight contributes nothing, and an empty result means
 *  nothing usable came back — draw "no line", not a flat one. */
export function combineCurves(curves: TraderCurve[], weights: Map<string, number>): Pt[] {
  const usable = curves.filter((c) => c.available && c.points.length > 0);
  if (usable.length === 0) return [];
  const grid = Array.from(new Set(usable.flatMap((c) => c.points.map((p) => p[0])))).sort((a, b) => a - b);
  const cursors = usable.map(() => ({ i: 0 }));
  return grid.map((t) => ({
    t,
    v: usable.reduce((s, c, i) =>
      s + (weights.get(c.address.toLowerCase()) ?? 0) * stepAt(c.points, t, cursors[i]), 0),
  }));
}

/** Normalised weights for a set of legs, keyed lowercase. A single wallet is
 *  just a one-leg basket. */
export function legWeights(legs: { address: string; weight: number }[]): Map<string, number> {
  const total = legs.reduce((s, l) => s + l.weight, 0) || 1;
  return new Map(legs.map((l) => [l.address.toLowerCase(), l.weight / total]));
}
