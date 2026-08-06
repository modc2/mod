"use client";

import { useEffect, useRef, useState } from 'react'
import { SwatchIcon } from '@heroicons/react/24/outline'
import { THEMES, useTheme, type Theme } from './theme'

// SKIN selector. The cap on the header carries three chips off the current
// palette; pressing it drops a list of every skin, each row wearing its own
// three plus the line that says what it's for. No preview, no animation —
// you flip the switch and the console is a different console.
export default function ThemePicker() {
  const { theme, setTheme } = useTheme()
  const [open, setOpen] = useState(false)
  const wrap = useRef<HTMLDivElement>(null)
  const current = THEMES.find(t => t.id === theme) ?? THEMES[0]

  // Click-away and Escape both close it — a menu you can only dismiss by
  // picking something is a modal, and this isn't one.
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (wrap.current && !wrap.current.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  return (
    <div ref={wrap} className="relative shrink-0">
      <button
        onClick={() => setOpen(o => !o)}
        className="btn btn-sm px-2.5 gap-2"
        title={`Skin: ${current.label}`}
        aria-haspopup="menu"
        aria-expanded={open}
      >
        <SwatchIcon className="w-3.5 h-3.5" />
        <Chips chips={current.chips} />
        {/* Name only from sm up — on a phone the chips already say which
            skin you're in, and the header is out of room. */}
        <span className="hidden sm:inline normal-case tracking-normal">{current.label}</span>
      </button>

      {open && (
        <div className="menu right-0 mt-2 w-64 max-h-[70vh] overflow-y-auto" role="menu" aria-label="Skin">
          <p className="lbl-dim px-2.5 pt-1.5 pb-2">Skin</p>
          {THEMES.map(t => (
            <button
              key={t.id}
              role="menuitemradio"
              aria-checked={t.id === theme}
              className="menu-item"
              onClick={() => { setTheme(t.id as Theme); setOpen(false) }}
            >
              <Chips chips={t.chips} />
              <span className="flex-1 min-w-0">
                <span className="block font-semibold">{t.label}</span>
                <span className="block text-[10px] text-faint truncate">{t.note}</span>
              </span>
              <span className="w-3 text-accent" aria-hidden>{t.id === theme ? '●' : ''}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

function Chips({ chips }: { chips: readonly string[] }) {
  return (
    <span className="inline-flex shrink-0 rounded-sm overflow-hidden border border-line" aria-hidden>
      {chips.map(c => (
        <span key={c} className="w-2.5 h-3.5 block" style={{ background: c }} />
      ))}
    </span>
  )
}
