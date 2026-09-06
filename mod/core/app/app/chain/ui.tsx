"use client"

// Cabinet primitives. The rule: a hard offset shadow is the throw of a
// BUTTON, so only things you press wear one — panels are quiet boxes with a
// hairline edge, and the page gets its depth from one light behind the
// marquee instead of from a black shadow under every rectangle. Pixel type on
// the chrome, terminal type on the data. Everything the chain tabs draw goes
// through these so the whole console reads as one machine.

import { CSSProperties, ReactNode, useContext, useEffect, useRef } from 'react'
import { TERM_FONT, ACCENT, useIsMobile } from './shared'
import { PIXEL, PX, NEON, Strip } from './arcade'

export const panelStyle: CSSProperties = {
  border: '1px solid var(--border-color)',
  background: 'var(--bg-secondary)',
  boxShadow: '0 1px 0 0 rgba(255,255,255,0.035) inset, 0 8px 24px -18px rgba(0,0,0,0.9)',
}

export function Panel({ children, style }: { children: ReactNode; style?: CSSProperties }) {
  return <div style={{ ...panelStyle, padding: '16px', ...style }}>{children}</div>
}

/**
 * A section header. The bar on the left is what turns a line of small pixel
 * text into a label — without it, chrome and data run together.
 *
 * Pass anything longer than a couple of words as `note`: a sentence set in
 * Press Start 2P is a wall, and on a phone it wraps into one.
 */
export function Label({
  children, note, style,
}: {
  children: ReactNode
  note?: ReactNode
  style?: CSSProperties
}) {
  const color = (style?.color as string) || 'var(--text-tertiary)'
  return (
    <div style={{
      display: 'flex', alignItems: 'baseline', gap: '8px', flexWrap: 'wrap',
      fontFamily: PIXEL, fontSize: PX.xs, letterSpacing: '0.14em',
      color, marginBottom: '10px', ...style,
    }}>
      <span style={{
        width: '4px', height: '11px', background: color, flexShrink: 0,
        alignSelf: 'center',
      }} />
      <span style={{ lineHeight: 1.6 }}>{children}</span>
      {note && (
        <span style={{
          fontFamily: TERM_FONT, fontSize: '14px', letterSpacing: 'normal',
          color: 'var(--text-tertiary)', opacity: 0.9,
        }}>
          {note}
        </span>
      )}
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
      className="arc-press arc-pixel"
      style={{
        fontFamily: PIXEL,
        fontSize: sm ? PX.xs : PX.sm,
        letterSpacing: '0.08em',
        lineHeight: 1.6,
        padding: sm ? '7px 10px' : '11px 16px',
        minHeight: sm ? '30px' : '42px',
        border: `${sm ? 1 : 2}px solid ${active ? color : 'var(--border-color)'}`,
        background: active ? `${color}16` : 'transparent',
        color: active ? color : 'var(--text-tertiary)',
        boxShadow: active && !disabled ? `2px 2px 0px 0px ${color}${sm ? '99' : ''}` : 'none',
        cursor: disabled ? 'not-allowed' : 'pointer',
        opacity: disabled ? 0.4 : 1,
        whiteSpace: 'nowrap',
        textShadow: active && !sm ? `0 0 12px ${color}55` : undefined,
        width: full && mobile ? '100%' : undefined,
        ...style,
      }}
    >
      {children}
    </button>
  )
}

