"use client"

// Terminal-console primitives: hard 2px borders, offset shadows, mono type.
// Everything the chain tabs draw goes through these so the vibe stays one vibe.

import { CSSProperties, ReactNode, useEffect } from 'react'
import { TERM_FONT, ACCENT, useIsMobile } from './shared'

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
  children, onClick, color = ACCENT, active = true, disabled, size = 'md', title, style, full,
}: {
  children: ReactNode
  onClick?: () => void
  color?: string
  active?: boolean
  disabled?: boolean
  size?: 'sm' | 'md'
  title?: string
  style?: CSSProperties
  /** stretch to the row on a phone — thumbs don't aim well at 80px targets */
  full?: boolean
}) {
  const sm = size === 'sm'
  const mobile = useIsMobile()
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      title={title}
      style={{
        fontFamily: TERM_FONT,
        fontSize: sm ? '11px' : '13px',
        letterSpacing: '0.08em',
        padding: sm ? '6px 10px' : '10px 18px',
        minHeight: sm ? '30px' : '44px',
        border: `${sm ? 1 : 2}px solid ${active ? color : 'var(--border-color)'}`,
        background: active ? `${color}14` : 'transparent',
        color: active ? color : 'var(--text-tertiary)',
        boxShadow: active && !sm && !disabled ? `2px 2px 0px 0px ${color}` : 'none',
        cursor: disabled ? 'not-allowed' : 'pointer',
        opacity: disabled ? 0.45 : 1,
        whiteSpace: 'nowrap',
        width: full && mobile ? '100%' : undefined,
        ...style,
      }}
    >
      {children}
    </button>
  )
}

export function Input({
  value, onChange, placeholder, mono = true, style, onEnter,
}: {
  value: string
  onChange: (v: string) => void
  placeholder?: string
  mono?: boolean
  style?: CSSProperties
  onEnter?: () => void
}) {
  const mobile = useIsMobile()
  return (
    <input
      value={value}
      onChange={e => onChange(e.target.value)}
      onKeyDown={onEnter ? e => { if (e.key === 'Enter') onEnter() } : undefined}
      placeholder={placeholder}
      style={{
        width: '100%',
        fontFamily: mono ? TERM_FONT : 'inherit',
        // iOS zooms the page for any field under 16px — never worth the pixels
        fontSize: mobile ? '16px' : '13px',
        padding: mobile ? '10px' : '6px 10px',
        border: '1px solid var(--border-color)',
        background: 'transparent',
        color: 'var(--text-primary)',
        outline: 'none',
        ...style,
      }}
    />
  )
}

/**
 * A slide-over panel for phone width — what the left rail becomes when there's
 * no room beside the editor. Renders nothing until opened.
 */
export function Sheet({
  open, onClose, title, children,
}: {
  open: boolean
  onClose: () => void
  title: string
  children: ReactNode
}) {
  // A sheet over the page shouldn't scroll the page behind it.
  useEffect(() => {
    if (!open) return
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => { document.body.style.overflow = prev }
  }, [open])

  if (!open) return null
  return (
    <div
      onClick={onClose}
      style={{
        // above the app's own top bar (z-70) — a half-covered sheet hides its
        // own close button behind the nav
        position: 'fixed', inset: 0, zIndex: 100,
        background: 'rgba(0,0,0,0.6)', display: 'flex',
      }}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{
          width: 'min(88vw, 320px)', height: '100%', overflowY: 'auto',
          background: 'var(--bg-primary)', borderRight: `2px solid ${ACCENT}`,
        }}
      >
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '12px 14px', borderBottom: '2px solid var(--border-color)',
          position: 'sticky', top: 0, background: 'var(--bg-primary)', zIndex: 1,
        }}>
          <span style={{ fontFamily: TERM_FONT, fontSize: '12px', letterSpacing: '0.14em', color: ACCENT }}>
            {title}
          </span>
          <button onClick={onClose} aria-label="close" style={{
            fontFamily: TERM_FONT, fontSize: '18px', lineHeight: 1, border: 'none',
            background: 'transparent', color: 'var(--text-tertiary)', cursor: 'pointer',
            padding: '4px 8px',
          }}>
            ✕
          </button>
        </div>
        <div style={{ padding: '10px' }}>{children}</div>
      </div>
    </div>
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
