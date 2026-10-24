'use client';
/* OVER TIME: what the selected chain costs per month as miners and validators
 * grow (GET forecast), for today's contract and each compression step. The
 * baseline is measured; batch/lazy/proof are modeled (core/forecast.py). */
import { useEffect, useRef, useState } from 'react';
import type { Chain } from '@/lib/api';
import { get } from '@/lib/api';

// fixed strategy colors + shapes (shape carries the series for CVD readers)
const ST: Record<string, { color: string; shape: 'circle' | 'square' | 'diamond' | 'triangle'; name: string }> = {
  baseline: { color: '#ff7a6b', shape: 'circle', name: 'Today' },
  batch: { color: '#f2c14e', shape: 'square', name: 'Batch scores' },
  lazy: { color: '#5cc8e8', shape: 'diamond', name: '+ Lazy claims' },
  proof: { color: '#6fdc9a', shape: 'triangle', name: '+ One proof' },
  committee: { color: '#b49cff', shape: 'square', name: 'Secret committees' },
  private: { color: '#6fdc9a', shape: 'triangle', name: '+ Private proof' },
};
const LADDERS: [string, string][] = [['private', 'keep privacy'], ['linking', 'link actions']];

export const money = (n?: number | null) =>
  n === undefined || n === null || Number.isNaN(n) ? '—'
    : n >= 1e9 ? `$${(n / 1e9).toFixed(2)}B`
    : n >= 1e6 ? `$${(n / 1e6).toFixed(2)}M`
    : n >= 1e4 ? `$${(n / 1e3).toFixed(1)}k`
    : n >= 100 ? `$${n.toFixed(0)}`
    : n >= 1 ? `$${n.toFixed(2)}`
    : n >= 0.01 ? `$${n.toFixed(3)}`
    : `$${n.toPrecision(2)}`;

function Mark({ shape, x, y, color }: { shape: string; x: number; y: number; color: string }) {
  const s = 4.5;
  const c = { fill: color, stroke: 'var(--bg2)', strokeWidth: 1.5 };
  if (shape === 'square') return <rect x={x - s} y={y - s} width={2 * s} height={2 * s} rx={1} {...c} />;
  if (shape === 'diamond') return <rect x={x - s} y={y - s} width={2 * s} height={2 * s} rx={1} transform={`rotate(45 ${x} ${y})`} {...c} />;
  if (shape === 'triangle') return <path d={`M${x} ${y - s - 1}L${x + s + 1} ${y + s}L${x - s - 1} ${y + s}Z`} {...c} />;
  return <circle cx={x} cy={y} r={s} {...c} />;
}

