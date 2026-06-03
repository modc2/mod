"use client";

import { useState } from "react";
import { formatPnl, timeAgo } from "../lib/polymarket";

export type CurvePoint = {
  i: number;
  ts: number;
  date: string;
  time: string;
  pnl: number;
  side: "BUY" | "SELL" | "MARK";
  realized: number;
  market: string;
  size: number;
  price: number;
  buyPrice?: number;
  buyTimestamp?: number;
};

/* ── Pure SVG P&L Chart ── */
export default function PnlChart({ points, dayLabel, tradesInWindow, filtered = false, highlightIndex, onHoverChange, linkedTrades, shortAddress }: {
  points: CurvePoint[];
  dayLabel: string;
  tradesInWindow: { timestamp: number }[];
  filtered?: boolean;
  highlightIndex?: number | null;
  onHoverChange?: (idx: number | null) => void;
  linkedTrades?: any[];
  shortAddress?: (addr: string) => string;
}) {
  const W = 800, H = 260;
  const pad = { top: 20, right: 16, bottom: 40, left: 60 };
  const cw = W - pad.left - pad.right;
  const ch = H - pad.top - pad.bottom;

  const pnls = points.map((p) => p.pnl);
  const minPnl = Math.min(...pnls);
  const maxPnl = Math.max(...pnls);
  const range = maxPnl - minPnl || 1;
  const yMin = minPnl - range * 0.1;
  const yMax = maxPnl + range * 0.1;
  const yRange = yMax - yMin;

  const tsMin = points[0].ts;
  const tsMax = points[points.length - 1].ts;
  const tsRange = tsMax - tsMin || 1;

  const toX = (ts: number) => pad.left + ((ts - tsMin) / tsRange) * cw;
  const toY = (v: number) => pad.top + ch - ((v - yMin) / yRange) * ch;

  // Build line path
  const linePath = points.map((p, i) => `${i === 0 ? "M" : "L"}${toX(p.ts).toFixed(1)},${toY(p.pnl).toFixed(1)}`).join(" ");
  // Build area path (fill under curve to zero line or bottom)
  const zeroY = toY(Math.max(yMin, Math.min(yMax, 0)));
  const areaPath = linePath + ` L${toX(points[points.length - 1].ts).toFixed(1)},${zeroY.toFixed(1)} L${toX(points[0].ts).toFixed(1)},${zeroY.toFixed(1)} Z`;

  // Y-axis ticks (5 ticks)
  const yTicks: number[] = [];
  for (let i = 0; i <= 4; i++) {
    yTicks.push(yMin + (yRange * i) / 4);
  }
  const fmtY = (v: number) => Math.abs(v) >= 1000 ? `$${(v / 1000).toFixed(1)}k` : `$${v.toFixed(0)}`;

  // X-axis ticks — deduplicated dates
  const seen = new Set<string>();
  const xTicks: { ts: number; label: string }[] = [];
  for (const p of points) {
    if (!seen.has(p.date)) {
      seen.add(p.date);
      xTicks.push({ ts: p.ts, label: p.date });
    }
  }

  const finalPnl = points[points.length - 1].pnl;
  const finalColor = finalPnl > 0 ? "#4ade80" : finalPnl < 0 ? "#f87171" : "#999";

  // Trade span info
  const tsList = tradesInWindow.map((t) => t.timestamp);
  const tradeMinTs = tsList.length ? Math.min(...tsList) : 0;
  const tradeMaxTs = tsList.length ? Math.max(...tsList) : 0;
  const fmt = (ts: number) => new Date(ts).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  const spanMs = tradeMaxTs - tradeMinTs;
  const spanLabel = spanMs < 3600_000 ? `${Math.round(spanMs / 60_000)}m`
    : spanMs < 86400_000 ? `${(spanMs / 3600_000).toFixed(1)}h`
    : `${(spanMs / 86400_000).toFixed(1)}d`;

  // Hover state
  const [hovered, setHovered] = useState<number | null>(null);
  const svgRef = (el: SVGSVGElement | null) => {
    if (!el) return;
    const onMove = (e: MouseEvent) => {
      const rect = el.getBoundingClientRect();
      const mx = ((e.clientX - rect.left) / rect.width) * W;
      if (mx < pad.left || mx > W - pad.right) { setHovered(null); return; }
      let best = 0, bestDist = Infinity;
      for (let i = 0; i < points.length; i++) {
        const d = Math.abs(toX(points[i].ts) - mx);
        if (d < bestDist) { bestDist = d; best = i; }
      }
      setHovered(best);
      onHoverChange?.(best);
    };
    const onLeave = () => { setHovered(null); onHoverChange?.(null); };
    el.onmousemove = onMove;
    el.onmouseleave = onLeave;
  };

  const activeIdx = hovered ?? highlightIndex ?? null;
  const hp = activeIdx !== null && activeIdx >= 0 && activeIdx < points.length ? points[activeIdx] : null;

  return (
    <div className="pixel-panel p-5">
      <div className="flex items-center justify-between mb-2 flex-wrap gap-2">
        <div className="text-[16px] text-pixel-gray-light tracking-wider">
          {dayLabel} P&L CURVE (MTM){filtered && <span className="text-yellow-400 ml-2 text-[13px]">FILTERED</span>}
        </div>
        <div className={`text-[15px] font-mono`} style={{ color: finalColor }}>{formatPnl(finalPnl)}</div>
      </div>
      {tsList.length > 0 && (
        <div className="text-[14px] text-pixel-gray mb-3 font-mono">
          {tradesInWindow.length} TRADES · {fmt(tradeMinTs)} → {fmt(tradeMaxTs)} · SPANS {spanLabel}
        </div>
      )}
      <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ height: "auto", maxHeight: 280 }}>
        <defs>
          <linearGradient id="pnlFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#ffffff" stopOpacity={0.15} />
            <stop offset="100%" stopColor="#ffffff" stopOpacity={0} />
          </linearGradient>
          <filter id="glow">
            <feGaussianBlur stdDeviation="3" result="coloredBlur"/>
            <feMerge>
              <feMergeNode in="coloredBlur"/>
              <feMergeNode in="SourceGraphic"/>
            </feMerge>
          </filter>
        </defs>
        {/* Grid lines */}
        {yTicks.map((v, i) => (
          <line key={i} x1={pad.left} y1={toY(v)} x2={W - pad.right} y2={toY(v)} stroke="#222" strokeWidth={1} />
        ))}
        {/* Zero line */}
        {minPnl < 0 && maxPnl > 0 && (
          <line x1={pad.left} y1={toY(0)} x2={W - pad.right} y2={toY(0)} stroke="#444" strokeWidth={1} strokeDasharray="4,4" />
        )}
        {/* Y-axis labels */}
        {yTicks.map((v, i) => (
          <text key={i} x={pad.left - 6} y={toY(v) + 3} textAnchor="end" fill="#666" fontSize={9} fontFamily="'IBM Plex Mono', monospace">
            {fmtY(v)}
          </text>
        ))}
        {/* X-axis labels */}
        {xTicks.map((t, i) => (
          <text key={i} x={toX(t.ts)} y={H - 8} textAnchor="middle" fill="#666" fontSize={9} fontFamily="'IBM Plex Mono', monospace">
            {t.label}
          </text>
        ))}
        {/* Axes */}
        <line x1={pad.left} y1={pad.top} x2={pad.left} y2={H - pad.bottom} stroke="#444" strokeWidth={1} />
        <line x1={pad.left} y1={H - pad.bottom} x2={W - pad.right} y2={H - pad.bottom} stroke="#444" strokeWidth={1} />
        {/* Area fill */}
        <path d={areaPath} fill="url(#pnlFill)" />
        {/* Line with glow */}
        <path d={linePath} fill="none" stroke="#fff" strokeWidth={1} strokeOpacity={0.3} strokeLinejoin="round" strokeLinecap="round" filter="url(#glow)" />
        <path d={linePath} fill="none" stroke="#fff" strokeWidth={2.5} strokeLinejoin="round" strokeLinecap="round" />
        {/* Dots */}
        {points.map((p, i) => {
          const x = toX(p.ts), y = toY(p.pnl);
          if (p.side === "MARK") {
            return <circle key={i} cx={x} cy={y} r={4} fill="#1a1a1a" stroke="#fff" strokeWidth={2} />;
          }
          if (p.side === "BUY") {
            return <circle key={i} cx={x} cy={y} r={3} fill="#444" stroke="#888" strokeWidth={1} />;
          }
          const dotFill = p.realized > 0 ? "#4ade80" : p.realized < 0 ? "#f87171" : "#999";
          return <rect key={i} x={x - 3} y={y - 3} width={6} height={6} fill={dotFill} stroke="#000" strokeWidth={1} />;
        })}
        {/* Hover crosshair + highlight */}
        {hp && (
          <>
            <line x1={toX(hp.ts)} y1={pad.top} x2={toX(hp.ts)} y2={H - pad.bottom} stroke="#555" strokeWidth={1} strokeDasharray="3,3" />
            <circle cx={toX(hp.ts)} cy={toY(hp.pnl)} r={5} fill="#fff" stroke="#000" strokeWidth={2} />
          </>
        )}
      </svg>
      {/* Tooltip */}
      {hp && (
        <div className="pixel-panel p-2 mt-1 text-[13px] font-mono flex items-center gap-3 flex-wrap">
          <span className="text-pixel-white font-bold">{hp.time}</span>
          {hp.side !== "MARK" && (
            <span className="text-pixel-white">
              {hp.side} {hp.size.toFixed(0)} @ {Math.round(hp.price * 100)}c
              <span className="text-pixel-gray-light"> · ${(hp.size * hp.price).toFixed(2)}</span>
            </span>
          )}
          {hp.side === "MARK" && <span className="text-pixel-white">MARK TO MARKET</span>}
          {hp.side === "SELL" && hp.buyPrice !== undefined && (
            <span className="text-pixel-gray-light">
              ENTRY {Math.round(hp.buyPrice * 100)}c
              {hp.buyTimestamp ? ` · ${timeAgo(hp.buyTimestamp)}` : ""}
            </span>
          )}
          {hp.side === "SELL" && (
            <span style={{ color: hp.realized > 0 ? "#4ade80" : hp.realized < 0 ? "#f87171" : "#999" }}>
              {hp.realized >= 0 ? "+" : ""}${hp.realized.toFixed(2)}
            </span>
          )}
          <span style={{ color: hp.pnl > 0 ? "#4ade80" : hp.pnl < 0 ? "#f87171" : "#fff" }}>
            MTM {hp.pnl >= 0 ? "+" : ""}${hp.pnl.toFixed(2)}
          </span>
          {hp.market && <span className="text-pixel-gray">{hp.market}</span>}
        </div>
      )}
    </div>
  );
}
