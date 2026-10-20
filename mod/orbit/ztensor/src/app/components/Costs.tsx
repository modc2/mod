'use client';
/* COSTS: mainnet dollars per action (GET economics: live gas x token price
 * applied to costs measured on each local chain) and the chain's own units
 * vs its hard limit by ring size (GET costs). Columns come from the data,
 * so a new adapter's fields render without UI changes. */
import { useEffect, useRef, useState } from 'react';
import type { Chain } from '@/lib/api';
import { fmt, usd } from '@/lib/api';
import Forecast from './Forecast';
import Experiment from './Experiment';

const SHAPES = ['circle', 'square', 'diamond', 'triangle'] as const;

function Marker({ shape, x, y, color }: { shape: typeof SHAPES[number]; x: number; y: number; color: string }) {
  const s = 5;
  const common = { fill: color, stroke: 'var(--bg2)', strokeWidth: 2 };
  if (shape === 'square') return <rect x={x - s} y={y - s} width={2 * s} height={2 * s} rx={1.5} {...common} />;
  if (shape === 'diamond') return <rect x={x - s} y={y - s} width={2 * s} height={2 * s} rx={1.5} transform={`rotate(45 ${x} ${y})`} {...common} />;
  if (shape === 'triangle') return <path d={`M${x} ${y - s - 1}L${x + s + 1} ${y + s}L${x - s - 1} ${y + s}Z`} {...common} />;
  return <circle cx={x} cy={y} r={s} {...common} />;
}

/* $ per score by ring size, one line per chain. Log y: costs span ~3 decades. */
function RingChart({ chains, eco, sel }: { chains: Chain[]; eco: any; sel?: string }) {
  const [hover, setHover] = useState<number | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const [W, setW] = useState(720);
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setW(Math.max(320, Math.round(e.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const series = chains.map((c, i) => ({
    c, shape: SHAPES[i % SHAPES.length],
    pts: ((eco?.chains?.[c.chain]?.by_ring || []) as any[]).filter((r) => r.usd > 0).map((r) => ({ ring: r.ring, usd: r.usd as number })),
  })).filter((s) => s.pts.length);
  if (!series.length) return <div className="empty" ref={box}>no priced runs yet</div>;
  const rings = Array.from(new Set(series.flatMap((s) => s.pts.map((p) => p.ring)))).sort((a, b) => a - b);
  const H = 300, L = 64, R = 96, T = 16, B = 34;
  const all = series.flatMap((s) => s.pts.map((p) => p.usd));
  const lo = Math.pow(10, Math.floor(Math.log10(Math.min(...all))));
  const hi = Math.pow(10, Math.ceil(Math.log10(Math.max(...all))));
  const x = (r: number) => L + (Math.log2(r) - Math.log2(rings[0])) / Math.max(1e-9, Math.log2(rings[rings.length - 1]) - Math.log2(rings[0])) * (W - L - R);
  const y = (v: number) => T + (1 - (Math.log10(v) - Math.log10(lo)) / (Math.log10(hi) - Math.log10(lo))) * (H - T - B);
  const ticks: number[] = [];
  for (let v = lo; v <= hi * 1.001; v *= 10) ticks.push(v);
  const hx = hover !== null ? x(rings[hover]) : 0;
  // direct labels at each line's end, pushed apart so they never overlap
  const labY = new Map<string, number>();
  [...series].map((s) => ({ k: s.c.chain, v: y(s.pts[s.pts.length - 1].usd) + 4 }))
    .sort((a, b) => a.v - b.v)
    .reduce((prev, l) => { const v = Math.max(l.v, prev + 14); labY.set(l.k, v); return v; }, -Infinity);

  return (
    <div className="chart" ref={box}>
      <div className="legend" style={{ marginBottom: 10 }}>
        {series.map((s) => (
          <span key={s.c.chain}>
            <svg width={14} height={14}><Marker shape={s.shape} x={7} y={7} color={s.c.color || '#fff'} /></svg>{s.c.title}
          </span>
        ))}
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} width={W} height={H}
        onMouseMove={(e) => {
          const r = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
          const px = ((e.clientX - r.left) / r.width) * W;
          let best = 0;
          rings.forEach((g, i) => { if (Math.abs(x(g) - px) < Math.abs(x(rings[best]) - px)) best = i; });
          setHover(best);
        }}
        onMouseLeave={() => setHover(null)}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={L} x2={W - R} y1={y(t)} y2={y(t)} stroke="rgba(255,255,255,0.06)" />
            <text x={L - 8} y={y(t) + 4} textAnchor="end" fontSize="10.5" fill="var(--ink3)" fontFamily="var(--mono)">{usd(t)}</text>
          </g>
        ))}
        {rings.map((g) => (
          <text key={g} x={x(g)} y={H - 12} textAnchor="middle" fontSize="10.5" fill="var(--ink3)" fontFamily="var(--mono)">{g}</text>
        ))}
        <text x={W - R} y={H - 12} textAnchor="start" dx={10} fontSize="10" fill="var(--ink3)">ring size</text>
        {hover !== null && <line x1={hx} x2={hx} y1={T} y2={H - B} stroke="rgba(255,255,255,0.18)" />}
        {series.map((s) => {
          const col = s.c.color || '#fff';
          const d = s.pts.map((p, i) => `${i ? 'L' : 'M'}${x(p.ring)} ${y(p.usd)}`).join('');
          const end = s.pts[s.pts.length - 1];
          return (
            <g key={s.c.chain} opacity={sel && sel !== s.c.chain ? 0.55 : 1}>
              <path d={d} fill="none" stroke={col} strokeWidth={2} strokeLinejoin="round" />
              {s.pts.map((p) => <Marker key={p.ring} shape={s.shape} x={x(p.ring)} y={y(p.usd)} color={col} />)}
              <text x={x(end.ring) + 10} y={labY.get(s.c.chain)} fontSize="11.5" fontWeight={650} fill="var(--ink2)">{s.c.title}</text>
            </g>
          );
        })}
      </svg>
      {hover !== null && (
        <div className="tip" style={{ left: `${(hx / W) * 100}%`, top: 10 }}>
          <div className="h">ring {rings[hover]} · $ per score</div>
          {series.map((s) => {
            const p = s.pts.find((q) => q.ring === rings[hover]);
            return p ? (
              <div className="r" key={s.c.chain}>
                <span><svg width={12} height={12}><Marker shape={s.shape} x={6} y={6} color={s.c.color || '#fff'} /></svg>{s.c.title}</span>
                <b className="mono">{usd(p.usd)}</b>
              </div>
            ) : null;
          })}
        </div>
      )}
    </div>
  );
}

