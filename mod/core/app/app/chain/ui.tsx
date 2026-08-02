"use client"

// Terminal-console primitives: hard 2px borders, offset shadows, mono type.
// Everything the chain tabs draw goes through these so the vibe stays one vibe.

import { CSSProperties, ReactNode } from 'react'
import { TERM_FONT, ACCENT } from './shared'

export const panelStyle: CSSProperties = {
  border: '2px solid var(--border-color)',
  background: 'var(--bg-secondary)',
}

export function Panel({ children, style }: { children: ReactNode; style?: CSSProperties }) {
  return <div style={{ ...panelStyle, padding: '16px', ...style }}>{children}</div>
}

export function Label({ children, style }: { children: ReactNode; style?: CSSProperties }) {
  return (
    <div style={{
      fontFamily: TERM_FONT, fontSize: '11px', letterSpacing: '0.1em',
      color: 'var(--text-tertiary)', marginBottom: '8px', ...style,
    }}>
      {children}
    </div>
  )
}

export function Btn({
  children, onClick, color = ACCENT, active = true, disabled, size = 'md', title, style,
}: {
  children: ReactNode
  onClick?: () => void
  color?: string
  active?: boolean
  disabled?: boolean
  size?: 'sm' | 'md'
  title?: string
  style?: CSSProperties
}) {
  const sm = size === 'sm'
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      title={title}
      style={{
        fontFamily: TERM_FONT,
        fontSize: sm ? '11px' : '13px',
        letterSpacing: '0.08em',
        padding: sm ? '4px 10px' : '8px 18px',
        border: `${sm ? 1 : 2}px solid ${active ? color : 'var(--border-color)'}`,
        background: active ? `${color}14` : 'transparent',
        color: active ? color : 'var(--text-tertiary)',
        boxShadow: active && !sm && !disabled ? `2px 2px 0px 0px ${color}` : 'none',
        cursor: disabled ? 'not-allowed' : 'pointer',
        opacity: disabled ? 0.45 : 1,
        whiteSpace: 'nowrap',
        ...style,
      }}
    >
      {children}
    </button>
  )
}

export function Input({
  value, onChange, placeholder, mono = true, style,
}: {
  value: string
  onChange: (v: string) => void
  placeholder?: string
  mono?: boolean
  style?: CSSProperties
}) {
  return (
    <input
      value={value}
      onChange={e => onChange(e.target.value)}
      placeholder={placeholder}
      style={{
        width: '100%',
        fontFamily: mono ? TERM_FONT : 'inherit',
        fontSize: '13px',
        padding: '6px 10px',
        border: '1px solid var(--border-color)',
        background: 'transparent',
        color: 'var(--text-primary)',
        outline: 'none',
        ...style,
      }}
    />
  )
}

/** Scrolling `>` log — the deploy/compile transcript. */
export function Log({ lines, live }: { lines: string[]; live?: boolean }) {
  if (!lines.length) return null
  return (
    <div style={{
      ...panelStyle, padding: '14px', fontFamily: TERM_FONT, fontSize: '12.5px',
      maxHeight: '260px', overflowY: 'auto',
    }}>
      {lines.map((line, i) => (
        <div key={i} style={{
          color: /ERROR|FAILED|✗/.test(line) ? '#ef4444'
            : /✓|complete|deployed/i.test(line) ? ACCENT
              : 'var(--text-secondary)',
          marginBottom: '3px', whiteSpace: 'pre-wrap', wordBreak: 'break-all',
        }}>
          {line}
        </div>
      ))}
      {live && <div style={{ color: ACCENT }}>{'> '}working…</div>}
    </div>
  )
}

export function Empty({ children }: { children: ReactNode }) {
  return (
    <div style={{ fontFamily: TERM_FONT, fontSize: '13px', color: 'var(--text-tertiary)', padding: '8px 0' }}>
      {children}
    </div>
  )
}
