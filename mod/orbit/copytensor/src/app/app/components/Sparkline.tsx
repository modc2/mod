"use client";

import { useId } from "react";

/**
 * Dependency-free price sparkline.
 *
 * Recharts is fine for the one big detail chart, but a grid of 128 cards
 * wants something that costs a single <path>. Colors follow the series'
 * own direction (first → last) so a card's tint always agrees with its
 * change chip.
 */
export default function Sparkline({
  values,
  width = 120,
  height = 34,
  color,
  fill = true,
  strokeWidth = 1.5,
  className = "",
}: {
  values: number[] | null | undefined;
  width?: number;
  height?: number;
  color?: string;
  fill?: boolean;
  strokeWidth?: number;
  className?: string;
}) {
  const gradId = useId().replace(/:/g, "");
  const pts = (values || []).filter((v) => typeof v === "number" && isFinite(v));

  if (pts.length < 2) {
    return (
      <svg
        width={width}
        height={height}
        viewBox={`0 0 ${width} ${height}`}
        className={className}
        aria-hidden
      >
        <line
          x1={0}
          y1={height / 2}
          x2={width}
          y2={height / 2}
          stroke="currentColor"
          strokeOpacity={0.18}
          strokeDasharray="3 4"
        />
      </svg>
    );
  }

  const min = Math.min(...pts);
  const max = Math.max(...pts);
  // A dead-flat series (root's 1.0 peg) would divide by zero — pin it to
  // the middle of the box instead of collapsing onto the baseline.
  const span = max - min || 1;
  const pad = strokeWidth;
  const usable = height - pad * 2;
  const x = (i: number) => (i / (pts.length - 1)) * width;
  const y = (v: number) =>
    max === min ? height / 2 : pad + (1 - (v - min) / span) * usable;

  const line = pts.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(2)},${y(v).toFixed(2)}`).join(" ");
  const area = `${line} L${width},${height} L0,${height} Z`;

  const up = pts[pts.length - 1] >= pts[0];
  const stroke = color || (up ? "#4ade80" : "#f87171");

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      className={className}
      aria-hidden
    >
      {fill && (
        <>
          <defs>
            <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={stroke} stopOpacity={0.28} />
              <stop offset="100%" stopColor={stroke} stopOpacity={0} />
            </linearGradient>
          </defs>
          <path d={area} fill={`url(#${gradId})`} stroke="none" />
        </>
      )}
      <path
        d={line}
        fill="none"
        stroke={stroke}
        strokeWidth={strokeWidth}
        strokeLinecap="round"
        strokeLinejoin="round"
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}
