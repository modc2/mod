'use client';
/* One line chart for the whole console — subnet price, trader equity, wallet
 * curve. Accent line, 10% area wash, hairline gridlines, clean y ticks, and a
 * crosshair + tooltip that snaps to the nearest point. Plain SVG, no lib. */
import { ReactNode, useEffect, useMemo, useRef, useState } from 'react';

export interface Pt { t: number; v: number; sub?: string }

function niceTicks(min: number, max: number, n: number) {
  if (min === max) { min *= 0.95; max = max * 1.05 || 1; }
  const span = max - min, step0 = span / n, mag = Math.pow(10, Math.floor(Math.log10(step0)));
  const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => span / s <= n) || mag * 10;
  const ticks: number[] = [];
  for (let v = Math.ceil(min / step) * step; v <= max + 1e-12; v += step) ticks.push(v);
  return ticks;
}

export default function LineChart({ series, hours, height = 260, color = 'var(--accent)',
  fmtY, empty }: {
  series: Pt[]; hours: number; height?: number; color?: string;
  fmtY: (v: number) => string; empty?: ReactNode;
}) {
  const box = useRef<HTMLDivElement>(null);
  const [W, setW] = useState(780);
  const [hover, setHover] = useState<number | null>(null);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setW(Math.max(el.clientWidth || 780, 320)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const H = height, padL = 62, padR = 18, padT = 14, padB = 26;
  const g = useMemo(() => {
    if (series.length < 2) return null;
    const tMin = series[0].t, tMax = series[series.length - 1].t || tMin + 1;
    let vMin = Math.min(...series.map(p => p.v)), vMax = Math.max(...series.map(p => p.v));
    if (vMin === vMax) { vMin *= 0.98; vMax = vMax * 1.02 || 1; }
    const X = (t: number) => padL + (t - tMin) / ((tMax - tMin) || 1) * (W - padL - padR);
    const Y = (v: number) => padT + (vMax - v) / (vMax - vMin) * (H - padT - padB);
    const line = series.map((p, i) => `${i ? 'L' : 'M'}${X(p.t).toFixed(1)},${Y(p.v).toFixed(1)}`).join('');
    const area = line + `L${X(tMax).toFixed(1)},${H - padB}L${X(tMin).toFixed(1)},${H - padB}Z`;
    const nX = Math.min(5, series.length);
    const xTicks = Array.from({ length: nX }, (_, i) => tMin + i * (tMax - tMin) / (nX - 1));
    return { X, Y, line, area, xTicks, nX, yTicks: niceTicks(vMin, vMax, 4) };
  }, [series, W, H]);

  const tLab = (t: number) => {
    const d = new Date(t * 1000);
    return hours <= 24 ? d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
      : d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  };

  if (!g) return <div ref={box} className="chart-empty muted">{empty ||
    'Not enough history yet — the indexer snapshots on an interval; charts fill in as it runs.'}</div>;

  const onMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const mx = (e.clientX - r.left) * (W / r.width);
    let best = 0, bd = Infinity;
    series.forEach((p, i) => { const d = Math.abs(g.X(p.t) - mx); if (d < bd) { bd = d; best = i; } });
    setHover(best);
  };
  const hp = hover != null ? series[hover] : null;
  const last = series[series.length - 1];
  const tipLeft = hp ? Math.min(Math.max(g.X(hp.t) - 80, 0), W - 170) : 0;

  return (
    <div ref={box} className="chart-box">
      {hp && (
        <div className="chart-tip" style={{ left: `${(tipLeft / W) * 100}%`, top: Math.max(g.Y(hp.v) - 58, 0) }}>
          <b>{fmtY(hp.v)}</b>
          <span>{hp.sub ? hp.sub + ' · ' : ''}{new Date(hp.t * 1000).toLocaleString(undefined,
            { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}</span>
        </div>
      )}
      <svg width="100%" height={H} viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none"
           onPointerMove={onMove} onPointerLeave={() => setHover(null)}>
        {g.yTicks.map(v => (
          <g key={v}>
            <line x1={padL} y1={g.Y(v)} x2={W - padR} y2={g.Y(v)} stroke="var(--line)" strokeWidth="1" />
            <text x={padL - 8} y={g.Y(v) + 4} textAnchor="end" fontSize="10.5" fill="var(--ink2)"
                  fontFamily="var(--mono)">{fmtY(v).replace(/^τ /, '')}</text>
          </g>
        ))}
        {g.xTicks.map((t, i) => (
          <text key={i} x={g.X(t)} y={H - 8} fontSize="10.5" fill="var(--ink2)"
                textAnchor={i === 0 ? 'start' : i === g.nX - 1 ? 'end' : 'middle'}>{tLab(t)}</text>
        ))}
        <path d={g.area} fill={color} opacity="0.1" />
        <path d={g.line} fill="none" stroke={color} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
        <circle cx={g.X(last.t)} cy={g.Y(last.v)} r="4" fill={color} stroke="var(--card)" strokeWidth="2" />
        {hp && <>
          <line x1={g.X(hp.t)} x2={g.X(hp.t)} y1={padT} y2={H - padB} stroke="var(--ink3)" strokeWidth="1" />
          <circle cx={g.X(hp.t)} cy={g.Y(hp.v)} r="4" fill={color} stroke="var(--card)" strokeWidth="2" />
        </>}
        <rect x={padL} y={padT} width={W - padL - padR} height={H - padT - padB} fill="transparent" />
      </svg>
    </div>
  );
}
