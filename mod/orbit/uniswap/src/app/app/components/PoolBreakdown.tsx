"use client";
import { PoolStats } from "../lib/types";
import { fmtUsd, pnlColor } from "../lib/format";

interface Props {
  pools: PoolStats[];
  diversityScore: number;
}

export default function PoolBreakdown({ pools, diversityScore }: Props) {
  if (!pools || pools.length === 0) return null;

  const totalVol = pools.reduce((sum, p) => sum + p.volume_usd, 0);

  // Colors for the pie segments
  const colors = [
    "#ff007a", "#7b3fe4", "#4ade80", "#f59e0b", "#06b6d4",
    "#8b5cf6", "#ec4899", "#14b8a6", "#f97316", "#6366f1",
  ];

  // SVG donut chart
  const size = 120;
  const radius = 50;
  const cx = size / 2;
  const cy = size / 2;

  let cumulativeAngle = 0;
  const segments = pools.slice(0, 8).map((pool, i) => {
    const pct = pool.volume_usd / totalVol;
    const angle = pct * 360;
    const startAngle = cumulativeAngle;
    cumulativeAngle += angle;

    const startRad = ((startAngle - 90) * Math.PI) / 180;
    const endRad = (((startAngle + angle) - 90) * Math.PI) / 180;

    const x1 = cx + radius * Math.cos(startRad);
    const y1 = cy + radius * Math.sin(startRad);
    const x2 = cx + radius * Math.cos(endRad);
    const y2 = cy + radius * Math.sin(endRad);

    const largeArc = angle > 180 ? 1 : 0;

    const d = `M ${cx} ${cy} L ${x1} ${y1} A ${radius} ${radius} 0 ${largeArc} 1 ${x2} ${y2} Z`;

    return { d, color: colors[i % colors.length], pool, pct };
  });

  return (
    <div className="card p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-xs font-bold text-uni-muted uppercase">Pool Breakdown</h3>
        <span className="text-[10px] text-uni-muted">
          diversity: {(diversityScore * 100).toFixed(0)}%
        </span>
      </div>

      <div className="flex gap-4">
        {/* Donut */}
        <svg width={size} height={size} className="shrink-0">
          {segments.map((seg, i) => (
            <path key={i} d={seg.d} fill={seg.color} opacity={0.8} />
          ))}
          <circle cx={cx} cy={cy} r={25} fill="var(--uni-card)" />
        </svg>

        {/* Legend */}
        <div className="flex-1 space-y-1 overflow-hidden">
          {segments.map((seg, i) => (
            <div key={i} className="flex items-center gap-2 text-[10px]">
              <div
                className="w-2 h-2 rounded-full shrink-0"
                style={{ background: seg.color }}
              />
              <span className="truncate flex-1">
                {seg.pool.token0}/{seg.pool.token1}
              </span>
              <span className="text-uni-muted">{(seg.pct * 100).toFixed(0)}%</span>
              <span className={pnlColor(seg.pool.pnl_usd)}>
                {fmtUsd(seg.pool.pnl_usd)}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
