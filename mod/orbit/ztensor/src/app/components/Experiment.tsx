'use client';
/* EXPERIMENT: one network, swept over epoch length x miners x validators
 * (GET experiment, core/experiment.py). $ per epoch does not depend on the
 * epoch length, the blockspace an epoch has does — so the largest subnet that
 * fits grows with the epoch. Monte Carlo over gas price and turnout gives the
 * p5/p50/p95 cost of each cell. */
import { Fragment, useEffect, useRef, useState } from 'react';
import type { Chain } from '@/lib/api';
import { get } from '@/lib/api';
import { money } from './Forecast';

const CEIL = 1 << 22;
const STRATS: [string, string][] = [['baseline', 'Today'], ['batch', 'Batch scores'], ['lazy', '+ Lazy claims'], ['proof', '+ One proof'], ['committee', 'Committees'], ['private', '+ Private proof']];
const SHARES = [0.01, 0.1, 0.25, 1];
const VOLS: [number, string][] = [[0.2, 'calm'], [0.5, 'normal'], [1, 'wild']];
const RINGS = [4, 8, 16, 32, 64];

const count = (n?: number) =>
  n === undefined || n === null ? '—' : n >= CEIL ? 'no limit' : n === 0 ? 'none'
    : n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e4 ? `${(n / 1e3).toFixed(0)}k` : n.toLocaleString();
const pct = (s?: number | null) =>
  s === undefined || s === null ? '—' : s >= 10 ? `${s.toFixed(0)}x` : s >= 1 ? `${s.toFixed(1)}x` : `${(s * 100).toFixed(s < 0.01 ? 2 : 1)}%`;

