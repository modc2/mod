"use client";

import { useEffect, useRef, useState } from 'react'
import { THEMES, useTheme, type Theme } from './theme'

// WORLD select — the pause-menu switch on the nav. The cap carries three
// chips off the current world; pressing it drops a hard menu of every world,
// each row wearing its own three. No preview, no fade: you flip the switch
// and the board is a different level.
export default function SkinPicker() {
  const { theme, setTheme } = useTheme()
  const [open, setOpen] = useState(false)
  const wrap = useRef<HTMLDivElement>(null)
  const current = THEMES.find(t => t.id === theme) ?? THEMES[0]

  // Click-away and Escape both close it — a menu you can only dismiss by
  // picking something is a modal, and this isn't one.
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => { if (wrap.current && !wrap.current.contains(e.target as Node)) setOpen(false) }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => { document.removeEventListener('mousedown', onDown); document.removeEventListener('keydown', onKey) }
  }, [open])

  return (
    <div ref={wrap} className="skinwrap">
      <button className="btn btn-sm" onClick={() => setOpen(o => !o)}
        title={`World: ${current.label}`} aria-haspopup="menu" aria-expanded={open}>
        <Chips chips={current.chips} />
        {/* Label only from md up — on a phone the chips already say which
            world you're in, and the nav is out of room. */}
        <span className="hidden md:inline">{current.label}</span>
      </button>

      {open && (
        <div className="skin-menu" role="menu" aria-label="World">
          {THEMES.map(t => (
            <button key={t.id} role="menuitemradio" aria-checked={t.id === theme}
              className="skin-menu__item" onClick={() => { setTheme(t.id as Theme); setOpen(false) }}>
              <span className="skin-menu__cursor" aria-hidden>{t.id === theme ? '▶' : ''}</span>
              <span className="skin-menu__label">{t.label}</span>
              <span className="skin-menu__world">{t.world}</span>
              <Chips chips={t.chips} />
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

function Chips({ chips }: { chips: readonly string[] }) {
  return (
    <span className="skin-chips" aria-hidden>
      {chips.map(c => <span key={c} className="skin-chip" style={{ background: c }} />)}
    </span>
  )
}
