"use client";

// PnL / equity over time for a single trader, fed by the HL "portfolio" info
// payload: [[period, { accountValueHistory, pnlHistory, vlm }], ...].
// Single series ⇒ no legend; identity lives in the panel title + metric pill.
//
// The portfolio payload carries TWO curves per window: `week` is the whole
// account and `perpWeek` is the perps sub-account only. They are not the same
// number and the gap is not small — there are wallets on the board whose
// `week` curve is up half a million dollars on zero perp volume, because the
// entire move is spot bags being repriced. This chart draws the PERP curve,
// because everything else on the trader page (the tiles, the round trips, the
// open positions table) is scored from perp fills. Drawing the combined curve
// next to perp-only tiles is how a page ends up claiming +$666 and -$5.4K at
// the same time under the same "(7d)" heading.
//
// The spot difference is not thrown away — it is `spot` below, and the footer
// names it, because "your bags moved" is a real answer to "why is the chart
// red when every trade won".

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
type Series = {
  pnl: Pt[];
  equity: Pt[];
  /** Combined-minus-perp: what the spot side of the account did, same window. */
  spot: Pt[];
  /** True when the perp sub-account curve was available and is what's drawn. */
  perpOnly: boolean;
};

function parseHistory(v: any): Pt[] {
  if (!Array.isArray(v)) return [];
  return v
    .map((p: any) => ({ t: Number(p?.[0]), v: Number(p?.[1]) }))
    .filter((p) => Number.isFinite(p.t) && Number.isFinite(p.v))
    .sort((a, b) => a.t - b.t);
}

/** Cumulative curve rebased to 0 at the window start, so it reads as "this
 *  window made X" rather than "lifetime stands at X". */
function rebase(pts: Pt[]): Pt[] {
  const base = pts.length ? pts[0].v : 0;
  return pts.map((p) => ({ t: p.t, v: p.v - base }));
}

/** a - b, joined on timestamp. HL emits both slots on the same sample grid,
 *  but a missing sample must drop the point rather than invent a zero. */
function diff(a: Pt[], b: Pt[]): Pt[] {
  const m = new Map(b.map((p) => [p.t, p.v]));
  return a.flatMap((p) => (m.has(p.t) ? [{ t: p.t, v: p.v - m.get(p.t)! }] : []));
}

// Pick the portfolio period that covers the UI window, perp sub-account first.
function extract(portfolio: any, days: number): Series {
  const want = days <= 1 ? "day" : days <= 7 ? "week" : days <= 30 ? "month" : "allTime";
  const perpWant = "perp" + want[0].toUpperCase() + want.slice(1);
  const rows: any[] = Array.isArray(portfolio) ? portfolio : [];
  const byName = (n: string) => rows.find((r) => Array.isArray(r) && r[0] === n)?.[1];

  const combined = byName(want) ?? byName("allTime") ?? rows[0]?.[1];
  const perp = byName(perpWant);
  // Perp is what the rest of the page measures; fall back to combined only
  // when this wallet has no perp slot at all.
  const slot = perp ?? combined;

  const pnl = rebase(parseHistory(slot?.pnlHistory));
  const combinedPnl = rebase(parseHistory(combined?.pnlHistory));

  return {
    pnl,
    equity: parseHistory(slot?.accountValueHistory),
    spot: perp ? diff(combinedPnl, pnl) : [],
    perpOnly: !!perp,
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

export default function PnlChart({ portfolio, days, windowPnl }: {
  portfolio: any;
  days: number;
  /** The realised, fee-net PnL the "pnl (Nd)" tile shows, from perp fills.
   *  Passed in so the chart can reconcile itself against the tile instead of
   *  leaving the reader to assume two different measures are one number. */
  windowPnl?: number;
}) {
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
            {m === "pnl" ? (series.perpOnly ? "perp pnl" : "pnl") : "account value"}
          </button>
        ))}
        <span className="text-[10px] text-muted">
          {series.perpOnly
            ? "perps sub-account — the same book the tiles are scored from"
            : "whole account — no perp-only curve published for this wallet"}
        </span>
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

      {metric === "pnl" && <Reconcile series={series} days={days} windowPnl={windowPnl} />}
    </div>
  );
}

/**
 * Ties the curve's endpoint back to the tile above it.
 *
 * These are two honest numbers that measure different things, and the page
 * used to print both under a "(7d)" heading and let the reader discover the
 * gap on their own. The curve is equity marked to market — it moves on open
 * positions, funding and the spot side. The tile is realised PnL on closed
 * fills. A wallet can close twelve winners for +$666 and still be down $5K on
 * the day, and that is not a bug in either number; it is the sentence this
 * strip exists to say out loud.
 */
function Reconcile({ series, days, windowPnl }: { series: Series; days: number; windowPnl?: number }) {
  const curve = series.pnl.length ? series.pnl[series.pnl.length - 1].v : 0;
  const spot = series.spot.length ? series.spot[series.spot.length - 1].v : null;

  if (windowPnl == null || !Number.isFinite(windowPnl)) return null;

  const gap = curve - windowPnl;
  // Below this the two measures agree for practical purposes and the
  // breakdown is just noise on the page.
  const material = Math.abs(gap) > Math.max(1, Math.abs(windowPnl) * 0.02);
  const spotMoved = spot != null && Math.abs(spot) > Math.max(1, Math.abs(curve) * 0.01);

  // A quiet perp book is not a quiet account. There are wallets on the board
  // that traded no perps at all this window and are still up six figures on
  // spot — every number on this page is legitimately zero for them, and
  // saying only "the curve agrees with the tile" would be true and useless.
  if (!material) {
    return (
      <div className="px-4 py-2 text-[10px] text-muted border-t border-border">
        Curve ends at {fmtPnl(curve)} — the same money the pnl ({days}d) tile counts.
        {spotMoved && (
          <>
            {" "}Separately, the spot side of this account moved{" "}
            <span className={spot! >= 0 ? "text-win" : "text-loss"}>{fmtPnl(spot!)}</span>{" "}
            over the same {days}d — bags repriced, counted in no tile on this page.
          </>
        )}
      </div>
    );
  }

  const rows: { label: string; value: number; note: string; tone?: boolean }[] = [
    {
      label: `${series.perpOnly ? "perp" : "account"} equity, marked to market`,
      value: curve,
      note: "where this curve ends",
    },
    {
      label: "closed fills, net of fees",
      value: windowPnl,
      note: `what the pnl (${days}d) tile counts`,
    },
    {
      label: "difference",
      value: gap,
      note: "funding, positions still open at the marks, and the portfolio window's own edges",
      tone: true,
    },
  ];
  if (spotMoved) {
    rows.push({
      label: `spot side, same ${days}d`,
      value: spot!,
      note: "bags repriced — no fill, no close, counted in no tile on this page",
    });
  }

  return (
    <div className="border-t border-border">
      <div className="px-4 pt-2 pb-1 text-[10px] uppercase tracking-wider text-muted">
        why the curve isn&apos;t the tile
      </div>
      {rows.map((r) => (
        <div key={r.label} className="grid grid-cols-[1.1fr_0.6fr_1.6fr] gap-2 px-4 py-1 items-baseline">
          <div className="text-[11px] text-muted">{r.label}</div>
          <div
            className={`num text-right text-[11px] ${
              r.tone ? "text-warn" : r.value >= 0 ? "text-win" : "text-loss"
            }`}
          >
            {fmtPnl(r.value)}
          </div>
          <div className="text-[10px] text-muted">{r.note}</div>
        </div>
      ))}
      <div className="h-2" />
    </div>
  );
}
