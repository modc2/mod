"use client";

// The strat's own PnL line: every leg's window curve, weighted the way the
// basket weights them, summed on one time grid. Polymarket's rule — a strat
// page leads with its curve, not a wall of tiles — applies here unchanged.
//
// Curves come from the same batched `/traders/curves` endpoint the board
// cards use (shared server cache, 30-a-request), and each leg's curve is
// already rebased to zero at the window start, so the weighted sum reads as
// "what this basket made this window". A leg with no curve (fresh wallet,
// upstream 429) contributes nothing and is named in the footer instead of
// silently flattening the line.

import { useEffect, useMemo, useRef, useState } from "react";
import { fetchTraderCurves, fmtPnl, shortAddr, type IndexLeg, type TraderCurve } from "../lib/api";

const H = 200;
const PAD = { t: 12, r: 12, b: 20, l: 52 };
const BATCH = 30;

type Pt = { t: number; v: number };

/** Step-interpolate a cumulative curve: value at `t` is the last sample ≤ t,
 *  0 before the first (curves are window-rebased). */
function stepAt(points: [number, number][], t: number, cursor: { i: number }): number {
  while (cursor.i < points.length && points[cursor.i][0] <= t) cursor.i++;
  return cursor.i === 0 ? 0 : points[cursor.i - 1][1];
}