export function Input({
  value, onChange, placeholder, mono = true, style, onEnter, autoFocus,
}: {
  value: string
  onChange: (v: string) => void
  placeholder?: string
  mono?: boolean
  style?: CSSProperties
  onEnter?: () => void
  /** an inline field that just opened — rename, quick-add — should already have the caret */
  autoFocus?: boolean
}) {
  const mobile = useIsMobile()
  return (
    <input
      autoFocus={autoFocus}
      value={value}
      onChange={e => onChange(e.target.value)}
      onKeyDown={onEnter ? e => { if (e.key === 'Enter') onEnter() } : undefined}
      placeholder={placeholder}
      style={{
        width: '100%',
        // What you type here is data — addresses, RPC URLs, constructor args —
        // so it stays in the terminal face even inside the cabinet.
        fontFamily: mono ? TERM_FONT : 'inherit',
        // iOS zooms the page for any field under 16px — never worth the pixels
        fontSize: mobile ? '16px' : '14px',
        padding: mobile ? '10px' : '7px 10px',
        border: '1px solid var(--border-color)',
        background: 'rgba(0,0,0,0.25)',
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
        background: 'rgba(0,0,0,0.75)', display: 'flex',
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
          padding: '12px 14px', borderBottom: '1px solid var(--border-color)',
          position: 'sticky', top: 0, background: 'var(--bg-primary)', zIndex: 1,
        }}>
          <span style={{ fontFamily: PIXEL, fontSize: PX.sm, letterSpacing: '0.14em', color: ACCENT }}>
            {title}
          </span>
          <button onClick={onClose} aria-label="close" style={{
            fontFamily: TERM_FONT, fontSize: '20px', lineHeight: 1, border: 'none',
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

/** Scrolling `>` log — the deploy/compile transcript, on the cabinet's screen. */
export function Log({ lines, live }: { lines: string[]; live?: boolean }) {
  if (!lines.length) return null
  return (
    <div style={{ ...panelStyle }}>
      <div style={{
        display: 'flex', alignItems: 'center', gap: '8px',
        padding: '7px 12px', borderBottom: '1px solid var(--border-color)',
        fontFamily: PIXEL, fontSize: PX.xs, letterSpacing: '0.14em', color: 'var(--text-tertiary)',
      }}>
        CONSOLE {live && <span className="arc-blink" style={{ color: NEON.coin }}>● REC</span>}
      </div>
      <div style={{
        padding: '12px 14px', fontFamily: TERM_FONT, fontSize: '14px',
        maxHeight: '260px', overflowY: 'auto',
      }}>
        {lines.map((line, i) => (
          <div key={i} style={{
            color: /ERROR|FAILED|✗/.test(line) ? NEON.dead
              : /✓|complete|deployed/i.test(line) ? ACCENT
                : 'var(--text-secondary)',
            marginBottom: '3px', whiteSpace: 'pre-wrap', wordBreak: 'break-all',
          }}>
            {line}
          </div>
        ))}
        {live && <div style={{ color: ACCENT }}>{'> '}working<span className="arc-blink">…</span></div>}
      </div>
    </div>
  )
}

export function Empty({ children }: { children: ReactNode }) {
  return (
    <div style={{ fontFamily: TERM_FONT, fontSize: '14px', color: 'var(--text-tertiary)', padding: '8px 0' }}>
      {children}
    </div>
  )
}

/**
 * A loud, explainable failure. The console's panels each swallow their own
 * errors, so without this a broken API just reads as an empty page.
 */
export function Banner({
  tone = 'bad', title, children, onRetry,
}: {
  tone?: 'bad' | 'warn'
  title: string
  children?: ReactNode
  onRetry?: () => void
}) {
  const color = tone === 'bad' ? NEON.dead : NEON.coin
  return (
    <div style={{
      border: `1px solid ${color}`,
      background: `${color}14`,
      boxShadow: `0 0 0 1px ${color}33, 0 10px 26px -20px ${color}`,
      padding: '12px 14px',
      marginBottom: '12px',
      display: 'flex', alignItems: 'flex-start', gap: '12px', flexWrap: 'wrap',
    }}>
      <span className="arc-blink" style={{ color, fontFamily: PIXEL, fontSize: PX.sm, lineHeight: 1.7 }}>!</span>
      <div style={{ flex: 1, minWidth: '200px' }}>
        <div style={{
          fontFamily: PIXEL, fontSize: PX.sm, letterSpacing: '0.1em', color,
          lineHeight: 1.7, marginBottom: children ? '6px' : 0,
        }}>
          {title}
        </div>
        {children && (
          <div style={{
            fontFamily: TERM_FONT, fontSize: '15px', color: 'var(--text-secondary)',
            lineHeight: 1.55, wordBreak: 'break-word',
          }}>
            {children}
          </div>
        )}
      </div>
      {onRetry && <Btn size="sm" color={color} onClick={onRetry}>RETRY</Btn>}
    </div>
  )
}

/**
 * Placeholder rows for a list that hasn't answered yet. An empty box reads as
 * "nothing here"; this reads as "not yet" — the difference the console needs
 * while the chain module is waking up.
 */
export function Skeleton({ rows = 3, height = 44 }: { rows?: number; height?: number }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="arc-pulse" style={{
          ...panelStyle, height: `${height}px`, opacity: 0.5 - i * 0.1,
          animationDelay: `${i * 0.15}s`,
        }} />
      ))}
    </div>
  )
}

