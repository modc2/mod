'use client'

// Select — the one dropdown for the whole console.
//
// A native <select> pops an OS-drawn menu (light grey on Linux/Windows) that
// fights the terminal palette, and it can't render icons, hints or badges.
// This draws the menu itself, in a portal keyed off the trigger's screen rect —
// so the zoomed, overflow-hidden Builder canvas can neither clip nor scale it.

import { useState, useRef, useEffect, useCallback } from 'react'
import { createPortal } from 'react-dom'

export type Option = {
  value: string
  label: string
  icon?: string    // glyph in the left gutter
  hint?: string    // dim trailing text — a description, a model family, …
  badge?: string   // small pill, e.g. "built-in"
}

export type Accent = 'emerald' | 'amber' | 'sky' | 'violet' | 'gray'

// Tailwind only sees whole class names, so accents are looked up, never built.
const ACCENT: Record<Accent, { open: string; item: string; check: string; ring: string }> = {
  emerald: { open: 'border-emerald-500/40', item: 'bg-emerald-500/10 border-emerald-500/25 text-gray-100', check: 'text-emerald-300', ring: 'focus:border-emerald-500/40' },
  amber:   { open: 'border-amber-400/40',   item: 'bg-amber-400/10 border-amber-400/25 text-gray-100',    check: 'text-amber-300',   ring: 'focus:border-amber-400/40' },
  sky:     { open: 'border-sky-400/40',     item: 'bg-sky-400/10 border-sky-400/25 text-gray-100',        check: 'text-sky-300',     ring: 'focus:border-sky-400/40' },
  violet:  { open: 'border-violet-400/40',  item: 'bg-violet-400/10 border-violet-400/25 text-gray-100',  check: 'text-violet-300',  ring: 'focus:border-violet-400/40' },
  gray:    { open: 'border-white/25',       item: 'bg-white/[0.07] border-white/15 text-gray-100',        check: 'text-gray-300',    ring: 'focus:border-white/25' },
}

const SIZE = {
  sm: { trigger: 'px-1.5 py-1 text-[11px] gap-1', item: 'px-2 py-1.5 text-[11px]', chev: 8 },
  md: { trigger: 'px-2 py-1.5 text-xs gap-1.5', item: 'px-2.5 py-2 text-xs', chev: 9 },
}

type Props = {
  value: string
  options: Option[]
  onChange: (value: string) => void
  accent?: Accent
  size?: 'sm' | 'md'
  placeholder?: string
  title?: string
  className?: string
  /** show the filter box once the list is long enough to need one */
  searchable?: boolean
  disabled?: boolean
}