function combine(curves: TraderCurve[], weights: Map<string, number>): Pt[] {
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

export default function StratCurve({ legs, days }: { legs: IndexLeg[]; days: number }) {
  const [curves, setCurves] = useState<TraderCurve[] | null>(null);
  const [hover, setHover] = useState<Pt | null>(null);
  const svgRef = useRef<SVGSVGElement | null>(null);
  // Width ref lives on the always-mounted wrapper, not the svg — the svg
  // only exists after the fetch, so measuring it at mount reads nothing and
  // the viewBox stays 640 wide, centered in the panel by preserveAspectRatio.
  const boxRef = useRef<HTMLDivElement | null>(null);
  const [w, setW] = useState(640);

  useEffect(() => {
    const el = boxRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setW(Math.max(320, el.clientWidth - 16)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    let dead = false;
    setCurves(null);
    const addrs = legs.map((l) => l.address);
    const chunks: string[][] = [];
    for (let i = 0; i < addrs.length; i += BATCH) chunks.push(addrs.slice(i, i + BATCH));
    Promise.all(chunks.map((c) => fetchTraderCurves(c, days)))
      .then((rs) => { if (!dead) setCurves(rs.flatMap((r) => r.curves)); })
      .catch(() => { if (!dead) setCurves([]); });
    return () => { dead = true; };
  }, [legs, days]);

  const weights = useMemo(() => {
    const total = legs.reduce((s, l) => s + l.weight, 0) || 1;
    return new Map(legs.map((l) => [l.address.toLowerCase(), l.weight / total]));
  }, [legs]);

  const pts = useMemo(() => (curves ? combine(curves, weights) : []), [curves, weights]);
  const missing = useMemo(
    () => (curves ?? []).filter((c) => !c.available || c.points.length === 0).map((c) => c.address),
    [curves]);

  const { path, area, zeroY, x, y, lo, hi, drawdown } = useMemo(() => {
    if (pts.length < 2) return { path: "", area: "", zeroY: 0, x: (_: number) => 0, y: (_: number) => 0, lo: 0, hi: 0, drawdown: 0 };
    const t0 = pts[0].t, t1 = pts[pts.length - 1].t;
    let lo = Math.min(0, ...pts.map((p) => p.v));
    let hi = Math.max(0, ...pts.map((p) => p.v));
    if (hi - lo < 1e-9) { hi += 1; lo -= 1; }
    const x = (t: number) => PAD.l + ((t - t0) / (t1 - t0 || 1)) * (w - PAD.l - PAD.r);
    const y = (v: number) => PAD.t + ((hi - v) / (hi - lo)) * (H - PAD.t - PAD.b);
    const path = pts.map((p, i) => `${i ? "L" : "M"}${x(p.t).toFixed(1)},${y(p.v).toFixed(1)}`).join("");
    const area = `${path}L${x(t1).toFixed(1)},${y(0).toFixed(1)}L${x(t0).toFixed(1)},${y(0).toFixed(1)}Z`;
    let peak = -Infinity, dd = 0;
    for (const p of pts) { peak = Math.max(peak, p.v); dd = Math.max(dd, peak - p.v); }
    return { path, area, zeroY: y(0), x, y, lo, hi, drawdown: dd };
  }, [pts, w]);

  const last = pts.length ? pts[pts.length - 1].v : 0;

  const onMove = (e: React.MouseEvent<SVGSVGElement>) => {
    if (pts.length < 2) return;
    const rect = svgRef.current!.getBoundingClientRect();
    const px = e.clientX - rect.left;
    let best = pts[0];
    for (const p of pts) if (Math.abs(x(p.t) - px) < Math.abs(x(best.t) - px)) best = p;
    setHover(best);
  };

  return (
    <div className="panel">
      <div className="px-4 py-2 border-b border-border flex items-center justify-between gap-3">
        <span className="text-[11px] uppercase tracking-wider text-muted">
          basket pnl · weighted · {days}d
        </span>
        {pts.length > 1 && (
          <span className="text-[11px] text-muted">
            <span className={`num font-semibold ${last >= 0 ? "text-win" : "text-loss"}`}>{fmtPnl(last)}</span>
            <span className="mx-2 text-dim">·</span>
            max drawdown <span className="num text-ink">{fmtPnl(-drawdown)}</span>
          </span>
        )}
      </div>
      <div ref={boxRef} className="px-2 pt-2 pb-1">
        {curves === null ? (
          <div className="skeleton" style={{ height: H }} />
        ) : pts.length < 2 ? (
          <div className="flex items-center justify-center text-xs text-muted" style={{ height: H }}>
            no curve data for this window yet
          </div>
        ) : (
          <svg ref={svgRef} width="100%" height={H} viewBox={`0 0 ${w} ${H}`}
            onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
            <defs>
              <linearGradient id="strat-curve-fill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="var(--chart-line)" stopOpacity="0.22" />
                <stop offset="100%" stopColor="var(--chart-line)" stopOpacity="0" />
              </linearGradient>
            </defs>
            {[0.25, 0.5, 0.75].map((f) => (
              <line key={f} x1={PAD.l} x2={w - PAD.r}
                y1={PAD.t + f * (H - PAD.t - PAD.b)} y2={PAD.t + f * (H - PAD.t - PAD.b)}
                stroke="var(--chart-grid)" />
            ))}
            <line x1={PAD.l} x2={w - PAD.r} y1={zeroY} y2={zeroY}
              stroke="var(--chart-grid)" strokeDasharray="3 3" />
            <text x={PAD.l - 6} y={PAD.t + 4} textAnchor="end" className="fill-current text-muted" fontSize={9}>
              {fmtPnl(hi)}
            </text>
            <text x={PAD.l - 6} y={H - PAD.b} textAnchor="end" className="fill-current text-muted" fontSize={9}>
              {fmtPnl(lo)}
            </text>
            <path d={area} fill="url(#strat-curve-fill)" />
            <path d={path} fill="none" stroke="var(--chart-line)" strokeWidth={1.6} />
            {hover && (
              <g>
                <line x1={x(hover.t)} x2={x(hover.t)} y1={PAD.t} y2={H - PAD.b}
                  stroke="var(--chart-grid)" />
                <circle cx={x(hover.t)} cy={y(hover.v)} r={3} fill="var(--chart-line)" />
                <text x={x(hover.t) < w / 2 ? x(hover.t) + 8 : x(hover.t) - 8}
                  y={Math.max(PAD.t + 10, y(hover.v) - 8)}
                  textAnchor={x(hover.t) < w / 2 ? "start" : "end"}
                  className="fill-current text-ink" fontSize={10}>
                  {fmtPnl(hover.v)} · {new Date(hover.t).toLocaleDateString(undefined, { month: "short", day: "numeric" })}
                </text>
              </g>
            )}
            <text x={PAD.l} y={H - 6} className="fill-current text-muted" fontSize={9}>
              {new Date(pts[0].t).toLocaleDateString(undefined, { month: "short", day: "numeric" })}
            </text>
            <text x={w - PAD.r} y={H - 6} textAnchor="end" className="fill-current text-muted" fontSize={9}>
              {new Date(pts[pts.length - 1].t).toLocaleDateString(undefined, { month: "short", day: "numeric" })}
            </text>
          </svg>
        )}
      </div>
      {pts.length > 1 && (
        <div className="px-4 pb-2 text-[10px] text-muted">
          source: each leg&apos;s hyperliquid portfolio curve — realised <em>and</em> unrealised —
          so it can honestly differ from the tiles, which score realised fills only.
          {missing.length > 0 && (
            <> no curve for {missing.slice(0, 3).map(shortAddr).join(", ")}
              {missing.length > 3 ? ` +${missing.length - 3} more` : ""} — their weight isn&apos;t drawn.</>
          )}
        </div>
      )}
    </div>
  );
}
