'use client';
/* Small shared pieces — every page is assembled from these. No UI library:
 * the whole look is app/globals.css. */
import { ReactNode, useState } from 'react';
import { RANGES } from '@/lib/format';
import { useCopy } from '@/lib/hooks';

export function Section({ id, title, lead, narrow, children }:
  { id: string; title: string; lead?: ReactNode; narrow?: boolean; children: ReactNode }) {
  return (
    <section id={id} className={`page${narrow ? ' narrow' : ''}`}>
      <h2>{title}</h2>
      {lead && <p className="lead">{lead}</p>}
      {children}
    </section>
  );
}

export const Spinner = () => <span className="spinner" aria-label="loading" />;

export const Muted = ({ children, style }: { children: ReactNode; style?: React.CSSProperties }) =>
  <span className="muted" style={style}>{children}</span>;

export function Pct({ v }: { v?: number | null }) {
  if (v == null || !isFinite(v)) return <span className="muted">—</span>;
  return <span className={v >= 0 ? 'up' : 'down'}>{v >= 0 ? '+' : '−'}{Math.abs(v).toFixed(1)}%</span>;
}

export function Spark({ pts, dir, w = 110, h = 30 }: { pts?: number[] | null; dir?: number | null; w?: number; h?: number }) {
  if (!pts || pts.length < 2) return <span className="muted" style={{ fontSize: 11 }}>soon</span>;
  const min = Math.min(...pts), max = Math.max(...pts), pad = 2, span = max - min || 1;
  const X = (i: number) => pad + i * (w - 2 * pad) / (pts.length - 1);
  const Y = (v: number) => h - pad - (v - min) * (h - 2 * pad) / span;
  const d = pts.map((v, i) => `${i ? 'L' : 'M'}${X(i).toFixed(1)},${Y(v).toFixed(1)}`).join('');
  const cls = dir == null ? 'flat' : dir >= 0 ? 'up' : 'down';
  return (
    <svg className={`spark ${cls}`} width={w} height={h} viewBox={`0 0 ${w} ${h}`} aria-hidden>
      <path className="l" d={d} />
    </svg>
  );
}

/* a deterministic face for an address — FNV hash of the ss58, no network */
export function Ident({ addr, size = 22 }: { addr?: string | null; size?: number }) {
  let h = 2166136261;
  for (const c of String(addr || '')) h = Math.imul(h ^ c.charCodeAt(0), 16777619) >>> 0;
  const c1 = `hsl(${h % 360},72%,58%)`, c2 = `hsl(${(h >>> 9) % 360},70%,44%)`, c3 = `hsl(${(h >>> 18) % 360},85%,68%)`;
  const gid = 'idg' + h.toString(36) + size;
  return (
    <svg className="ident" width={size} height={size} viewBox="0 0 24 24" aria-hidden>
      <defs><linearGradient id={gid} x1="0" y1="0" x2="1" y2="1">
        <stop offset="0" stopColor={c1} /><stop offset="1" stopColor={c2} /></linearGradient></defs>
      <rect x="1" y="1" width="22" height="22" rx="6" fill={`url(#${gid})`} stroke="rgba(0,0,20,.4)" strokeWidth="1.5" />
      <circle cx={7 + (h % 9)} cy={7 + ((h >>> 5) % 9)} r="3.4" fill={c3} />
      <circle cx={17 - ((h >>> 11) % 8)} cy={16 - ((h >>> 7) % 7)} r="2" fill="rgba(255,255,255,.8)" />
    </svg>
  );
}

export function SubnetLogo({ logo, symbol, big }: { logo?: string | null; symbol?: string; big?: boolean }) {
  const [bad, setBad] = useState(false);
  const cls = big ? ' big' : '';
  if (logo && !bad)
    // eslint-disable-next-line @next/next/no-img-element
    return <img className={'sn-logo' + cls} src={logo} alt="" loading="lazy" referrerPolicy="no-referrer"
                onError={() => setBad(true)} />;
  return <span className={'sn-avatar' + cls}>{(symbol || 'τ').slice(0, 2)}</span>;
}

export function CopyBlock({ text }: { text: string }) {
  const [done, copy] = useCopy();
  return (
    <div className="copyblock">
      <button className="copybtn" onClick={() => copy(text)}>{done ? 'Copied' : 'Copy'}</button>
      <code>{text}</code>
    </div>
  );
}

export function Ranges({ sel, onSel }: { sel: number; onSel: (i: number) => void }) {
  return (
    <div className="ranges">
      {RANGES.map((r, i) => (
        <button key={r.label} className={i === sel ? 'sel' : ''} onClick={() => onSel(i)}>{r.label}</button>
      ))}
    </div>
  );
}

export function Cells({ items }: { items: [string, ReactNode][] }) {
  return (
    <div className="poolgrid">
      {items.map(([k, v]) => <div className="cell" key={k}><span>{k}</span><b>{v}</b></div>)}
    </div>
  );
}

export function Stat({ label, value, left }: { label: string; value: ReactNode; left?: boolean }) {
  return <div className={'stat' + (left ? ' left' : '')}><b>{value}</b><span>{label}</span></div>;
}

export function SideTag({ side }: { side: string }) {
  return <span className={`tag ${side === 'sell' ? 'warn' : ''}`}>{side}</span>;
}

export function Tabs<T extends string>({ value, options, onChange }:
  { value: T; options: [T, string][]; onChange: (v: T) => void }) {
  return (
    <div className="seg">
      {options.map(([v, l]) => (
        <button key={v} className={v === value ? 'on' : ''} onClick={() => onChange(v)}>{l}</button>
      ))}
    </div>
  );
}
