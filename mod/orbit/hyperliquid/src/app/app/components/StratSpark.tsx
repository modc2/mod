"use client";

// The shape behind a card's number: the row's own PnL over the board window,
// drawn small. One wallet for a trader or a vault, weight-summed legs for a
// basket — the same math the strat page's full chart uses, so a card and the
// page it opens can never disagree about the line.
//
// Curves come from `lib/curves.ts` — the same shared, deduped, rate-limited
// cache the trader board draws from, so a wallet on both boards is fetched
// once and a re-sort costs nothing.

import { useMemo } from "react";
import { fmtPnl, type TraderCurve } from "../lib/api";
import { combineCurves, legWeights } from "../lib/curveMath";

const H = 40;
const W = 100;

export type SparkLeg = { address: string; weight: number };

export default function StratSpark({
  legs, days, curves,
}: {
  legs: SparkLeg[];
  days: number;
  /** Wallet (lowercase) → its window curve. A wallet still on its way is
   *  simply absent; one Hyperliquid could not answer for is present with
   *  `available: false`. */
  curves: Record<string, TraderCurve>;
}) {
  const addrs = useMemo(() => legs.map((l) => l.address.toLowerCase()), [legs]);
  const weights = useMemo(() => legWeights(legs), [legs]);
  const got = addrs.map((a) => curves[a]).filter((c): c is TraderCurve => !!c);
  const pending = got.length === 0 && addrs.length > 0;
  // Both of these are a pass over at most a few dozen points per leg, and the
  // inputs are a slice of a map that changes shape as batches land — memoising
  // on them would cost more than it saves (and a dependency list whose LENGTH
  // varies with leg count is a React error waiting to happen).
  const pts = pending ? [] : combineCurves(got, weights);

  const shape = (() => {
    if (pts.length < 2) return null;
    const t0 = pts[0].t, t1 = pts[pts.length - 1].t;
    let lo = Math.min(0, ...pts.map((p) => p.v));
    let hi = Math.max(0, ...pts.map((p) => p.v));
    if (hi - lo < 1e-9) { hi += 1; lo -= 1; }
    const x = (t: number) => ((t - t0) / (t1 - t0 || 1)) * W;
    const y = (v: number) => 2 + ((hi - v) / (hi - lo)) * (H - 4);
    const path = pts.map((p, i) => `${i ? "L" : "M"}${x(p.t).toFixed(2)},${y(p.v).toFixed(2)}`).join("");
    let peak = -Infinity, dd = 0;
    for (const p of pts) { peak = Math.max(peak, p.v); dd = Math.max(dd, peak - p.v); }
    return {
      path,
      area: `${path}L${W},${y(0).toFixed(2)}L0,${y(0).toFixed(2)}Z`,
      zero: y(0),
      last: pts[pts.length - 1].v,
      dd,
    };
  })();

  // A curve that never arrived is said out loud — a card that quietly draws
  // nothing looks like a flat wallet, which is a different claim.
  const missing = !pending && got.length < legs.length;

  return (
    <div className="mt-3">
      <div className="h-[40px]">
        {pending ? (
          <div className="skeleton h-full w-full rounded" />
        ) : shape ? (
          <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className={`h-full w-full ${shape.last >= 0 ? "text-win" : "text-loss"}`}>
            <path d={shape.area} fill="currentColor" opacity={0.12} />
            <line x1={0} x2={W} y1={shape.zero} y2={shape.zero} stroke="currentColor"
              strokeDasharray="2 3" strokeWidth={1} opacity={0.25} vectorEffect="non-scaling-stroke" />
            <path d={shape.path} fill="none" stroke="currentColor" strokeWidth={1.5}
              strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
          </svg>
        ) : (
          <div className="flex h-full items-center text-[10px] uppercase tracking-wider text-dim">
            no line for this window
          </div>
        )}
      </div>
      <div className="mt-1 flex items-baseline justify-between gap-2 text-[10px] uppercase tracking-wider text-muted">
        <span>
          {shape ? (
            <span className={`num font-semibold ${shape.last >= 0 ? "text-win" : "text-loss"}`}>{fmtPnl(shape.last)}</span>
          ) : <span className="text-dim">{pending ? "loading" : "—"}</span>}
          <span className="ml-1.5">past {days}d</span>
        </span>
        {shape && (
          <span className="text-dim">
            {missing ? `${got.length}/${legs.length} legs` : <>dd {fmtPnl(-shape.dd)}</>}
          </span>
        )}
      </div>
    </div>
  );
}
