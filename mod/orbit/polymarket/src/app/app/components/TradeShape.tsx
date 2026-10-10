"use client";

import { useMemo, useState } from "react";
import type { ClosedPosition } from "../lib/polymarket";

/** One fill, as the profile already computes it (trades + FIFO realized). */
export type ShapeTrade = {
  timestamp: number;
  side: "BUY" | "SELL";
  price: number;
  size: number;
  usdcSize?: number;
  realized: number;
  hasBasis: boolean;
  market: string;
};

/** Money decides a settled position, not the outcome: bought at 97¢, resolved
 *  YES, exited at 96¢ = resolved your way and still lost. Exported so the
 *  WIN RATE tile and the win-rate curve can never disagree about a position. */
export function isSettledWin(p: ClosedPosition): boolean {
  return p.realizedPnl !== 0 ? p.realizedPnl > 0 : p.curPrice >= 0.99;
}

/** USDC that actually moved on a fill. `usdcSize` is what the data-api
 *  reported (net of the sell-side fee); price × size is the fallback for
 *  rows stored before that field was carried. */
function notionalOf(t: ShapeTrade): number {
  return t.usdcSize && t.usdcSize > 0 ? t.usdcSize : t.price * t.size;
}

const MONO = "'IBM Plex Mono', monospace";

/** Compact dollars that survive a sub-$1 tape. `formatVolume` rounds to whole
 *  dollars, which renders a roster of 74¢ fills as a column of "$1". */
function money(v: number): string {
  const a = Math.abs(v);
  const sign = v < 0 ? "-" : "";
  if (a >= 999.5) return `${sign}$${(a / 1000).toFixed(1)}K`;
  if (a >= 10) return `${sign}$${a.toFixed(0)}`;
  if (a >= 1) return `${sign}$${a.toFixed(1)}`;
  return `${sign}$${a.toFixed(2)}`;
}

function quantile(sorted: number[], q: number): number {
  if (sorted.length === 0) return 0;
  const pos = (sorted.length - 1) * q;
  const lo = Math.floor(pos);
  const hi = Math.ceil(pos);
  if (lo === hi) return sorted[lo];
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo);
}

function niceStep(raw: number): number {
  if (!(raw > 0)) return 1;
  const exp = Math.floor(Math.log10(raw));
  const base = Math.pow(10, exp);
  const f = raw / base;
  const mult = f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10;
  return mult * base;
}

/* ────────────────────────────────────────────────────────────────────────
   DISTRIBUTION — how this trader's fills are spread across size, entry
   price, or realized P&L. The stat tiles give one average each; an average
   is exactly the statistic that hides a tape made of 140 dust fills and one
   whale, which is the shape that decides whether copying them is possible.
   ──────────────────────────────────────────────────────────────────────── */

type DistMode = "size" | "price" | "pnl";

type Bucket = {
  lo: number;
  hi: number;
  label: string;
  buys: number;
  sells: number;
  notional: number;
};

/** Bucket edges for the SIZE mode. Linear by default; log-spaced once the
 *  tape spans two orders of magnitude, because a single $900 fill next to a
 *  hundred 70¢ fills otherwise renders as one bar and eleven empty ones. */
function sizeEdges(values: number[], n: number): { edges: number[]; log: boolean } {
  let min = Infinity;
  let max = -Infinity;
  // Loop, not Math.min(...values) — a depth-capped wallet carries thousands
  // of fills and spreading those as arguments blows the stack.
  for (const v of values) {
    if (v < min) min = v;
    if (v > max) max = v;
  }
  if (!isFinite(min) || !isFinite(max)) return { edges: [0, 1], log: false };
  if (max <= min) return { edges: [min, min === 0 ? 1 : min * 1.1], log: false };

  const lo = Math.max(min, max / 1e4);
  if (lo > 0 && max / lo >= 100) {
    const edges: number[] = [];
    const ratio = Math.pow(max / lo, 1 / n);
    for (let i = 0; i <= n; i++) edges.push(lo * Math.pow(ratio, i));
    edges[edges.length - 1] = max * 1.000001;
    return { edges, log: true };
  }

  const step = niceStep((max - min) / n);
  const start = Math.floor(min / step) * step;
  const edges: number[] = [];
  for (let v = start; v < max + step; v += step) edges.push(v);
  if (edges.length < 2) edges.push(start + step);
  return { edges, log: false };
}

