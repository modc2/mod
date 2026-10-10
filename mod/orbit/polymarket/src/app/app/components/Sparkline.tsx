"use client";

// One sparkline for every P&L curve on the console — extracted from
// CopyTrading so the FIND TRADERS cards and the /traders board draw the
// same picture. Pure SVG, no deps.
//
// `stretch` renders at 100% width of the parent (viewBox + non-scaling
// strokes, so the line stays 1.5px however wide the card is) — the fixed
// width/height path is what the dense /traders table keeps using.

import { formatPnl } from "../lib/polymarket";

export default function Sparkline({
  data,
  width = 120,
  height = 28,
  stretch = false,
  hoverLabel,
}: {
  /** Cumulative P&L points, oldest → newest (the server's 12-bucket curve). */
  data: number[];
  width?: number;
  height?: number;
  /** Fill the parent's width; strokes keep their pixel weight. */
  stretch?: boolean;
  /** Per-bucket hover captions, e.g. "1D window · bucket 3/12". */
  hoverLabel?: (index: number, value: number) => string;
}) {
  const svgProps = stretch
    ? { width: "100%" as const, height, viewBox: `0 0 ${width} ${height}`, preserveAspectRatio: "none" }
    : { width, height };

  if (!data || data.length < 2) {
    return (
      <svg {...svgProps} className="opacity-20">
        <line x1={0} y1={height / 2} x2={width} y2={height / 2} stroke="currentColor" strokeWidth={1} strokeDasharray="3,3" vectorEffect="non-scaling-stroke" />
      </svg>
    );
  }
  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const pad = 3;
  const pts = data.map((v, i) => {
    const x = (i / (data.length - 1)) * (width - pad * 2) + pad;
    const y = height - pad - ((v - min) / range) * (height - pad * 2);
    return `${x},${y}`;
  }).join(" ");
  const final = data[data.length - 1];
  // CSS vars so the light theme gets its darker green/red variants — SVG
  // attributes don't resolve var(), hence the style={} usage below.
  const color = final > 0 ? "var(--up)" : final < 0 ? "var(--down)" : "var(--flat)";
  const zeroY = max <= 0 ? pad : min >= 0 ? height - pad : height - pad - ((0 - min) / range) * (height - pad * 2);

  const areaPoints = data.map((v, i) => {
    const x = (i / (data.length - 1)) * (width - pad * 2) + pad;
    const y = height - pad - ((v - min) / range) * (height - pad * 2);
    return [x, y] as [number, number];
  });
  const areaPath = `M${areaPoints[0][0]},${areaPoints[0][1]} ${areaPoints.slice(1).map(p => `L${p[0]},${p[1]}`).join(" ")} L${areaPoints[areaPoints.length - 1][0]},${height - pad} L${areaPoints[0][0]},${height - pad} Z`;

  // Hover slices: one invisible rect per bucket carrying a native <title>
  // tooltip — the cheapest crosshair there is, and it survives SSR.
  const sliceW = (width - pad * 2) / (data.length - 1);

  return (
    <svg {...svgProps}>
      {min < 0 && max > 0 && (
        <line x1={0} y1={zeroY} x2={width} y2={zeroY} stroke="currentColor" strokeOpacity={0.25} strokeWidth={1} strokeDasharray="2,2" vectorEffect="non-scaling-stroke" />
      )}
      <path d={areaPath} style={{ fill: color }} opacity={0.08} />
      <polyline points={pts} fill="none" style={{ stroke: color }} strokeWidth={1.5} strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
      <circle cx={areaPoints[areaPoints.length - 1][0]} cy={areaPoints[areaPoints.length - 1][1]} r={2} style={{ fill: color }} />
      {hoverLabel &&
        data.map((v, i) => (
          <rect
            key={i}
            x={pad + i * sliceW - sliceW / 2}
            y={0}
            width={sliceW}
            height={height}
            fill="transparent"
          >
            <title>{`${formatPnl(v)} — ${hoverLabel(i, v)}`}</title>
          </rect>
        ))}
    </svg>
  );
}
