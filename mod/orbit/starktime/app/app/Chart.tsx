'use client';

// Single-series line/area chart: one validated hue (--chart-1), 2px line,
// recessive grid, crosshair + tooltip on hover, optional marker. A single
// series needs no legend box — the card title names it.

import { useMemo, useRef, useState } from 'react';

export type Pt = { x: number; y: number };

export default function Chart({
  data,
  height = 220,
  area = false,
  marker,
  fmtX,
  fmtY,
  label,
}: {
  data: Pt[];
  height?: number;
  area?: boolean;
  marker?: Pt | null;
  fmtX: (x: number) => string;
  fmtY: (y: number) => string;
  label: string;
}) {
  const W = 640;
  const H = height;
  const pad = { l: 46, r: 14, t: 12, b: 26 };
  const ref = useRef<SVGSVGElement>(null);
  const [hover, setHover] = useState<Pt | null>(null);

  const geo = useMemo(() => {
    if (data.length < 2) return null;
    const xs = data.map((d) => d.x);
    const ys = data.map((d) => d.y);
    const x0 = Math.min(...xs);
    const x1 = Math.max(...xs);
    const yMax = Math.max(...ys) * 1.08 || 1;
    const yMin = area ? 0 : Math.min(0, Math.min(...ys));
    const sx = (x: number) => pad.l + ((x - x0) / (x1 - x0 || 1)) * (W - pad.l - pad.r);
    const sy = (y: number) => H - pad.b - ((y - yMin) / (yMax - yMin || 1)) * (H - pad.t - pad.b);
    const line = data.map((d, i) => `${i ? 'L' : 'M'}${sx(d.x).toFixed(1)},${sy(d.y).toFixed(1)}`).join('');
    const fill = `${line}L${sx(x1).toFixed(1)},${sy(yMin)}L${sx(x0).toFixed(1)},${sy(yMin)}Z`;
    const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => yMin + f * (yMax - yMin));
    const xticks = [0, 0.25, 0.5, 0.75, 1].map((f) => x0 + f * (x1 - x0));
    return { sx, sy, line, fill, ticks, xticks, x0, x1 };
  }, [data, area, H]);

  if (!geo) return <div className="empty">no data</div>;

  const onMove = (e: React.MouseEvent) => {
    const r = ref.current?.getBoundingClientRect();
    if (!r) return;
    const px = ((e.clientX - r.left) / r.width) * W;
    let best = data[0];
    for (const d of data) if (Math.abs(geo.sx(d.x) - px) < Math.abs(geo.sx(best.x) - px)) best = d;
    setHover(best);
  };

  const tipLeft = hover ? (geo.sx(hover.x) / W) * 100 : 0;

  return (
    <div className="chart" role="img" aria-label={label}>
      <svg
        ref={ref}
        viewBox={`0 0 ${W} ${H}`}
        onMouseMove={onMove}
        onMouseLeave={() => setHover(null)}
      >
        {geo.ticks.map((t, i) => (
          <g key={i}>
            <line x1={pad.l} x2={W - pad.r} y1={geo.sy(t)} y2={geo.sy(t)} className="grid" />
            <text x={pad.l - 8} y={geo.sy(t) + 4} className="axis" textAnchor="end">
              {fmtY(t)}
            </text>
          </g>
        ))}
        {geo.xticks.map((t, i) => (
          <text key={i} x={geo.sx(t)} y={H - 8} className="axis" textAnchor={i === 0 ? 'start' : i === 4 ? 'end' : 'middle'}>
            {fmtX(t)}
          </text>
        ))}
        {area && <path d={geo.fill} className="area" />}
        <path d={geo.line} className="line" />
        {marker && (
          <g>
            <line x1={geo.sx(marker.x)} x2={geo.sx(marker.x)} y1={pad.t} y2={H - pad.b} className="mark-rule" />
            <circle cx={geo.sx(marker.x)} cy={geo.sy(marker.y)} r={5} className="dot" />
          </g>
        )}
        {hover && (
          <g>
            <line x1={geo.sx(hover.x)} x2={geo.sx(hover.x)} y1={pad.t} y2={H - pad.b} className="cross" />
            <circle cx={geo.sx(hover.x)} cy={geo.sy(hover.y)} r={4} className="dot" />
          </g>
        )}
        {/* oversized hit target */}
        <rect x={0} y={0} width={W} height={H} fill="transparent" />
      </svg>
      {hover && (
        <div className="tip" style={{ left: `clamp(60px, ${tipLeft}%, calc(100% - 60px))` }}>
          <b>{fmtY(hover.y)}</b>
          <span>{fmtX(hover.x)}</span>
        </div>
      )}
    </div>
  );
}