const CAPS: [string, string][] = [['limit_tgas', 'tgas'], ['cap_cu', 'cu_max'], ['cap', 'gas']];

/* No measured sweep (Starknet): fall back to the priced per-ring estimate. */
function Estimate({ m, chain }: { m: any; chain: Chain }) {
  const rows = (m?.by_ring || []) as any[];
  if (!rows.length) return <div className="empty sm">No ring sweep recorded for {chain.title} yet — run an epoch to measure it.</div>;
  const top = Math.max(...rows.map((r) => r.usd || 0)) || 1;
  return (
    <table className="t">
      <thead>
        <tr><th className="num" style={{ width: 70 }}>Ring</th><th className="num">{m.symbol || 'native'} / score</th><th className="num">$ / score</th><th style={{ width: '34%' }}>relative</th></tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.ring}>
            <td className="num">{r.ring}</td>
            <td className="num">{fmt(r.native, 4)}</td>
            <td className="num">{usd(r.usd)}{r.est ? <span className="muted tiny"> est.</span> : null}</td>
            <td>
              <div style={{ height: 8, borderRadius: 4, background: 'rgba(255,255,255,0.06)' }}>
                <div style={{ width: `${(r.usd / top) * 100}%`, height: '100%', borderRadius: '0 4px 4px 0', background: 'rgb(var(--c-rgb))' }} />
              </div>
            </td>
          </tr>
        ))}
        <tr><td colSpan={4} className="muted tiny">No hard per-tx ceiling measured on {chain.title}{m.max_ring ? `; max ring ${m.max_ring}` : ''}. Estimated from the last epoch&apos;s steps, scaled by ring size.</td></tr>
      </tbody>
    </table>
  );
}

function Limits({ table, chain, m }: { table: any; chain: Chain; m?: any }) {
  if (!table?.rows?.length) return <Estimate m={m} chain={chain} />;
  const capPair = CAPS.find(([c]) => typeof table[c] === 'number');
  const cols = Array.from(new Set(table.rows.flatMap((r: any) => Object.keys(r)))).filter((k) => k !== 'ring' && k !== 'error') as string[];
  return (
    <table className="t">
      <thead>
        <tr><th className="num" style={{ width: 70 }}>Ring</th>{cols.map((c) => <th key={c} className="num">{c}</th>)}{capPair && <th style={{ width: '30%' }}>vs limit {fmt(table[capPair[0]])}</th>}</tr>
      </thead>
      <tbody>
        {table.rows.map((r: any) => {
          const load = capPair && typeof r[capPair[1]] === 'number' ? r[capPair[1]] / table[capPair[0]] : null;
          return (
            <tr key={r.ring}>
              <td className="num">{r.ring}</td>
              {r.error ? <td colSpan={cols.length + 1} style={{ color: 'var(--bad)' }}>{r.error}</td> : <>
                {cols.map((c) => <td key={c} className="num">{typeof r[c] === 'number' ? fmt(r[c], 1) : r[c] ?? '—'}</td>)}
                {capPair && (
                  <td>
                    {load !== null && (
                      <div className="row" style={{ gap: 8, flexWrap: 'nowrap' }}>
                        <div style={{ flex: 1, height: 8, borderRadius: 4, background: 'rgba(255,255,255,0.06)' }}>
                          <div style={{ width: `${Math.min(100, load * 100)}%`, height: '100%', borderRadius: '0 4px 4px 0', background: load > 0.9 ? 'var(--warn)' : 'rgb(var(--c-rgb))' }} />
                        </div>
                        <span className="mono tiny">{(load * 100).toFixed(0)}%</span>
                      </div>
                    )}
                  </td>
                )}
              </>}
            </tr>
          );
        })}
        {table.max_ring && <tr><td colSpan={cols.length + 2} className="muted tiny">max ring on {chain.title}: {table.max_ring}</td></tr>}
      </tbody>
    </table>
  );
}