function Chart({ f }: { f: any }) {
  const box = useRef<HTMLDivElement>(null);
  const [W, setW] = useState(720);
  const [hover, setHover] = useState<number | null>(null);
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setW(Math.max(320, Math.round(e.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const rows = f.rows as any[];
  const sts = (f.shown || f.strategies) as string[];
  const all = rows.flatMap((r) => sts.map((s) => r[s].month)).filter((v) => v > 0);
  const H = 280, L = 62, R = 112, T = 14, B = 30;
  const lo = Math.pow(10, Math.floor(Math.log10(Math.min(...all))));
  const hi = Math.pow(10, Math.ceil(Math.log10(Math.max(...all))));
  const n = rows.length - 1 || 1;
  const x = (i: number) => L + (i / n) * (W - L - R);
  const y = (v: number) => T + (1 - (Math.log10(Math.max(v, lo)) - Math.log10(lo)) / (Math.log10(hi) - Math.log10(lo))) * (H - T - B);
  const ticks: number[] = [];
  for (let v = lo; v <= hi * 1.001; v *= 10) ticks.push(v);
  const labY = new Map<string, number>();
  sts.map((s) => ({ s, v: y(rows[rows.length - 1][s].month) + 4 })).sort((a, b) => a.v - b.v)
    .reduce((prev, l) => { const v = Math.max(l.v, prev + 14); labY.set(l.s, v); return v; }, -Infinity);
  const step = Math.max(1, Math.ceil(rows.length / 12));
  const h = hover !== null ? rows[hover] : null;
  return (
    <div className="chart" ref={box}>
      <svg viewBox={`0 0 ${W} ${H}`} width={W} height={H}
        onMouseMove={(e) => {
          const r = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
          const px = ((e.clientX - r.left) / r.width) * W;
          setHover(Math.max(0, Math.min(rows.length - 1, Math.round(((px - L) / (W - L - R)) * n))));
        }}
        onMouseLeave={() => setHover(null)}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={L} x2={W - R} y1={y(t)} y2={y(t)} stroke="rgba(255,255,255,0.06)" />
            <text x={L - 8} y={y(t) + 4} textAnchor="end" fontSize="10.5" fill="var(--ink3)" fontFamily="var(--mono)">{money(t)}</text>
          </g>
        ))}
        {rows.map((r, i) => i % step === 0 || i === rows.length - 1 ? (
          <text key={i} x={x(i)} y={H - 10} textAnchor="middle" fontSize="10.5" fill="var(--ink3)" fontFamily="var(--mono)">{r.month}</text>
        ) : null)}
        <text x={W - R} y={H - 10} dx={10} fontSize="10" fill="var(--ink3)">month</text>
        {hover !== null && <line x1={x(hover)} x2={x(hover)} y1={T} y2={H - B} stroke="rgba(255,255,255,0.18)" />}
        {sts.map((s) => {
          const c = ST[s] || ST.baseline;
          const d = rows.map((r, i) => `${i ? 'L' : 'M'}${x(i)} ${y(r[s].month)}`).join('');
          return (
            <g key={s}>
              <path d={d} fill="none" stroke={c.color} strokeWidth={2} strokeLinejoin="round" />
              {rows.map((r, i) => i % step === 0 || i === rows.length - 1
                ? <Mark key={i} shape={c.shape} x={x(i)} y={y(r[s].month)} color={c.color} /> : null)}
              <text x={W - R + 10} y={labY.get(s)} fontSize="11.5" fontWeight={650} fill="var(--ink2)">{c.name}</text>
            </g>
          );
        })}
      </svg>
      {h && (
        <div className="tip" style={{ left: `${(x(hover!) / W) * 100}%`, top: 8 }}>
          <div className="h">month {h.month} · {h.miners} miners · {h.validators} validators · $ / month</div>
          {sts.map((s) => (
            <div className="r" key={s}>
              <span><svg width={12} height={12}><Mark shape={ST[s].shape} x={6} y={6} color={ST[s].color} /></svg>{ST[s].name}</span>
              <b className="mono">{money(h[s].month)}</b>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

const SHAPES = ['exp', 'linear', 'logistic'];
const EPOCHS = [1, 4, 24];

export default function Forecast({ chain }: { chain?: Chain }) {
  const [p, setP] = useState({ m0: 16, m1: 256, v0: 4, v1: 64, months: 12, ring: 16, epoch_hours: 1, shape: 'exp' });
  const [raw, setF] = useState<any>(null);
  const [ladder, setLadder] = useState('private');
  const [err, setErr] = useState('');
  const f = raw ? { ...raw, shown: raw.ladders?.[ladder] || raw.strategies } : null;
  useEffect(() => {
    if (!chain) return;
    let live = true;
    const t = setTimeout(() => {
      get('forecast', { chain: chain.chain, miners: `${p.m0},${p.m1}`, validators: `${p.v0},${p.v1}`,
        months: p.months, ring: p.ring, epoch_hours: p.epoch_hours, shape: p.shape })
        .then((d) => { if (live) { setF(d?.chains?.[chain.chain] || null); setErr(''); } })
        .catch((e) => live && setErr(String(e.message || e)));
    }, 250);
    return () => { live = false; clearTimeout(t); };
  }, [chain?.chain, p]);
  if (!chain) return null;
  const num = (k: keyof typeof p, min: number, max: number) => (
    <input type="number" min={min} max={max} value={p[k] as number} style={{ width: 74 }}
      onChange={(e) => { const v = Math.max(min, Math.min(max, Number(e.target.value) || min)); setP({ ...p, [k]: v }); }} />
  );
  const last = f?.rows?.[f.rows.length - 1];
  return (
    <div className="card">
      <h3><span className="n">TIME</span> {chain.title} as the subnet grows</h3>
      <p className="lede">
        Monthly dollars at today&apos;s mainnet gas. Each epoch every validator scores every miner, so today&apos;s
        cost grows with miners × validators. The lines below that one show what each fix would save.
      </p>
      <div className="row" style={{ gap: 14, marginBottom: 14 }}>
        <label className="row tiny" style={{ gap: 6 }}>miners {num('m0', 1, 4096)} → {num('m1', 1, 4096)}</label>
        <label className="row tiny" style={{ gap: 6 }}>validators {num('v0', 1, 1024)} → {num('v1', 1, 1024)}</label>
        <label className="row tiny" style={{ gap: 6 }}>over {num('months', 1, 60)} months</label>
        <label className="row tiny" style={{ gap: 6 }}>ring {num('ring', 2, 64)}</label>
        <div className="seg" title="growth curve">
          {SHAPES.map((s) => <button key={s} type="button" className={p.shape === s ? 'on' : ''} onClick={() => setP({ ...p, shape: s })}>{s}</button>)}
        </div>
        <div className="seg" title="which ladder of fixes">
          {LADDERS.map(([k, n]) => <button key={k} type="button" className={ladder === k ? 'on' : ''} onClick={() => setLadder(k)}>{n}</button>)}
        </div>
        <div className="seg" title="epoch length">
          {EPOCHS.map((h) => <button key={h} type="button" className={p.epoch_hours === h ? 'on' : ''} onClick={() => setP({ ...p, epoch_hours: h })}>{h}h epoch</button>)}
        </div>
      </div>
      {err && <div className="err">{err}</div>}
      {!f ? <div className="empty sm">{err ? '' : `no priced run on ${chain.title} yet — run an epoch to measure it`}</div> : <>
        <Chart f={f} />
        <div className="tscroll" style={{ marginTop: 14 }}>
        <table className="t">
          <thead>
            <tr>
              <th>At month {last.month} ({last.miners} miners · {last.validators} validators · ring {last.ring})</th>
              <th className="num">$ / month</th><th className="num">{f.rows.length - 1 > 0 ? `total over ${f.rows.length - 1} mo` : 'total'}</th>
              <th className="num">cheaper</th><th className="num">miner / mo</th><th className="num">validator / mo</th><th className="num">blockspace</th>
            </tr>
          </thead>
          <tbody>
            {f.shown.map((s: string) => {
              const r = last[s];
              const bs = r.blockspace;
              return (
                <tr key={s}>
                  <td>
                    <span className="row" style={{ gap: 7, flexWrap: 'nowrap' }}>
                      <svg width={12} height={12}><Mark shape={ST[s].shape} x={6} y={6} color={ST[s].color} /></svg>
                      <b>{ST[s].name}</b><span className="muted tiny">{f.labels[s]}{s !== 'baseline' ? ' · modeled' : ' · measured'}</span>
                      {f.privacy?.[s] && <span className="tiny" title={f.privacy[s].note} style={{ color: f.privacy[s].kept ? 'var(--good)' : 'var(--warn)' }}>{f.privacy[s].kept ? 'private' : 'links'}</span>}
                    </span>
                  </td>
                  <td className="num">{money(r.month)}</td>
                  <td className="num">{money(r.cum)}</td>
                  <td className="num">{s === 'baseline' ? '—' : `${(f.savings[s] || 0).toFixed(f.savings[s] < 10 ? 1 : 0)}x`}</td>
                  <td className="num">{money(r.per_miner)}</td>
                  <td className="num">{money(r.per_validator)}</td>
                  <td className="num" style={bs > 1 ? { color: 'var(--bad)' } : bs > 0.25 ? { color: 'var(--warn)' } : undefined}
                    title={bs > 1 ? 'one epoch needs more than the whole chain carries in that time: does not fit' : 'share of the chain\'s capacity one epoch uses (approx.)'}>
                    {bs === null || bs === undefined ? '—' : bs > 1 ? `${bs.toFixed(1)}x full` : `${(bs * 100).toFixed(bs < 0.01 ? 2 : 1)}%`}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        </div>
{ladder === 'private' ? (
        <ol className="tiny muted" style={{ margin: '14px 0 0', paddingLeft: 18, lineHeight: 1.6 }}>
          <li><b style={{ color: 'var(--ink2)' }}>Secret committees.</b> A validator scores a miner only when the hash of its nullifier for that miner falls under {f.committee}/validators. Nobody, the validator included, can choose or predict it before the epoch, and anyone can check it. About {f.committee} scores per miner instead of every validator. Nothing new is revealed. The price is security: a coalition holding {Math.round(f.coalition * 100)}% of the validators wins a miner&apos;s committee with probability {f.capture < 1e-4 ? f.capture.toExponential(1) : f.capture.toFixed(4)}, or by abstaining leaves it without a quorum with probability {f.starve < 1e-4 ? f.starve.toExponential(1) : f.starve.toFixed(4)} (a retry committee can re-score it).</li>
          <li><b style={{ color: 'var(--ink2)' }}>Private proof.</b> The same ring signatures as today, checked off chain and settled with one validity proof per epoch (≈{money(f.units.proof)} to verify, estimated). Scores never have to be published, only each miner&apos;s result.</li>
          <li><b style={{ color: 'var(--ink2)' }}>Free levers.</b> Longer epochs divide the cost (try 4h/24h) and leak nothing. Pick the ring as the privacy floor you need: cost grows linearly with it.</li>
        </ol>
        ) : (
        <ol className="tiny muted" style={{ margin: '14px 0 0', paddingLeft: 18, lineHeight: 1.6 }}>
          <li><b style={{ color: 'var(--ink2)' }}>Batch scores.</b> A validator signs its whole score list once per epoch (like Bittensor&apos;s set_weights) instead of once per miner. Ring checks drop from validators × miners to validators. <span style={{ color: 'var(--warn)' }}>Privacy cost: one validator&apos;s scores are linked into a vector that can fingerprint it across epochs.</span></li>
          <li><b style={{ color: 'var(--ink2)' }}>Lazy claims.</b> A miner claims {f.K} epochs with one signature, so claims cost 1/{f.K} as much. <span style={{ color: 'var(--warn)' }}>Privacy cost: those {f.K} epochs are linked to one miner.</span></li>
          <li><b style={{ color: 'var(--ink2)' }}>One proof per epoch.</b> Every ring signature is checked off chain and one validity proof lands on chain (≈{money(f.units.proof)} to verify, estimated). It inherits the linkage of the steps above.</li>
        </ol>
        )}
        <p className="tiny muted" style={{ margin: '10px 0 0' }}>
          &quot;Today&quot; uses gas measured on the local chain. The other lines need contract changes and are estimates: a score entry is priced at ¼ of a settle, and proving at $0.0001 per million Cairo steps. Hover a private/links tag to see what an observer learns.
          The blockspace column compares one epoch with what the chain carries in that time; above 1x, the epoch does not fit.
        </p>
      </>}
    </div>
  );
}
