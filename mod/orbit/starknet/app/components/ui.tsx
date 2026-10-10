'use client';
import Link from 'next/link';
import { useState } from 'react';
import { nameOf, short } from '@/lib/format';

export function Card({ title, right, children, className = '' }: {
  title?: React.ReactNode; right?: React.ReactNode; children: React.ReactNode; className?: string;
}) {
  return (
    <section className={`card ${className}`}>
      {(title || right) && (
        <div className="card-head">
          {title && <h2>{title}</h2>}
          {right && <div className="card-right">{right}</div>}
        </div>
      )}
      {children}
    </section>
  );
}

export function Stat({ label, value, sub }: { label: string; value: React.ReactNode; sub?: React.ReactNode }) {
  return (
    <div className="stat">
      <div className="stat-l">{label}</div>
      <div className="stat-v">{value}</div>
      {sub && <div className="stat-s">{sub}</div>}
    </div>
  );
}

export function Rows({ rows }: { rows: [React.ReactNode, React.ReactNode][] }) {
  return (
    <dl className="rows">
      {rows.map(([k, v], i) => (
        <div className="row" key={i}><dt>{k}</dt><dd>{v}</dd></div>
      ))}
    </dl>
  );
}

export function Copy({ text, label = 'copy' }: { text: string; label?: string }) {
  const [done, setDone] = useState(false);
  return (
    <button className="copy" title="copy" onClick={e => {
      e.preventDefault(); e.stopPropagation();
      navigator.clipboard?.writeText(text).then(() => { setDone(true); setTimeout(() => setDone(false), 1100); });
    }}>{done ? 'copied' : label}</button>
  );
}

/** An address: known name if we have one, else a short hash; links to its page. */
export function Addr({ a, full = false, link = true }: { a: string; full?: boolean; link?: boolean }) {
  if (!a) return <span className="dim">—</span>;
  const name = nameOf(a);
  const text = name || (full ? a : short(a));
  const inner = <span className={`mono ${name ? 'named' : ''}`} title={a}>{text}</span>;
  return link ? <Link href={`/address?a=${a}`} className="hash">{inner}</Link> : inner;
}

export function TxLink({ h, full = false }: { h: string; full?: boolean }) {
  return <Link href={`/tx?h=${h}`} className="hash mono" title={h}>{full ? h : short(h)}</Link>;
}

export function BlockLink({ n }: { n: number | string }) {
  return <Link href={`/block?n=${n}`} className="hash mono">#{Number(n).toLocaleString()}</Link>;
}

export function Badge({ tone = 'dim', children }: { tone?: 'ok' | 'err' | 'warn' | 'dim' | 'accent'; children: React.ReactNode }) {
  return <span className={`badge ${tone}`}>{children}</span>;
}

export function Loading({ what = 'Loading' }: { what?: string }) {
  return <div className="loading"><span className="spin" />{what}…</div>;
}

export function ErrorBox({ error, onRetry }: { error: string; onRetry?: () => void }) {
  return (
    <div className="error">
      <span>{error}</span>
      {onRetry && <button className="btn ghost sm" onClick={onRetry}>try again</button>}
    </div>
  );
}

export function Raw({ data, label = 'Show raw JSON' }: { data: any; label?: string }) {
  const [open, setOpen] = useState(false);
  const text = JSON.stringify(data, null, 2);
  return (
    <div className="raw">
      <button className="btn ghost sm" onClick={() => setOpen(!open)}>{open ? 'Hide raw JSON' : label}</button>
      {open && <div className="raw-box"><Copy text={text} /><pre>{text}</pre></div>}
    </div>
  );
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <div className="empty">{children}</div>;
}