function buildBuckets(trades: ShapeTrade[], mode: DistMode): { buckets: Bucket[]; total: number } {
  if (mode === "price") {
    // Fixed 5¢ buckets over the whole 0–1 price domain: entry price is the
    // one axis that means the same thing for every trader, so it stays
    // comparable across profiles instead of rescaling per tape.
    const buckets: Bucket[] = [];
    for (let i = 0; i < 20; i++) {
      const lo = i / 20;
      buckets.push({ lo, hi: lo + 0.05, label: `${Math.round(lo * 100)}¢`, buys: 0, sells: 0, notional: 0 });
    }
    for (const t of trades) {
      const idx = Math.min(19, Math.max(0, Math.floor(t.price * 20)));
      const b = buckets[idx];
      if (t.side === "BUY") b.buys += 1; else b.sells += 1;
      b.notional += notionalOf(t);
    }
    return { buckets, total: trades.length };
  }

  if (mode === "pnl") {
    // Only SELLs with a cost basis are scoreable — a SELL whose BUY predates
    // the synced tape would otherwise book an invented $0.
    const scored = trades.filter((t) => t.side === "SELL" && t.hasBasis);
    let maxAbs = 0;
    for (const t of scored) maxAbs = Math.max(maxAbs, Math.abs(t.realized));
    const step = niceStep((maxAbs || 1) / 5);
    const buckets: Bucket[] = [];
    for (let i = -5; i < 5; i++) {
      const lo = i * step;
      buckets.push({ lo, hi: lo + step, label: money(lo), buys: 0, sells: 0, notional: 0 });
    }
    for (const t of scored) {
      // Outliers clamp into the end bars rather than stretching the axis flat.
      const idx = Math.min(9, Math.max(0, Math.floor(t.realized / step) + 5));
      buckets[idx].sells += 1;
      buckets[idx].notional += t.realized;
    }
    return { buckets, total: scored.length };
  }

  const values = trades.map(notionalOf);
  const { edges } = sizeEdges(values, 12);
  const buckets: Bucket[] = [];
  for (let i = 0; i < edges.length - 1; i++) {
    buckets.push({ lo: edges[i], hi: edges[i + 1], label: money(edges[i]), buys: 0, sells: 0, notional: 0 });
  }
  for (const t of trades) {
    const v = notionalOf(t);
    let idx = buckets.findIndex((b) => v >= b.lo && v < b.hi);
    if (idx < 0) idx = v < buckets[0].lo ? 0 : buckets.length - 1;
    const b = buckets[idx];
    if (t.side === "BUY") b.buys += 1; else b.sells += 1;
    b.notional += v;
  }
  return { buckets, total: trades.length };
}

