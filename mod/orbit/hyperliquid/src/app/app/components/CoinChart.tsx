"use client";

import { useEffect, useRef, useState } from "react";
import type { Candle } from "../lib/api";

const LINE = "var(--chart-line)";
const SURFACE = "var(--chart-surface)";
const GRID = "var(--chart-grid)";
const H = 220;
const PAD = { t: 14, r: 14, b: 24, l: 60 };

type Fill = { time: number | string; px: number | string; side: string };
type DotState = { cx: number; cy: number; px: number; side: string; time: number };

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

const fmtPrice = (n: number): string => {
  if (n >= 10000) return `$${(n / 1000).toFixed(1)}k`;
  if (n >= 1000) return n.toFixed(0);
  if (n >= 10) return n.toFixed(1);
  return n.toPrecision(4);
};

export default function CoinChart({ candles, fills }: { candles: Candle[]; fills: Fill[] }) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(0);
  const [dot, setDot] = useState<DotState | null>(null);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setW(el.clientWidth));
    ro.observe(el);
    setW(el.clientWidth);
    return () => ro.disconnect();
  }, []);

  if (candles.length < 2) {
    return <div className="px-4 py-6 text-xs text-muted">no candle data available</div>;
  }

  const t0 = candles[0].t;
  const t1 = candles[candles.length - 1].t;

  const fillData = fills
    .map((f) => ({ t: Number(f.time), px: Number(f.px), side: f.side }))
    .filter((f) => Number.isFinite(f.t) && Number.isFinite(f.px) && f.t >= t0 && f.t <= t1);

  const closes = candles.map((c) => c.c);
  const allPrices = [...closes, ...fillData.map((f) => f.px)];
  let lo = Math.min(...allPrices);
  let hi = Math.max(...allPrices);
  if (hi === lo) { hi += 1; lo -= 1; }
  const padV = (hi - lo) * 0.1;
  lo -= padV; hi += padV;

  const iw = Math.max(0, w - PAD.l - PAD.r);
  const ih = H - PAD.t - PAD.b;
  const X = (t: number) => PAD.l + ((t - t0) / (t1 - t0 || 1)) * iw;
  const Y = (v: number) => PAD.t + (1 - (v - lo) / (hi - lo)) * ih;

  const line = candles.map((c, i) => `${i ? "L" : "M"}${X(c.t).toFixed(1)},${Y(c.c).toFixed(1)}`).join("");
  const yTicks = niceTicks(lo, hi);
  const xTickIdx = [0, 0.25, 0.5, 0.75, 1].map((f) => Math.round(f * (candles.length - 1)));
  const spanMs = t1 - t0;

  return (
    <div>
      <div
        ref={wrapRef}
        className="relative outline-none"
        tabIndex={0}
        role="img"
        aria-label="coin price chart with fill markers"
        onPointerLeave={() => setDot(null)}
      >
        {w > 0 && (
          <svg width={w} height={H} className="block">
            {yTicks.map((v) => (
              <g key={v}>
                <line x1={PAD.l} x2={w - PAD.r} y1={Y(v)} y2={Y(v)} stroke={GRID} strokeWidth={1} />
                <text x={PAD.l - 6} y={Y(v) + 3} textAnchor="end" fontSize={10} className="fill-muted num">
                  {fmtPrice(v)}
                </text>
              </g>
            ))}
            {xTickIdx.map((i) => (
              <text key={i} x={X(candles[i].t)} y={H - 8} textAnchor="middle" fontSize={10} className="fill-muted">
                {fmtTime(candles[i].t, spanMs)}
              </text>
            ))}
            <path d={line} fill="none" stroke={LINE} strokeWidth={1.5} strokeLinejoin="round" strokeLinecap="round" />
            {fillData.map((f, i) => (
              <circle
                key={i}
                cx={X(f.t)}
                cy={Y(f.px)}
                r={5}
                fill={f.side === "B" ? "rgb(var(--c-win))" : "rgb(var(--c-loss))"}
                fillOpacity={0.85}
                stroke={SURFACE}
                strokeWidth={1.5}
                className="cursor-pointer"
                onPointerEnter={() => setDot({ cx: X(f.t), cy: Y(f.px), px: f.px, side: f.side, time: f.t })}
              />
            ))}
          </svg>
        )}
        {dot && (
          <div
            className="absolute pointer-events-none rounded-md border border-white/[0.08] bg-black/80 px-2.5 py-1.5"
            style={{
              left: Math.min(Math.max(dot.cx + 10, PAD.l), w - 150),
              top: Math.max(dot.cy - 48, 4),
            }}
          >
            <div className={`num text-sm ${dot.side === "B" ? "text-win" : "text-loss"}`}>
              {dot.side === "B" ? "buy" : "sell"} @ {fmtPrice(dot.px)}
            </div>
            <div className="text-[10px] text-muted whitespace-nowrap">
              {new Date(dot.time).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}
            </div>
          </div>
        )}
      </div>
      <div className="px-4 py-1.5 text-[10px] text-muted border-t border-border flex gap-4">
        <span className="text-win">● buy</span>
        <span className="text-loss">● sell</span>
        <span>{fillData.length} fill{fillData.length !== 1 ? "s" : ""} in chart window</span>
      </div>
    </div>
  );
}