function useWidth(min = 320) {
  const box = useRef<HTMLDivElement>(null);
  const [W, setW] = useState(720);
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setW(Math.max(min, Math.round(e.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, [min]);
  return [box, W] as const;
}

/* Largest miners set vs validators, one line per epoch length (sequential:
 * one hue, light = short epoch, dark = long). Log-log. */
function Frontier({ x: d, H, setH }: { x: any; H: number; setH: (h: number) => void }) {
  const [box, W] = useWidth();
  const [hover, setHover] = useState<number | null>(null);
  const Vs = d.validators as number[];
  const eps = d.epochs as any[];
  const vals = eps.flatMap((e) => e.max_miners.map((m: any) => m.miners)).filter((v: number) => v > 0 && v < CEIL);
  if (!vals.length) return <div className="empty sm" ref={box}>No subnet size fits these limits on any epoch length.</div>;
  const Ht = 270, L = 58, R = 58, T = 14, B = 34;
  const lo = Math.pow(10, Math.floor(Math.log10(Math.min(...vals, 4))));
  const hi = Math.pow(10, Math.ceil(Math.log10(Math.max(...vals, 256))));
  const x = (v: number) => L + (Math.log2(v) - Math.log2(Vs[0])) / Math.max(1e-9, Math.log2(Vs[Vs.length - 1]) - Math.log2(Vs[0])) * (W - L - R);
  const y = (v: number) => T + (1 - (Math.log10(Math.max(v, lo)) - Math.log10(lo)) / (Math.log10(hi) - Math.log10(lo))) * (Ht - T - B);
  const ticks: number[] = [];
  for (let v = lo; v <= hi * 1.001; v *= 10) ticks.push(v);
  const op = (i: number) => 0.32 + 0.68 * (i / Math.max(1, eps.length - 1));
  const labY = new Map<number, number>();
  eps.map((e) => ({ h: e.hours, v: y(Math.max(lo, Math.min(hi, e.max_miners[e.max_miners.length - 1].miners))) + 4 }))
    .sort((a, b) => a.v - b.v)
    .reduce((p, l) => { const v = Math.max(l.v, p + 13); labY.set(l.h, v); return v; }, -Infinity);
  const bx = x(64), by = y(256);
  return (
    <div className="chart" ref={box}>
      <svg viewBox={`0 0 ${W} ${Ht}`} width={W} height={Ht}
        onMouseMove={(e) => {
          const r = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
          const px = ((e.clientX - r.left) / r.width) * W;
          let best = 0;
          Vs.forEach((v, i) => { if (Math.abs(x(v) - px) < Math.abs(x(Vs[best]) - px)) best = i; });
          setHover(best);
        }}
        onMouseLeave={() => setHover(null)}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={L} x2={W - R} y1={y(t)} y2={y(t)} stroke="rgba(255,255,255,0.06)" />
            <text x={L - 8} y={y(t) + 4} textAnchor="end" fontSize="10.5" fill="var(--ink3)" fontFamily="var(--mono)">{count(t)}</text>
          </g>
        ))}
        {Vs.map((v) => <text key={v} x={x(v)} y={Ht - 14} textAnchor="middle" fontSize="10.5" fill="var(--ink3)" fontFamily="var(--mono)">{v}</text>)}
        <text x={(L + W - R) / 2} y={Ht - 1} textAnchor="middle" fontSize="10" fill="var(--ink3)">validators</text>
        <text x={12} y={T + 2} fontSize="10" fill="var(--ink3)" transform={`rotate(90 12 ${T + 2})`}>max miners</text>
        {hover !== null && <line x1={x(Vs[hover])} x2={x(Vs[hover])} y1={T} y2={Ht - B} stroke="rgba(255,255,255,0.18)" />}
        {eps.map((e, i) => {
          const pts = (e.max_miners as any[]).filter((m) => m.miners > 0);
          if (!pts.length) return null;
          const path = pts.map((m, j) => `${j ? 'L' : 'M'}${x(m.validators)} ${y(Math.min(m.miners, hi))}`).join('');
          const on = e.hours === H;
          return (
            <g key={e.hours} opacity={op(i)} style={{ cursor: 'pointer' }} onClick={() => setH(e.hours)}>
              <path d={path} fill="none" stroke="rgb(var(--c-rgb))" strokeWidth={on ? 3 : 1.6} strokeLinejoin="round" />
              {on && pts.map((m) => <circle key={m.validators} cx={x(m.validators)} cy={y(Math.min(m.miners, hi))} r={4} fill="rgb(var(--c-rgb))" stroke="var(--bg2)" strokeWidth={2} />)}
              <text x={W - R + 8} y={labY.get(e.hours)} fontSize="11" fontWeight={on ? 750 : 550} fill={on ? 'var(--ink)' : 'var(--ink3)'} fontFamily="var(--mono)">{e.hours}h</text>
            </g>
          );
        })}
        {/* one Bittensor subnet for scale */}
        <g>
          <rect x={bx - 5} y={by - 5} width={10} height={10} transform={`rotate(45 ${bx} ${by})`} fill="none" stroke="var(--ink)" strokeWidth={1.5} />
          <text x={bx + 9} y={by - 8} fontSize="10.5" fill="var(--ink2)">Bittensor subnet (256 × 64)</text>
        </g>
      </svg>
      {hover !== null && (
        <div className="tip" style={{ left: `${(x(Vs[hover]) / W) * 100}%`, top: 8 }}>
          <div className="h">{Vs[hover]} validators · largest miner set</div>
          {eps.map((e) => (
            <div className="r" key={e.hours}>
              <span style={{ fontWeight: e.hours === H ? 700 : 500 }}>{e.hours}h epoch</span>
              <b className="mono">{count(e.max_miners[hover].miners)}</b>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/* miners x validators for the chosen epoch: $ per epoch (median of the
 * simulation) in each cell, tint = share of the chain's blockspace, hatched =
 * over the limit. */
function Heat({ x: d, ep }: { x: any; ep: any }) {
  const [hov, setHov] = useState<any>(null);
  const Ms = d.miners as number[];
  const Vs = d.validators as number[];
  const at = new Map<string, any>((ep.cells as any[]).map((c) => [`${c.miners}:${c.validators}`, c]));
  const shades = (ep.cells as any[]).map((c) => c.share).filter((s: any) => s > 0);
  const smin = Math.log10(Math.max(1e-6, Math.min(...shades, 1)));
  const tint = (c: any) => {
    if (!c.fits) return undefined;
    if (!(c.share > 0)) return 'rgba(var(--c-rgb), 0.10)';
    const t = (Math.log10(c.share) - smin) / Math.max(1e-9, Math.log10(d.share) - smin);
    return `rgba(var(--c-rgb), ${(0.08 + 0.55 * Math.max(0, Math.min(1, t))).toFixed(3)})`;
  };
  return (
    <div className="xheat">
      <div className="xgrid" style={{ gridTemplateColumns: `64px repeat(${Ms.length}, minmax(52px, 1fr))` }}>
        <div className="corner">V \ M</div>
        {Ms.map((m) => <div key={m} className="ch">{m}</div>)}
        {Vs.map((v) => (
          <Fragment key={v}>
            <div className="rh">{v}</div>
            {Ms.map((m) => {
              const c = at.get(`${m}:${v}`);
              const bt = m === 256 && v === 64;
              return (
                <div key={`${m}:${v}`} className={`cell${c.fits ? '' : ' over'}${bt ? ' bt' : ''}`}
                  style={{ background: tint(c) }}
                  onMouseEnter={() => setHov(c)} onMouseLeave={() => setHov(null)}>
                  {money(c.p50)}
                </div>
              );
            })}
          </Fragment>
        ))}
      </div>
      <div className="xhint">
        {hov ? (
          <span>
            <b>{hov.miners} miners × {hov.validators} validators</b> · {money(hov.p50)} / epoch
            <span className="muted"> (p5 {money(hov.p5)} – p95 {money(hov.p95)})</span> · {money(hov.usd_day)} / day ·
            {hov.share !== null ? ` ${pct(hov.share)} of the chain` : ' blockspace unknown'} ·{' '}
            <b style={{ color: hov.fits ? 'var(--good)' : 'var(--bad)' }}>{hov.fits ? 'fits' : 'does not fit'}</b>
          </span>
        ) : (
          <span className="muted">Hover a cell. Darker = more of the chain&apos;s blockspace. Striped = over your limit. Outlined = one Bittensor subnet.</span>
        )}
      </div>
    </div>
  );
}

export default function Experiment({ chain }: { chain?: Chain }) {
  const [p, setP] = useState({ strategy: 'baseline', share: 0.1, ring: 16, vol: 0.5, budget: '' });
  const [H, setH] = useState(1);
  const [d, setD] = useState<any>(null);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!chain) return;
    let live = true;
    setBusy(true);
    const t = setTimeout(() => {
      get('experiment', { chain: chain.chain, strategy: p.strategy, share: p.share, ring: p.ring, vol: p.vol,
        ...(Number(p.budget) > 0 ? { budget_day: Number(p.budget) } : {}) })
        .then((r) => { if (!live) return; if (r?.ok === false) { setD(null); setErr(r.error || 'no data'); } else { setD(r); setErr(''); } })
        .catch((e) => live && setErr(String(e.message || e)))
        .finally(() => live && setBusy(false));
    }, 250);
    return () => { live = false; clearTimeout(t); };
  }, [chain?.chain, p]);
  if (!chain) return null;
  const ep = d?.epochs?.find((e: any) => e.hours === H) || d?.epochs?.[0];
  const mm64 = ep?.max_miners?.find((m: any) => m.validators === 64)?.miners;
  const mv256 = ep?.max_validators?.find((m: any) => m.miners === 256)?.validators;
  const bt = ep?.bittensor;
  const btCell = ep?.cells?.find((c: any) => c.miners === 256 && c.validators === 64);
  return (
    <div className="card hi">
      <h3><span className="n">MAX</span> How big can a {chain.title} subnet get?</h3>
      <p className="lede">
        Every epoch length × miners × validators, priced at today&apos;s {chain.title} mainnet gas. An epoch costs the same
        whatever its length; a longer epoch just has more blocks, so a bigger subnet fits.
        Each cell is simulated {d?.trials ?? 200} times with random gas prices and turnout.
      </p>
      <div className="xctl">
        <div><span>epoch</span>
          <div className="seg">{(d?.hours || [1, 2, 4, 6, 12, 24]).map((h: number) => <button key={h} type="button" className={H === h ? 'on' : ''} onClick={() => setH(h)}>{h}h</button>)}</div>
        </div>
        <div><span>contract</span>
          <div className="seg">{STRATS.map(([k, n]) => <button key={k} type="button" className={p.strategy === k ? 'on' : ''} onClick={() => setP({ ...p, strategy: k })}>{n}</button>)}</div>
        </div>
        <div><span>max share of chain</span>
          <div className="seg">{SHARES.map((s) => <button key={s} type="button" className={p.share === s ? 'on' : ''} onClick={() => setP({ ...p, share: s })}>{s * 100}%</button>)}</div>
        </div>
        <div><span>ring</span>
          <div className="seg">{RINGS.filter((r) => r <= (chain.max_ring || 64)).map((r) => <button key={r} type="button" className={p.ring === r ? 'on' : ''} onClick={() => setP({ ...p, ring: r })}>{r}</button>)}</div>
        </div>
        <div><span>gas swings</span>
          <div className="seg">{VOLS.map(([v, n]) => <button key={v} type="button" className={p.vol === v ? 'on' : ''} onClick={() => setP({ ...p, vol: v })}>{n}</button>)}</div>
        </div>
        <div><span>budget $ / day</span>
          <input type="number" min={0} placeholder="none" value={p.budget} style={{ width: 96 }}
            onChange={(e) => setP({ ...p, budget: e.target.value })} />
        </div>
      </div>
      {err && <div className="empty sm">{err}</div>}
      {!d && !err && <div className="empty sm">{busy ? 'running the sweep…' : '—'}</div>}
      {d && ep && <div style={{ opacity: busy ? 0.6 : 1, transition: 'opacity .2s' }}>
        <div className="xstats">
          <div className="xstat"><div className="k">Max subnet</div><div className="v">{count(ep.max_square)}<small> × {count(ep.max_square)}</small></div><div className="s">miners = validators, {H}h epoch</div></div>
          <div className="xstat"><div className="k">Max miners</div><div className="v">{count(mm64)}</div><div className="s">with 64 validators</div></div>
          <div className="xstat"><div className="k">Max validators</div><div className="v">{count(mv256)}</div><div className="s">with 256 miners</div></div>
          <div className="xstat"><div className="k">Bittensor size / epoch</div><div className="v">{money(btCell?.p50 ?? bt?.usd)}</div>
            <div className="s">{money(bt?.usd_day)} / day · <b style={{ color: bt?.fits ? 'var(--good)' : 'var(--bad)' }}>{bt?.fits ? 'fits' : 'does not fit'}</b></div></div>
        </div>
        {d.privacy && !d.privacy.kept && <p className="tiny" style={{ color: 'var(--warn)' }}>This contract links actions today&apos;s keeps apart: {d.privacy.note}. Committees and Private proof save as much without that.</p>}
        {!d.blockspace_known && <p className="tiny" style={{ color: 'var(--warn)' }}>{chain.title} reports no block capacity, so only the $ budget limits the size.</p>}
        <h4 className="xh">Largest miner set by validators · one line per epoch length</h4>
        <Frontier x={d} H={H} setH={setH} />
        <h4 className="xh">$ per epoch · {H}h epoch · median of {d.trials} runs</h4>
        <Heat x={d} ep={ep} />
        <h4 className="xh">Every epoch length</h4>
        <div className="tscroll">
          <table className="t">
            <thead>
              <tr><th className="num">epoch</th><th className="num">blocks</th><th className="num">max M = V</th><th className="num">max miners · 64 V</th><th className="num">max V · 256 M</th><th className="num">Bittensor $ / epoch</th><th className="num">$ / day</th><th className="num">uses</th></tr>
            </thead>
            <tbody>
              {d.epochs.map((e: any) => {
                const c = e.cells.find((q: any) => q.miners === 256 && q.validators === 64);
                return (
                  <tr key={e.hours} className={e.hours === H ? 'on' : ''} style={{ cursor: 'pointer' }} onClick={() => setH(e.hours)}>
                    <td className="num"><b>{e.hours}h</b></td>
                    <td className="num">{e.blocks ? Math.round(e.blocks).toLocaleString() : '—'}</td>
                    <td className="num">{count(e.max_square)}</td>
                    <td className="num">{count(e.max_miners.find((m: any) => m.validators === 64)?.miners)}</td>
                    <td className="num">{count(e.max_validators.find((m: any) => m.miners === 256)?.validators)}</td>
                    <td className="num">{money(c?.p50)} <span className="muted tiny">p95 {money(c?.p95)}</span></td>
                    <td className="num">{money(e.bittensor.usd_day)}</td>
                    <td className="num" style={{ color: c?.fits ? undefined : 'var(--bad)' }}>{pct(c?.share)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <p className="tiny muted" style={{ margin: '12px 0 0' }}>
          Max = largest set where one epoch fits in {d.share * 100}% of the chain&apos;s blocks{d.budget_day ? ` and costs ≤ $${d.budget_day}/day` : ''}, every member acting.
          Capacity {d.cap_per_hour ? `${Number(d.cap_per_hour.toPrecision(3)).toLocaleString()} ${d.capacity_unit}/h` : 'unknown'}{d.capacity_estimated ? ' (estimate)' : ''}; ring {d.ring}.
          Simulation: gas × token price lognormal (σ {d.vol}), each member shows up with probability {d.p_min}–1.
          {d.modeled ? ' This contract is modeled, not deployed: estimates.' : ' Today’s contract, gas measured on the local chain.'}
        </p>
      </div>}
    </div>
  );
}