function TradeDistribution({ trades, dayLabel, filtered }: { trades: ShapeTrade[]; dayLabel: string; filtered: boolean }) {
  const [mode, setMode] = useState<DistMode>("size");
  const [hovered, setHovered] = useState<number | null>(null);

  const { buckets, total } = useMemo(() => buildBuckets(trades, mode), [trades, mode]);

  const summary = useMemo(() => {
    const vals = (mode === "pnl"
      ? trades.filter((t) => t.side === "SELL" && t.hasBasis).map((t) => t.realized)
      : mode === "price"
        ? trades.map((t) => t.price)
        : trades.map(notionalOf)
    ).sort((a, b) => a - b);
    if (vals.length === 0) return null;
    const avg = vals.reduce((s, v) => s + v, 0) / vals.length;
    const fmt = mode === "price" ? (v: number) => `${Math.round(v * 100)}¢` : money;
    return { median: fmt(quantile(vals, 0.5)), avg: fmt(avg), p90: fmt(quantile(vals, 0.9)), max: fmt(vals[vals.length - 1]), n: vals.length };
  }, [trades, mode]);

  const W = 800, H = 190;
  const pad = { top: 14, right: 14, bottom: 34, left: 44 };
  const cw = W - pad.left - pad.right;
  const ch = H - pad.top - pad.bottom;

  let maxCount = 1;
  for (const b of buckets) maxCount = Math.max(maxCount, b.buys + b.sells);
  const yTicks = [0, Math.round(maxCount / 2), maxCount].filter((v, i, a) => a.indexOf(v) === i);
  const yMax = maxCount * 1.1;
  const toY = (v: number) => pad.top + ch - (v / yMax) * ch;
  const gap = cw / buckets.length;
  const barW = Math.max(2, gap * 0.74);

  const hb = hovered !== null ? buckets[hovered] : null;
  const rangeLabel = (b: Bucket) =>
    mode === "price"
      ? `${Math.round(b.lo * 100)}–${Math.round(b.hi * 100)}¢`
      : `${money(b.lo)} → ${money(b.hi)}`;

  return (
    <div className="pixel-panel p-5">
      <div className="flex items-center justify-between mb-2 flex-wrap gap-2">
        <div className="text-[16px] text-pixel-gray-light tracking-wider">
          {dayLabel} TRADE DISTRIBUTION{filtered && <span className="text-yellow-400 ml-2 text-[13px]">FILTERED</span>}
        </div>
        <div className="flex items-center gap-1">
          {([
            ["size", "SIZE"],
            ["price", "ENTRY PRICE"],
            ["pnl", "REALIZED"],
          ] as const).map(([id, label]) => (
            <button
              key={id}
              onClick={() => { setMode(id); setHovered(null); }}
              className={`pixel-btn text-[12px] px-2 py-0.5 transition-colors ${
                mode === id
                  ? "border-green-400 text-green-400 bg-green-400/10"
                  : "border-pixel-border text-pixel-gray hover:text-pixel-white hover:border-pixel-white"
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      </div>
      {summary && (
        <div className="text-[14px] text-pixel-gray mb-3 font-mono">
          {summary.n} {mode === "pnl" ? "SCORED SELLS" : "FILLS"} · MEDIAN {summary.median} · AVG {summary.avg} · P90 {summary.p90} · MAX {summary.max}
        </div>
      )}
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full text-pixel-white" style={{ height: "auto", maxHeight: 200 }}>
        {yTicks.map((v, i) => (
          <g key={i}>
            <line x1={pad.left} y1={toY(v)} x2={W - pad.right} y2={toY(v)} stroke="currentColor" strokeOpacity={0.12} strokeWidth={1} />
            <text x={pad.left - 6} y={toY(v) + 3} textAnchor="end" fill="currentColor" fillOpacity={0.45} fontSize={9} fontFamily={MONO}>{v}</text>
          </g>
        ))}
        {buckets.map((b, i) => {
          const x = pad.left + gap * i + (gap - barW) / 2;
          const n = b.buys + b.sells;
          const top = toY(n);
          const buyH = (b.buys / yMax) * ch;
          const sellH = (b.sells / yMax) * ch;
          const loserBar = mode === "pnl" && b.hi <= 0;
          return (
            <g key={i} onMouseEnter={() => setHovered(i)} onMouseLeave={() => setHovered(null)}>
              {/* Full-height hit area so thin bars are still hoverable */}
              <rect x={pad.left + gap * i} y={pad.top} width={gap} height={ch} fill="transparent" />
              {mode === "pnl" ? (
                <rect x={x} y={top} width={barW} height={(n / yMax) * ch}
                  style={{ fill: loserBar ? "var(--down)" : "var(--up)", opacity: hovered === i ? 1 : 0.85 }} />
              ) : (
                <>
                  <rect x={x} y={top} width={barW} height={buyH} fill="currentColor" opacity={hovered === i ? 1 : 0.9} />
                  <rect x={x} y={top + buyH} width={barW} height={sellH} style={{ fill: "var(--pixel-gray)" }} />
                </>
              )}
              {/* Labels sit on the bucket's LEFT EDGE, not under its middle —
                  a histogram tick names a boundary, and a centered one reads
                  as "this bar is $500" when the bar is $500→$1K. Every other
                  edge only: 12–20 of them will not fit. */}
              {i % 2 === 0 && (
                <text x={pad.left + gap * i} y={H - 12} textAnchor="middle" fill="currentColor" fillOpacity={0.45} fontSize={8} fontFamily={MONO}>
                  {b.label}
                </text>
              )}
            </g>
          );
        })}
        {/* The last bucket's closing edge, so the axis names both ends */}
        {buckets.length > 0 && (
          <text x={pad.left + cw} y={H - 12} textAnchor="middle" fill="currentColor" fillOpacity={0.45} fontSize={8} fontFamily={MONO}>
            {mode === "price" ? "100¢" : money(buckets[buckets.length - 1].hi)}
          </text>
        )}
        <line x1={pad.left} y1={pad.top} x2={pad.left} y2={H - pad.bottom} stroke="currentColor" strokeOpacity={0.28} strokeWidth={1} />
        <line x1={pad.left} y1={H - pad.bottom} x2={W - pad.right} y2={H - pad.bottom} stroke="currentColor" strokeOpacity={0.28} strokeWidth={1} />
      </svg>
      <div className="flex items-center gap-4 mt-1 text-[13px] text-pixel-gray font-mono min-h-[18px]">
        {hb ? (
          <>
            <span className="text-pixel-white">{rangeLabel(hb)}</span>
            <span>{hb.buys + hb.sells} {mode === "pnl" ? "SELLS" : "FILLS"}</span>
            <span>{total > 0 ? Math.round(((hb.buys + hb.sells) / total) * 100) : 0}% OF TAPE</span>
            <span className={mode === "pnl" ? (hb.notional > 0 ? "text-green-400" : hb.notional < 0 ? "text-red-400" : "") : ""}>
              {hb.buys + hb.sells === 0
                ? "EMPTY"
                : mode === "pnl"
                  ? `${hb.notional >= 0 ? "+" : ""}${money(hb.notional)} REALIZED`
                  : `${money(hb.notional)} NOTIONAL`}
            </span>
          </>
        ) : mode === "pnl" ? (
          <>
            <div className="flex items-center gap-1"><div className="w-2 h-2" style={{ background: "var(--up)" }} /> WINNING EXITS</div>
            <div className="flex items-center gap-1"><div className="w-2 h-2" style={{ background: "var(--down)" }} /> LOSING EXITS</div>
            <span className="text-pixel-gray/70">EXITS ONLY — POSITIONS THAT EXPIRED WORTHLESS LEAVE NO SELL</span>
          </>
        ) : (
          <>
            <div className="flex items-center gap-1"><div className="w-2 h-2 bg-pixel-white" /> BUYS</div>
            <div className="flex items-center gap-1"><div className="w-2 h-2 bg-pixel-gray" /> SELLS</div>
            <span className="text-pixel-gray/70">HOVER A BAR FOR ITS SHARE OF THE TAPE</span>
          </>
        )}
      </div>
    </div>
  );
}

/* ────────────────────────────────────────────────────────────────────────
   SIZE & WIN RATE — one time axis, two questions: how big were the fills,
   and what share of what settled came back a winner. The win-rate line is
   built from the SETTLED book (the same source as the WIN RATE tile), not
   from exits: counting exits drops every position that expired worthless
   and can only read high.
   ──────────────────────────────────────────────────────────────────────── */

function SizeWinRateChart({
  trades,
  settled,
  dayLabel,
  filtered,
}: {
  trades: ShapeTrade[];
  /** Window- and filter-scoped settled book, oldest first. `null` = not
      loaded / fetch failed, which renders as unknown, never as 100%. */
  settled: ClosedPosition[] | null;
  dayLabel: string;
  filtered: boolean;
}) {
  const [hovered, setHovered] = useState<number | null>(null);

  const fills = useMemo(
    () => trades.map((t) => ({ ...t, notional: notionalOf(t) })).sort((a, b) => a.timestamp - b.timestamp),
    [trades],
  );

  /** Cumulative win rate: after each settled position, wins / decided so far.
      This is the line that walks to the tile's number — its last point IS
      the WIN RATE tile. */
  const rateCurve = useMemo(() => {
    if (!settled || settled.length === 0) return [];
    let wins = 0;
    return settled.map((p, i) => {
      const win = isSettledWin(p);
      if (win) wins += 1;
      return { ts: p.timestamp, rate: (wins / (i + 1)) * 100, decided: i + 1, wins, win, market: p.market, pnl: p.realizedPnl };
    });
  }, [settled]);

  const W = 800, H = 230;
  const pad = { top: 18, right: 46, bottom: 34, left: 52 };
  const cw = W - pad.left - pad.right;
  const ch = H - pad.top - pad.bottom;

  if (fills.length === 0) {
    return (
      <div className="pixel-panel p-5">
        <div className="text-[16px] text-pixel-gray-light tracking-wider">{dayLabel} SIZE & WIN RATE</div>
        <div className="text-[15px] text-pixel-gray mt-2">NO FILLS IN WINDOW</div>
      </div>
    );
  }

  let tsMin = fills[0].timestamp;
  let tsMax = fills[fills.length - 1].timestamp;
  for (const r of rateCurve) {
    if (r.ts < tsMin) tsMin = r.ts;
    if (r.ts > tsMax) tsMax = r.ts;
  }
  const tsRange = tsMax - tsMin || 1;
  const toX = (ts: number) => pad.left + ((ts - tsMin) / tsRange) * cw;

  let maxNotional = 0;
  for (const f of fills) maxNotional = Math.max(maxNotional, f.notional);
  // niceStep always rounds UP to 1/2/5×10ⁿ, so this is a headroomed axis top.
  const sizeMax = niceStep(maxNotional || 1);
  const toYSize = (v: number) => pad.top + ch - (v / (sizeMax || 1)) * ch;
  const toYRate = (v: number) => pad.top + ch - (v / 100) * ch;

  const barW = Math.max(1, Math.min(10, (cw / fills.length) * 0.7));

  // 10-fill moving average of size — the trend under the noise of a tape
  // where every other fill is a different market.
  const maPath = (() => {
    if (fills.length < 5) return "";
    const win = Math.max(3, Math.round(fills.length / 20));
    let sum = 0;
    const pts: string[] = [];
    for (let i = 0; i < fills.length; i++) {
      sum += fills[i].notional;
      if (i >= win) sum -= fills[i - win].notional;
      if (i >= win - 1) {
        const avg = sum / win;
        pts.push(`${pts.length === 0 ? "M" : "L"}${toX(fills[i].timestamp).toFixed(1)},${toYSize(avg).toFixed(1)}`);
      }
    }
    return pts.join(" ");
  })();

  // A rate built on 1–4 decided positions is noise wearing a percentage;
  // it draws dashed until the sample is worth reading.
  const THIN = 5;
  const ratePath = (seg: typeof rateCurve) =>
    seg.map((r, i) => `${i === 0 ? "M" : "L"}${toX(r.ts).toFixed(1)},${toYRate(r.rate).toFixed(1)}`).join(" ");
  const thinSeg = rateCurve.slice(0, Math.min(THIN, rateCurve.length));
  const solidSeg = rateCurve.slice(Math.max(0, Math.min(THIN, rateCurve.length) - 1));

  const finalRate = rateCurve.length ? rateCurve[rateCurve.length - 1] : null;
  const hf = hovered !== null && hovered >= 0 && hovered < fills.length ? fills[hovered] : null;
  // The rate as it stood when that fill printed.
  const hr = hf ? [...rateCurve].reverse().find((r) => r.ts <= hf.timestamp) ?? null : null;

  const svgRef = (el: SVGSVGElement | null) => {
    if (!el) return;
    el.onmousemove = (e: MouseEvent) => {
      const rect = el.getBoundingClientRect();
      const mx = ((e.clientX - rect.left) / rect.width) * W;
      if (mx < pad.left || mx > W - pad.right) { setHovered(null); return; }
      let best = 0, bestDist = Infinity;
      for (let i = 0; i < fills.length; i++) {
        const d = Math.abs(toX(fills[i].timestamp) - mx);
        if (d < bestDist) { bestDist = d; best = i; }
      }
      setHovered(best);
    };
    el.onmouseleave = () => setHovered(null);
  };

  const fmtTime = (ts: number) => new Date(ts).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  const seenDates = new Set<string>();
  const xTicks: { ts: number; label: string }[] = [];
  for (const f of fills) {
    const label = new Date(f.timestamp).toLocaleDateString([], { month: "short", day: "numeric" });
    if (!seenDates.has(label)) { seenDates.add(label); xTicks.push({ ts: f.timestamp, label }); }
  }

  return (
    <div className="pixel-panel p-5">
      <div className="flex items-center justify-between mb-2 flex-wrap gap-2">
        <div className="text-[16px] text-pixel-gray-light tracking-wider">
          {dayLabel} SIZE & WIN RATE{filtered && <span className="text-yellow-400 ml-2 text-[13px]">FILTERED</span>}
        </div>
        <div className="text-[15px] font-mono" style={{ color: finalRate ? (finalRate.rate >= 50 ? "var(--up)" : "var(--down)") : "var(--pixel-gray)" }}>
          {finalRate ? `${Math.round(finalRate.rate)}% · ${finalRate.wins}/${finalRate.decided}` : "WIN RATE —"}
        </div>
      </div>
      <div className="text-[14px] text-pixel-gray mb-3 font-mono">
        {fills.length} FILLS · AVG {money(fills.reduce((s, f) => s + f.notional, 0) / fills.length)} · MAX {money(maxNotional)}
        {settled === null
          ? " · SETTLED BOOK NOT LOADED — WIN RATE UNKNOWN"
          : rateCurve.length === 0
            ? " · NOTHING SETTLED IN WINDOW YET"
            : rateCurve.length < THIN
              ? ` · ${rateCurve.length} DECIDED — THIN SAMPLE`
              : ""}
      </div>
      <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} className="w-full text-pixel-white" style={{ height: "auto", maxHeight: 250 }}>
        {/* Right axis: win rate, pinned 0–100 so the line is comparable
            across traders and windows instead of auto-scaling flat. */}
        {[0, 25, 50, 75, 100].map((v) => (
          <g key={v}>
            <line x1={pad.left} y1={toYRate(v)} x2={W - pad.right} y2={toYRate(v)} stroke="currentColor" strokeOpacity={v === 50 ? 0.22 : 0.1} strokeWidth={1} strokeDasharray={v === 50 ? "4,4" : undefined} />
            <text x={W - pad.right + 6} y={toYRate(v) + 3} textAnchor="start" fill="currentColor" fillOpacity={0.45} fontSize={9} fontFamily={MONO}>{v}%</text>
          </g>
        ))}
        {/* Left axis: fill size */}
        {[0, sizeMax / 2, sizeMax].map((v, i) => (
          <text key={i} x={pad.left - 6} y={toYSize(v) + 3} textAnchor="end" fill="currentColor" fillOpacity={0.45} fontSize={9} fontFamily={MONO}>{money(v)}</text>
        ))}
        {xTicks.map((t, i) => (
          <text key={i} x={toX(t.ts)} y={H - 10} textAnchor="middle" fill="currentColor" fillOpacity={0.45} fontSize={9} fontFamily={MONO}>{t.label}</text>
        ))}
        {/* Size bars — one per fill, BUY white / SELL gray */}
        {fills.map((f, i) => {
          const x = toX(f.timestamp) - barW / 2;
          const y = toYSize(f.notional);
          return (
            <rect
              key={i}
              x={x}
              y={y}
              width={barW}
              height={Math.max(1, pad.top + ch - y)}
              fill={f.side === "BUY" ? "currentColor" : undefined}
              style={f.side === "BUY" ? undefined : { fill: "var(--pixel-gray)" }}
              opacity={hovered === i ? 1 : 0.7}
            />
          );
        })}
        {maPath && <path d={maPath} fill="none" stroke="currentColor" strokeOpacity={0.5} strokeWidth={1.5} strokeDasharray="2,3" />}
        {/* Win-rate line */}
        {thinSeg.length > 1 && (
          <path d={ratePath(thinSeg)} fill="none" style={{ stroke: "var(--up)" }} strokeWidth={1.5} strokeOpacity={0.5} strokeDasharray="4,4" />
        )}
        {solidSeg.length > 1 && (
          <path d={ratePath(solidSeg)} fill="none" style={{ stroke: finalRate && finalRate.rate >= 50 ? "var(--up)" : "var(--down)" }} strokeWidth={2.5} strokeLinejoin="round" strokeLinecap="round" />
        )}
        {/* One dot per decided position, colored by how THAT one landed —
            the line is the running rate, the dots are what moved it. */}
        {rateCurve.map((r, i) => (
          <circle key={i} cx={toX(r.ts)} cy={toYRate(r.rate)} r={2} style={{ fill: r.win ? "var(--up)" : "var(--down)" }} />
        ))}
        {finalRate && (
          <circle cx={toX(finalRate.ts)} cy={toYRate(finalRate.rate)} r={4}
            style={{ fill: "var(--pixel-panel)", stroke: finalRate.rate >= 50 ? "var(--up)" : "var(--down)" }} strokeWidth={2} />
        )}
        <line x1={pad.left} y1={pad.top} x2={pad.left} y2={H - pad.bottom} stroke="currentColor" strokeOpacity={0.28} strokeWidth={1} />
        <line x1={pad.left} y1={H - pad.bottom} x2={W - pad.right} y2={H - pad.bottom} stroke="currentColor" strokeOpacity={0.28} strokeWidth={1} />
        {hf && (
          <line x1={toX(hf.timestamp)} y1={pad.top} x2={toX(hf.timestamp)} y2={H - pad.bottom} stroke="currentColor" strokeOpacity={0.4} strokeWidth={1} strokeDasharray="3,3" />
        )}
      </svg>
      <div className="flex items-center gap-4 mt-1 text-[13px] text-pixel-gray font-mono min-h-[18px] flex-wrap">
        {hf ? (
          <>
            <span className="text-pixel-white">{fmtTime(hf.timestamp)}</span>
            <span className={hf.side === "BUY" ? "text-pixel-white" : "text-pixel-gray-light"}>{hf.side}</span>
            <span>{money(hf.notional)} @ {Math.round(hf.price * 100)}¢</span>
            {hr && <span style={{ color: hr.rate >= 50 ? "var(--up)" : "var(--down)" }}>{Math.round(hr.rate)}% · {hr.wins}/{hr.decided} DECIDED</span>}
            <span className="text-pixel-gray/70 truncate max-w-[280px]">{hf.market}</span>
          </>
        ) : (
          <>
            <div className="flex items-center gap-1"><div className="w-2 h-2 bg-pixel-white" /> BUY SIZE</div>
            <div className="flex items-center gap-1"><div className="w-2 h-2 bg-pixel-gray" /> SELL SIZE</div>
            <div className="flex items-center gap-1"><div className="w-4 h-[2px]" style={{ background: "var(--up)" }} /> WIN RATE (RIGHT, 0–100)</div>
            <span className="text-pixel-gray/70">SETTLED POSITIONS ONLY — DASHED WHILE UNDER {THIN} DECIDED</span>
          </>
        )}
      </div>
    </div>
  );
}

/** Both shape charts, in the order the questions get asked: how big are the
 *  fills and is the record holding up (time), then what the tape is made of
 *  (distribution). */
export default function TradeShape(props: {
  trades: ShapeTrade[];
  settled: ClosedPosition[] | null;
  dayLabel: string;
  filtered: boolean;
}) {
  return (
    <div className="space-y-3">
      <SizeWinRateChart {...props} />
      <TradeDistribution trades={props.trades} dayLabel={props.dayLabel} filtered={props.filtered} />
    </div>
  );
}