export default function Select({
  value, options, onChange, accent = 'gray', size = 'md',
  placeholder = 'select…', title, className = '', searchable, disabled,
}: Props) {
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState('')
  const [cursor, setCursor] = useState(0)
  const [box, setBox] = useState<
    { left?: number; right?: number; top?: number; bottom?: number; minW: number; maxW: number; maxH: number } | null
  >(null)

  const triggerRef = useRef<HTMLButtonElement>(null)
  const listRef = useRef<HTMLDivElement>(null)
  const searchRef = useRef<HTMLInputElement>(null)

  const a = ACCENT[accent]
  const sz = SIZE[size]
  const current = options.find(o => o.value === value)
  const withSearch = searchable ?? options.length > 8

  const shown = search.trim()
    ? options.filter(o => (o.label + ' ' + (o.hint || '')).toLowerCase().includes(search.trim().toLowerCase()))
    : options

  // Anchor the menu to the trigger's position on screen. Recomputed on open and
  // on any scroll/resize, since the trigger may live on a pannable canvas.
  const place = useCallback(() => {
    const el = triggerRef.current
    if (!el) return
    const r = el.getBoundingClientRect()
    // the menu sizes to its longest option (model ids run long) between these bounds
    const minW = Math.max(r.width, 200)
    const maxW = Math.min(380, window.innerWidth - 16)
    // triggers living in the right half tuck to their right edge rather than spilling offscreen
    const horiz = r.left + r.width / 2 > window.innerWidth * 0.6
      ? { right: Math.max(8, window.innerWidth - r.right) }
      : { left: Math.max(8, r.left) }
    const below = window.innerHeight - r.bottom - 12
    const above = r.top - 12
    // drop downward unless the list would be cramped and there's more room up top
    if (below < 200 && above > below) setBox({ ...horiz, bottom: window.innerHeight - r.top + 6, minW, maxW, maxH: Math.min(340, above) })
    else setBox({ ...horiz, top: r.bottom + 6, minW, maxW, maxH: Math.min(340, below) })
  }, [])

  useEffect(() => {
    if (!open) return
    place()
    const onMove = () => place()
    // Escape closes wherever focus happens to be — the menu is in a portal, so
    // it can't rely on the keydown bubbling up through the trigger.
    const onEsc = (e: KeyboardEvent) => { if (e.key === 'Escape') { setOpen(false); triggerRef.current?.focus() } }
    window.addEventListener('scroll', onMove, true)
    window.addEventListener('resize', onMove)
    document.addEventListener('keydown', onEsc)
    return () => {
      window.removeEventListener('scroll', onMove, true)
      window.removeEventListener('resize', onMove)
      document.removeEventListener('keydown', onEsc)
    }
  }, [open, place])

  useEffect(() => {
    if (!open) { setSearch(''); return }
    setCursor(Math.max(0, options.findIndex(o => o.value === value)))
    if (withSearch) requestAnimationFrame(() => searchRef.current?.focus())
  }, [open]) // eslint-disable-line react-hooks/exhaustive-deps

  // keep the highlighted row in view as the cursor walks the list
  useEffect(() => {
    if (!open) return
    listRef.current?.querySelector<HTMLElement>('[data-cursor="1"]')?.scrollIntoView({ block: 'nearest' })
  }, [cursor, open])

  const commit = (v: string) => { onChange(v); setOpen(false) }

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') { setOpen(false); triggerRef.current?.focus(); return }
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault()
      if (!open) { setOpen(true); return }
      setCursor(c => {
        const n = shown.length
        if (!n) return 0
        return (c + (e.key === 'ArrowDown' ? 1 : n - 1)) % n
      })
      return
    }
    if (e.key === 'Enter' || (e.key === ' ' && !open)) {
      e.preventDefault()
      if (!open) setOpen(true)
      else if (shown[cursor]) commit(shown[cursor].value)
    }
  }

  const menu = open && box && (
    <>
      <div className="fixed inset-0 z-[90]" onPointerDown={() => setOpen(false)} />
      <div
        className="fixed z-[91] flex flex-col bg-[#141414] border border-white/10 rounded-lg shadow-2xl overflow-hidden select-pop"
        style={{
          left: box.left, right: box.right, top: box.top, bottom: box.bottom,
          width: 'max-content', minWidth: box.minW, maxWidth: box.maxW, maxHeight: box.maxH,
        }}
        onPointerDown={e => e.stopPropagation()}
        onKeyDown={onKeyDown}
      >
        {withSearch && (
          <div className="p-1.5 border-b border-white/[0.06] shrink-0">
            <input
              ref={searchRef}
              value={search}
              onChange={e => { setSearch(e.target.value); setCursor(0) }}
              placeholder="Filter…"
              className={`w-full bg-white/[0.04] border border-white/[0.08] rounded-md px-2 py-1.5 text-[11px] text-gray-200 outline-none placeholder:text-gray-600 transition ${a.ring}`}
            />
          </div>
        )}
        <div ref={listRef} className="flex-1 overflow-y-auto min-h-0 p-1 space-y-0.5">
          {shown.length === 0 && (
            <div className="px-2.5 py-4 text-center text-[11px] text-gray-600">nothing matches</div>
          )}
          {shown.map((o, i) => {
            const active = o.value === value
            return (
              <button
                key={o.value}
                data-cursor={i === cursor ? '1' : undefined}
                onClick={() => commit(o.value)}
                onPointerEnter={() => setCursor(i)}
                className={`w-full flex items-center gap-2 text-left rounded-md border transition ${sz.item} ${
                  active ? a.item : i === cursor ? 'bg-white/[0.05] border-transparent text-gray-200' : 'border-transparent text-gray-400'
                }`}
              >
                <span className="w-4 text-center shrink-0 opacity-80">{o.icon || ''}</span>
                <span className="truncate">{o.label}</span>
                {o.badge && (
                  <span className="text-[9px] px-1 py-0.5 rounded bg-white/[0.06] text-gray-500 shrink-0 font-mono">{o.badge}</span>
                )}
                {o.hint && <span className="text-[10px] text-gray-600 truncate ml-auto pl-2">{o.hint}</span>}
                {active && <span className={`ml-auto shrink-0 text-[10px] ${a.check}`}>✓</span>}
              </button>
            )
          })}
        </div>
      </div>
    </>
  )

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        disabled={disabled}
        onClick={() => !disabled && setOpen(v => !v)}
        onKeyDown={onKeyDown}
        title={title ?? current?.label ?? placeholder}
        className={`flex items-center bg-white/5 border rounded-md text-gray-300 outline-none transition-colors min-w-0 ${sz.trigger} ${
          disabled ? 'opacity-40 cursor-not-allowed border-white/10'
            : open ? `${a.open} text-gray-200 cursor-pointer`
            : 'border-white/10 hover:border-white/20 cursor-pointer'
        } ${className}`}
      >
        {current?.icon && <span className="shrink-0 opacity-80">{current.icon}</span>}
        <span className={`truncate ${current ? '' : 'text-gray-600'}`}>{current?.label ?? placeholder}</span>
        <svg width={sz.chev} height={sz.chev} viewBox="0 0 24 24" fill="none" stroke="currentColor"
          strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"
          className={`shrink-0 ml-auto pl-0.5 text-gray-600 transition-transform ${open ? 'rotate-180' : ''}`}>
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </button>
      {typeof document !== 'undefined' && menu ? createPortal(menu, document.body) : null}
    </>
  )
}
