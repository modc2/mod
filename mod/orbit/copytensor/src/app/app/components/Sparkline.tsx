"use client";

/**
 * Dependency-free price sparkline, plotted on a pixel lattice.
 *
 * Recharts is fine for the one big detail chart, but a grid of 128 cards
 * wants something that costs a single <path>. Colors follow the series'
 * own direction (first → last) so a card's tint always agrees with its
 * change chip.
 *
 * Every vertex snaps to a CELL-px grid and the path moves in steps —
 * across, then up — so the trace reads as something a console could
 * actually have drawn. A smooth diagonal is the giveaway that a chart
 * was rendered rather than plotted.
 */
import { useThemeColors } from "../context/ThemeContext";

const CELL = 2;
const snap = (n: number) => Math.round(n / CELL) * CELL;
export default function Sparkline({
  values,
  width = 120,
  height = 34,
  color,
  fill = true,
  strokeWidth = 2,
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
  const skin = useThemeColors();
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

  // Staircase: hold the previous level across to the new x, then step to
  // the new level. Two axis-aligned segments per point, no diagonals.
  const line = pts
    .map((v, i) => {
      const px = snap(x(i));
      const py = snap(y(v));
      if (!i) return `M${px},${py}`;
      return `H${px} V${py}`;
    })
    .join(" ");
  const area = `${line} V${height} H0 Z`;

  const up = pts[pts.length - 1] >= pts[0];
  const stroke = color || (up ? skin.lime : skin.red);

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      className={className}
      aria-hidden
    >
      {/* Flat fill, not a gradient — a fade needs more colours than the
          palette has. One translucent block under the trace. */}
      {fill && <path d={area} fill={stroke} fillOpacity={0.16} stroke="none" shapeRendering="crispEdges" />}
      <path
        d={line}
        fill="none"
        stroke={stroke}
        strokeWidth={strokeWidth}
        strokeLinecap="butt"
        strokeLinejoin="miter"
        shapeRendering="crispEdges"
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}
