"use client";

// PnL / equity over time for a single trader, fed by the HL "portfolio" info
// payload: [[period, { accountValueHistory, pnlHistory, vlm }], ...].
// Single series ⇒ no legend; identity lives in the panel title + metric pill.

import { useEffect, useMemo, useRef, useState } from "react";
import { fmtPnl, fmtUsd } from "../lib/api";

// Theme-aware via CSS vars (globals.css) — SVG presentation attributes
// resolve var() like any CSS property, so the chart flips with the theme.
const LINE = "var(--chart-line)";
const SURFACE = "var(--chart-surface)";
const GRID = "var(--chart-grid)";
const H = 220;
const PAD = { t: 14, r: 14, b: 24, l: 52 };

type Pt = { t: number; v: number };
type Metric = "pnl" | "equity";

function parseHistory(v: any): Pt[] {
  if (!Array.isArray(v)) return [];
  return v
    .map((p: any) => ({ t: Number(p?.[0]), v: Number(p?.[1]) }))
    .filter((p) => Number.isFinite(p.t) && Number.isFinite(p.v))
    .sort((a, b) => a.t - b.t);
}

// Pick the portfolio period that covers the UI window.
function extract(portfolio: any, days: number): { pnl: Pt[]; equity: Pt[] } {
  const want = days <= 1 ? "day" : days <= 7 ? "week" : days <= 30 ? "month" : "allTime";
  const rows: any[] = Array.isArray(portfolio) ? portfolio : [];
  const byName = (n: string) => rows.find((r) => Array.isArray(r) && r[0] === n)?.[1];
  const slot = byName(want) ?? byName("allTime") ?? rows[0]?.[1];
  const pnlRaw = parseHistory(slot?.pnlHistory);
  // Rebase cumulative PnL to the window start so the curve matches the
  // "pnl (Nd)" tile: it answers "what did this window make", not lifetime.
  const base = pnlRaw.length ? pnlRaw[0].v : 0;
  return {
    pnl: pnlRaw.map((p) => ({ t: p.t, v: p.v - base })),
    equity: parseHistory(slot?.accountValueHistory),
  };
}