export default function Costs({ chains, chain, eco, costs, onPick }: { chains: Chain[]; chain?: Chain; eco: any; costs: any; onPick?: (chain: string) => void }) {
  const order = ['submit', 'score', 'settle', 'claim', 'register'];
  const scores = chains.map((c) => eco?.chains?.[c.chain]?.actions?.score?.usd).filter((v): v is number => v > 0);
  const cheapest = scores.length ? Math.min(...scores) : 0;
  return (
    <div className="stack fade">
      <Experiment chain={chain} />
      <div className="card">
        <h3><span className="n">$</span> {chain?.title ?? 'Mainnet'} cost per action</h3>
        <p className="lede">
          Live gas and token price applied to what each verifier actually burned on its local chain.
          Epochs are at least {eco?.min_epoch_s ? `${eco.min_epoch_s / 3600}h` : '1h'}.
          {eco?.price_error ? <span style={{ color: 'var(--warn)' }}> price feed: {eco.price_error}</span> : null}
        </p>
        <div className="eco">
          {chains.filter((c) => c.chain === chain?.chain).map((c) => {
            const m = eco?.chains?.[c.chain];
            const on = c.chain === chain?.chain;
            const s = m?.actions?.score?.usd;
            const best = cheapest > 0 && s > 0 && s <= cheapest;
            const acts = order.filter((k) => m?.actions?.[k]);
            const top = Math.max(0, ...acts.map((k) => m.actions[k].usd || 0)) || 1;
            return (
              <button key={c.chain} type="button" className={`ecotile${on ? ' on' : ''}`}
                style={{ ['--tc' as any]: c.color || '#fff' }}
                onClick={() => onPick?.(c.chain)} title={`Show ${c.title} limits below`}>
                <div className="hd">
                  <span className="nm"><i />{c.title}</span>
                  {m && <span className="px">{m.symbol} {usd(m.usd_price)}</span>}
                </div>
                {!m ? <div className="muted tiny" style={{ marginTop: 10 }}>no price yet</div> : <>
                  <div className="v">{usd(s)}<small> / score</small></div>
                  {cheapest > 0 && s > 0 && (
                    <div className={`rel${best ? ' best' : ''}`}>
                      {best ? 'cheapest chain' : `${(s / cheapest).toFixed(s / cheapest < 10 ? 1 : 0)}x the cheapest`}
                    </div>
                  )}
                  <div className="acts">
                    {acts.map((k) => (
                      <div key={k} className="act">
                        <span>{k}</span>
                        <span className="bar"><b style={{ width: `${Math.max(3, (m.actions[k].usd / top) * 100)}%` }} /></span>
                        <span className="mono">{usd(m.actions[k].usd)}</span>
                      </div>
                    ))}
                  </div>
                  <div className="s">
                    {m.block_seconds ? `${m.block_seconds.toFixed(2)}s blocks` : ''}{m.actions_per_hour ? ` · ${fmt(m.actions_per_hour)} actions/h` : ''}
                  </div>
                </>}
              </button>
            );
          })}
        </div>
      </div>
      <div className="card">
        <h3><span className="n">RING</span> Price of anonymity</h3>
        <p className="lede">Dollars per score as the ring grows: a bigger set hides you better and costs linearly more. Log scale.</p>
        <RingChart chains={chains.filter((c) => c.chain === chain?.chain)} eco={eco} sel={chain?.chain} />
      </div>
      <Forecast chain={chain} />
      {chain && (
        <div className="card">
          <h3><span className="n">LIMIT</span> {chain.title} in its own units</h3>
          <p className="lede">One verification against the chain&apos;s hard per-transaction ceiling.{eco?.chains?.[chain.chain]?.note ? ` ${eco.chains[chain.chain].note}.` : ''}</p>
          <Limits table={costs?.[chain.chain]} chain={chain} m={eco?.chains?.[chain.chain]} />
        </div>
      )}
    </div>
  );
}
