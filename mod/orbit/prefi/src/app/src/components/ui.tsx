'use client'

/**
 * The small set of primitives every panel is built from. Keeping them here
 * means the pool, the predictor and the market list all share one look —
 * change a table header once, it changes everywhere.
 */

import { ReactNode } from 'react'
import { hue } from '@/lib/fmt'

export function Section({ title, count, sub, action, children, className = '' }: {
  title: ReactNode; count?: number; sub?: ReactNode; action?: ReactNode; children: ReactNode; className?: string
}) {
  return (
    <div className={`card overflow-hidden ${className}`}>
      <div className="section-head">
        <div className="flex items-center gap-2.5 shrink-0">
          <h2 className="section-title whitespace-nowrap">{title}</h2>
          {count != null && <span className="count">{count}</span>}
        </div>
        {action ?? (sub && <span className="text-xs t3 text-right min-w-0">{sub}</span>)}
      </div>
      {children}
    </div>
  )
}

export function Stat({ label, value, sub, tone, className = '' }: {
  label: ReactNode; value: ReactNode; sub?: ReactNode; tone?: 'up' | 'down' | 'warn' | 'accent' | 'violet' | 'bad' | string; className?: string
}) {
  const color = tone === 'bad' ? 'down' : tone || ''
  return (
    <div className={`stat ${className}`}>
      <div className="stat-label">{label}</div>
      <div className={`stat-value ${color}`}>{value}</div>
      {sub && <div className="stat-sub">{sub}</div>}
    </div>
  )
}

export function Empty({ title, msg, children }: { title?: string; msg?: ReactNode; children?: ReactNode }) {
  return (
    <div className="empty">
      <div className="empty-glyph">
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.4">
          <circle cx="7" cy="7" r="5.5" strokeDasharray="3 2.5" />
        </svg>
      </div>
      {title && <div className="empty-title">{title}</div>}
      {msg && <div className="mt-1 max-w-sm mx-auto leading-relaxed">{msg}</div>}
      {children && <div className="mt-4">{children}</div>}
    </div>
  )
}

export function Avatar({ symbol, size = 'md' }: { symbol: string; size?: 'sm' | 'md' | 'lg' }) {
  const h = hue(symbol || '?')
  const letter = (symbol || '?').replace(/^SN/, '').replace(/^@/, '').replace(/^k/, '')[0]?.toUpperCase() || '?'
  return (
    <span className={`avatar ${size === 'sm' ? 'avatar-sm' : size === 'lg' ? 'avatar-lg' : ''}`}
      style={{ background: `linear-gradient(145deg, hsl(${h} 60% 40%), hsl(${(h + 40) % 360} 55% 22%))` }}>
      {letter}
    </span>
  )
}

/** Colored source tag: Hyperliquid = teal, Bittensor = violet, Base = pink. */
export function SourceTag({ m }: { m: any }) {
  if (m.source === 'hyperliquid')
    return <span className="tag tag-teal">HL {m.hl_kind === 'spot' ? 'spot' : 'perp'}</span>
  if (m.source === 'bittensor') return <span className="tag tag-violet">SN{m.bt_netuid}</span>
  if (m.source === 'dex')
    return (
      <span className={`tag ${m.chain === 'solana' ? 'tag-pink' : 'tag-blue'}`}
        title={m.eligible === false ? `under the liquidity floor` : m.dex ? `${m.dex} pool` : ''}>
        {m.chain === 'solana' ? 'Solana' : 'Base'}{m.eligible === false ? ' · thin' : ''}
      </span>
    )
  return <span className="tag tag-pink">Base</span>
}

export function Tabs<T extends string>({ tabs, value, onChange, size = 'md' }: {
  tabs: { id: T; label: ReactNode; count?: number }[]; value: T; onChange: (t: T) => void; size?: 'sm' | 'md'
}) {
  return (
    <div className="tabs">
      {tabs.map(t => (
        <button key={t.id} onClick={() => onChange(t.id)}
          className={`tab ${value === t.id ? 'active' : ''} ${size === 'sm' ? 'text-xs py-2 px-3' : ''}`}>
          {t.label}
          {t.count != null && t.count > 0 && <span className="count ml-2">{t.count}</span>}
        </button>
      ))}
    </div>
  )
}

export function Tag({ tone = 'neutral', children, title }: { tone?: string; children: ReactNode; title?: string }) {
  return <span className={`tag tag-${tone}`} title={title}>{children}</span>
}

export function Field({ label, value, tone }: { label: ReactNode; value: ReactNode; tone?: string }) {
  return (
    <div className="min-w-0">
      <div className="text-[11px] t3">{label}</div>
      <div className={`num text-sm truncate ${tone || ''}`}>{value}</div>
    </div>
  )
}

export function Label({ children, hint }: { children: ReactNode; hint?: ReactNode }) {
  return (
    <label className="label flex items-center justify-between">
      <span>{children}</span>
      {hint && <span className="normal-case tracking-normal font-normal t3">{hint}</span>}
    </label>
  )
}

export function Spinner() { return <span className="spinner" /> }