// ── pills + dropdowns ───────────────────────────────────────────────────────
//
// The marquee's control row is three of these side by side — network, player,
// balance — so they share one shape: a colour bar down the left edge, a pixel
// label in that colour, a terminal-face value, a chevron flush right. Only the
// OPEN one lights its whole border, which is what keeps three coloured
// controls from fighting each other. Each fills the width it's given, so the
// row reads as one strip of instruments rather than three loose badges.

export function Pill({
  label, children, color = ACCENT, open, onClick, led, tip, title, style, blink,
}: {
  label: string
  children: ReactNode
  color?: string
  open?: boolean
  onClick?: () => void
  led?: ReactNode
  /** hover hint, drawn in the cabinet's face — never the browser's grey box */
  tip?: string
  /** same as `tip` — kept so a pill written against the old name still hints */
  title?: string
  style?: CSSProperties
  /** the value is a call to action, not a reading — flash it */
  blink?: boolean
}) {
  const dense = useContext(Strip)
  return (
    <button
      onClick={onClick}
      data-tip={tip || title || ''}
      aria-expanded={onClick ? !!open : undefined}
      className="arc-ctl arc-tip"
      style={{
        display: 'flex', alignItems: 'center', gap: '10px',
        width: '100%', height: '100%',
        // line-height 1 + overflow:hidden on the value would clip the tall
        // terminal digits top and bottom — a 0 came out as ()
        fontFamily: TERM_FONT, fontSize: dense ? '15px' : '17px', lineHeight: 1.3,
        padding: dense ? '0 10px' : '0 12px', minHeight: dense ? '38px' : '42px',
        // Four pills used to wear four fat coloured edges and the strip read
        // as a fruit machine. The chrome is now one neutral face for all of
        // them; the colour survives where it does work — the label, the lamp,
        // and the whole border of the one that's OPEN.
        borderStyle: 'solid', borderWidth: '1px',
        borderColor: open ? color : 'var(--border-color)',
        background: open ? `${color}12` : 'rgba(0,0,0,0.22)',
        boxShadow: open ? `0 0 0 1px ${color}55` : 'none',
        color: 'var(--text-primary)', cursor: onClick ? 'pointer' : 'default',
        textAlign: 'left',
        ...style,
      }}
    >
      {led}
      <span style={{
        fontFamily: PIXEL, fontSize: PX.xs, letterSpacing: '0.12em', color,
        flexShrink: 0, paddingTop: '1px',
      }}>
        {label}
      </span>
      <span
        className={`arc-val${blink ? ' arc-blink-soft' : ''}`}
        style={{
          display: 'flex', alignItems: 'center', gap: '8px', minWidth: 0, flex: 1,
          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
        }}
      >
        {children}
      </span>
      {onClick && (
        <span style={{ fontSize: dense ? '9px' : '11px', color: open ? color : 'var(--text-tertiary)', flexShrink: 0, opacity: open ? 1 : 0.7 }}>
          {open ? '▲' : '▼'}
        </span>
      )}
    </button>
  )
}

/**
 * A hover hint in the cabinet's own face. The browser's native `title` draws
 * a grey system box that belongs to no theme and takes a second to appear;
 * this is the same hint drawn like a dropdown. Wrap anything that would
 * otherwise carry a `title` — a truncated address, an RPC's own error text.
 */
export function Tip({ text, children, style }: { text: string; children: ReactNode; style?: CSSProperties }) {
  return (
    <span className="arc-tip" data-tip={text} tabIndex={0} style={{ display: 'inline-flex', alignItems: 'center', ...style }}>
      {children}
    </span>
  )
}

/**
 * A pill's second reading — the block behind the chain, the wallet's badge,
 * a project's saved state. Shown where the pill has room; dropped whole in
 * the marquee strip, where four pills share a line and a hint cut off
 * mid-word (\"blk 46,\") reads worse than no hint.
 */