function niceTicks(min: number, max: number, n = 4): number[] {
  if (!(max > min)) return [min];
  const span = max - min;
  const step0 = span / n;
  const mag = Math.pow(10, Math.floor(Math.log10(step0)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => span / s <= n) ?? mag * 10;
  const ticks: number[] = [];
  for (let v = Math.ceil(min / step) * step; v <= max + step * 1e-6; v += step) ticks.push(v);
  return ticks;
}

const fmtTime = (ms: number, spanMs: number) => {
  const d = new Date(ms);
  if (spanMs <= 36 * 3600_000)
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return d.toLocaleDateString([], { month: "short", day: "numeric" });
};

export default function PnlChart({ portfolio, days }: { portfolio: any; days: number }) {
  const [metric, setMetric] = useState<Metric>("pnl");
  const [hover, setHover] = useState<number | null>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(0);

  const series = useMemo(() => extract(portfolio, days), [portfolio, days]);
  const pts = series[metric];

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setW(el.clientWidth));
    ro.observe(el);
    setW(el.clientWidth);
    return () => ro.disconnect();
  }, []);

  if (pts.length < 2) {
    return (
      <div className="px-4 py-6 text-xs text-muted">
        no {metric === "pnl" ? "pnl" : "equity"} history for this window
      </div>
    );
  }

  const t0 = pts[0].t, t1 = pts[pts.length - 1].t;
  let lo = Math.min(...pts.map((p) => p.v));
  let hi = Math.max(...pts.map((p) => p.v));
  if (metric === "pnl") { lo = Math.min(lo, 0); hi = Math.max(hi, 0); } // keep the zero line in frame
  if (hi === lo) { hi += 1; lo -= 1; }
  const padV = (hi - lo) * 0.08;
  lo -= padV; hi += padV;

  const iw = Math.max(0, w - PAD.l - PAD.r);
  const ih = H - PAD.t - PAD.b;
  const X = (t: number) => PAD.l + ((t - t0) / (t1 - t0 || 1)) * iw;
  const Y = (v: number) => PAD.t + (1 - (v - lo) / (hi - lo)) * ih;

  const line = pts.map((p, i) => `${i ? "L" : "M"}${X(p.t).toFixed(1)},${Y(p.v).toFixed(1)}`).join("");
  const floor = Y(metric === "pnl" ? Math.max(lo, Math.min(hi, 0)) : lo);
  const area = `${line}L${X(t1).toFixed(1)},${floor.toFixed(1)}L${X(t0).toFixed(1)},${floor.toFixed(1)}Z`;

  const yTicks = niceTicks(lo, hi);
  const xTickIdx = [0, 0.25, 0.5, 0.75, 1].map((f) => Math.round(f * (pts.length - 1)));
  const fmtV = metric === "pnl" ? fmtPnl : fmtUsd;

  const nearest = (px: number) => {
    const t = t0 + ((px - PAD.l) / (iw || 1)) * (t1 - t0);
    let best = 0, bd = Infinity;
    for (let i = 0; i < pts.length; i++) {
      const d = Math.abs(pts[i].t - t);
      if (d < bd) { bd = d; best = i; }
    }
    return best;
  };

  const hp = hover != null ? pts[hover] : null;
  const tipLeft = hp ? Math.min(Math.max(X(hp.t) + 10, PAD.l), w - 150) : 0;

  return (
    <div>
      <div className="flex items-center gap-2 px-4 pt-3">
        {(["pnl", "equity"] as Metric[]).map((m) => (
          <button
            key={m}
            onClick={() => { setMetric(m); setHover(null); }}
            className={`px-2 py-0.5 rounded-full border text-[10px] uppercase tracking-wider transition-colors ${
              metric === m
                ? "border-accent/50 bg-accent/10 text-ink"
                : "border-white/[0.08] bg-white/[0.03] text-muted hover:text-ink"
            }`}
          >
            {m === "pnl" ? "pnl" : "account value"}
          </button>
        ))}
      </div>

      <div
        ref={wrapRef}
        className="relative outline-none"
        tabIndex={0}
        role="img"
        aria-label={`${metric} over time, ${fmtV(pts[pts.length - 1].v)} at end of window`}
        onKeyDown={(e) => {
          if (e.key === "ArrowRight") setHover((h) => Math.min((h ?? -1) + 1, pts.length - 1));
          else if (e.key === "ArrowLeft") setHover((h) => Math.max((h ?? pts.length) - 1, 0));
          else if (e.key === "Escape") setHover(null);
          else return;
          e.preventDefault();
        }}
        onPointerMove={(e) => {
          const r = wrapRef.current?.getBoundingClientRect();
          if (r) setHover(nearest(e.clientX - r.left));
        }}
        onPointerLeave={() => setHover(null)}
      >
        {w > 0 && (
          <svg width={w} height={H} className="block">
            {/* recessive hairline grid + y ticks */}
            {yTicks.map((v) => (
              <g key={v}>
                <line x1={PAD.l} x2={w - PAD.r} y1={Y(v)} y2={Y(v)} stroke={GRID} strokeWidth={1} />
                <text x={PAD.l - 8} y={Y(v) + 3} textAnchor="end" fontSize={10} className="fill-muted num">
                  {fmtUsd(v)}
                </text>
              </g>
            ))}
            {/* zero baseline sits above the grid when pnl crosses it */}
            {metric === "pnl" && lo < 0 && hi > 0 && (
              <line x1={PAD.l} x2={w - PAD.r} y1={Y(0)} y2={Y(0)} stroke="var(--chart-zero)" strokeWidth={1} />
            )}
            {xTickIdx.map((i) => (
              <text key={i} x={X(pts[i].t)} y={H - 8} textAnchor="middle" fontSize={10} className="fill-muted">
                {fmtTime(pts[i].t, t1 - t0)}
              </text>
            ))}
            {/* area wash + 2px line */}
            <path d={area} fill={LINE} fillOpacity={0.1} />
            <path d={line} fill="none" stroke={LINE} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
            {/* end marker: r4 dot with a 2px surface ring */}
            <circle cx={X(t1)} cy={Y(pts[pts.length - 1].v)} r={4} fill={LINE} stroke={SURFACE} strokeWidth={2} />
            {hp && (
              <g>
                <line x1={X(hp.t)} x2={X(hp.t)} y1={PAD.t} y2={H - PAD.b} stroke="var(--chart-cross)" strokeWidth={1} />
                <circle cx={X(hp.t)} cy={Y(hp.v)} r={4} fill={LINE} stroke={SURFACE} strokeWidth={2} />
              </g>
            )}
          </svg>
        )}
        {hp && (
          <div
            className="absolute pointer-events-none rounded-md border border-white/[0.08] bg-black/80 px-2.5 py-1.5"
            style={{ left: tipLeft, top: Math.max(Y(hp.v) - 48, 4) }}
          >
            <div className={`num text-sm ${metric === "pnl" ? (hp.v >= 0 ? "text-win" : "text-loss") : "text-ink"}`}>
              {fmtV(hp.v)}
            </div>
            <div className="text-[10px] text-muted whitespace-nowrap">
              {new Date(hp.t).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