export function Hint({ children }: { children: ReactNode }) {
  const dense = useContext(Strip)
  return dense ? null : <>{children}</>
}

/**
 * A panel hung under its trigger. Closes on a click anywhere else or Esc;
 * never wider than the phone it drops down on.
 */
export function Dropdown({
  open, onClose, trigger, width = 340, align = 'left', color = ACCENT, children, grow,
}: {
  open: boolean
  onClose: () => void
  trigger: ReactNode
  width?: number
  align?: 'left' | 'right'
  color?: string
  children: ReactNode
  /** take a share of the row — the marquee pills, so the strip is always full */
  grow?: number
}) {
  const box = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const away = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) onClose()
    }
    const key = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('mousedown', away)
    document.addEventListener('keydown', key)
    return () => {
      document.removeEventListener('mousedown', away)
      document.removeEventListener('keydown', key)
    }
  }, [open, onClose])

  return (
    <div ref={box} style={{ position: 'relative', maxWidth: '100%', minWidth: 0, flex: grow ? `${grow} 1 auto` : undefined }}>
      {trigger}
      {open && (
        <div style={{
          ...panelStyle,
          position: 'absolute', zIndex: 60, top: 'calc(100% + 6px)',
          [align]: 0,
          width: `min(${width}px, calc(100vw - 32px))`,
          maxHeight: 'min(70vh, 560px)', overflowY: 'auto',
          borderColor: color,
          boxShadow: `0 0 0 1px ${color}44, 0 18px 40px -24px rgba(0,0,0,0.95)`,
          background: 'var(--bg-primary)',
        }}>
          {children}
        </div>
      )}
    </div>
  )
}

/** A group heading inside a dropdown. */
export function DropHead({ children, right, color }: { children: ReactNode; right?: ReactNode; color?: string }) {
  return (
    <div style={{
      display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px',
      fontFamily: PIXEL, fontSize: PX.xs, letterSpacing: '0.14em',
      color: color || 'var(--text-tertiary)', padding: '12px 12px 6px',
    }}>
      <span>{children}</span>
      {right}
    </div>
  )
}

/** One selectable line inside a dropdown. `right` sits flush against the edge. */
export function DropRow({
  active, onClick, children, right, title, color = ACCENT, disabled,
}: {
  active?: boolean
  onClick?: () => void
  children: ReactNode
  right?: ReactNode
  title?: string
  color?: string
  disabled?: boolean
}) {
  return (
    <div style={{ display: 'flex', alignItems: 'stretch' }}>
      <button
        onClick={onClick}
        disabled={disabled}
        title={title}
        style={{
          flex: 1, minWidth: 0, display: 'flex', alignItems: 'center', gap: '10px',
          textAlign: 'left', fontFamily: TERM_FONT, fontSize: '15px',
          padding: '8px 12px', minHeight: '42px', border: 'none',
          borderLeft: `3px solid ${active ? color : 'transparent'}`,
          background: active ? `${color}14` : 'transparent',
          color: active ? 'var(--text-primary)' : 'var(--text-secondary)',
          cursor: onClick && !disabled ? 'pointer' : 'default',
          opacity: disabled ? 0.5 : 1,
        }}
      >
        {children}
      </button>
      {right && (
        <div style={{ display: 'flex', alignItems: 'center', gap: '4px', paddingRight: '8px', flexShrink: 0 }}>
          {right}
        </div>
      )}
    </div>
  )
}

/** A hairline between dropdown sections. */
export const DropRule = () => <div style={{ borderTop: '1px solid var(--border-color)', margin: '6px 0' }} />

/** A quiet text-only action for dropdown rows — export, remove, copy. */
export function Quiet({ onClick, title, children, color }: {
  onClick: () => void
  title?: string
  children: ReactNode
  color?: string
}) {
  return (
    <button
      onClick={onClick}
      title={title}
      style={{
        fontFamily: PIXEL, fontSize: PX.xs, letterSpacing: '0.08em', lineHeight: 1,
        padding: '6px 6px', border: 'none', background: 'transparent',
        color: color || 'var(--text-tertiary)', cursor: 'pointer',
      }}
      className="arc-pixel"
    >
      {children}
    </button>
  )
}
