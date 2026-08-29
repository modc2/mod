/* ── Vibes ───────────────────────────────────────────────
   Eight cabinets. Every colour, font and texture on the site is a CSS
   token, so a vibe is nothing but three attributes on <html>:

     mode  the field      paper (washi, daylight) | digital (a CRT)
     skin  the treatment  soft (curves, blur) | pixel (8-bit, hard shadows)
     id    the palette    ten triplets and nothing else

   That split is the reason this table is ten lines and not eight
   stylesheets: MARIO and GAMEBOY differ by colour alone, and both ride
   the same paper·pixel structure. See the VIBES section of globals.css.
   `chips` are three colours lifted from the cabinet's own palette (field,
   accent, gain) — the swatch you read before you switch, and the only
   identity a row needs. (A glyph column was tried and cut: the pixel
   skins' faces have no box-drawing glyphs, so every row rendered tofu.)

   layout.tsx applies the saved vibe before first paint on every route, so
   nothing flashes and the picker travels with the nav; here we only read
   it back and rewrite it. */

"use client";

import { useCallback, useEffect, useRef, useState } from 'react'

type Mode = 'paper' | 'digital'
type Skin = 'soft' | 'pixel'
const VIBE_KEY = 'openhouse_vibe'

const VIBES = [
  { id: 'sunday',   label: 'Sunday',   mode: 'paper',   skin: 'soft',  note: 'washi paper, Memphis pastels',     chips: ['#fff6ec', '#ff7a5c', '#2ed3b7'] },
  { id: 'mario',    label: 'Mario',    mode: 'paper',   skin: 'pixel', note: 'world 1-1: brick, coin, pipe',     chips: ['#5c94fc', '#e43434', '#fcd83c'] },
  { id: 'gameboy',  label: 'Game Boy', mode: 'paper',   skin: 'pixel', note: 'DMG — four greens, no fifth',      chips: ['#9bbc0f', '#306230', '#0f380f'] },
  { id: 'terminal', label: 'Terminal', mode: 'digital', skin: 'soft',  note: 'the building on a green screen',   chips: ['#040807', '#00e8a0', '#ff2f87'] },
  { id: 'amber',    label: 'Amber',    mode: 'digital', skin: 'soft',  note: 'single-gun CRT, one hue burnt in', chips: ['#0d0700', '#ffb028', '#ffe896'] },
  { id: 'arcade',   label: 'Arcade',   mode: 'digital', skin: 'pixel', note: 'NES black under cabinet neon',     chips: ['#0a0614', '#22f0ff', '#2bff88'] },
  { id: 'c64',      label: 'C64',      mode: 'digital', skin: 'pixel', note: '64K BASIC, light blue on blue',    chips: ['#30288a', '#aaffee', '#aaff66'] },
  { id: 'vapor',    label: 'Vapor',    mode: 'digital', skin: 'pixel', note: 'Miami at 2am, horizon grid',       chips: ['#160328', '#ff2d95', '#2bffce'] },
] as const satisfies readonly { id: string; label: string; mode: Mode; skin: Skin; note: string; chips: readonly string[] }[]

type VibeId = (typeof VIBES)[number]['id']
const DEFAULT_VIBE: VibeId = 'sunday'

function useVibe(): [(typeof VIBES)[number], (id: VibeId) => void] {
  const [id, setId] = useState<VibeId>(DEFAULT_VIBE)
  // The boot script already stamped <html>; read it back rather than
  // re-reading localStorage, so the two can never disagree.
  useEffect(() => {
    const stamped = document.documentElement.getAttribute('data-vibe')
    if (stamped && VIBES.some(v => v.id === stamped)) setId(stamped as VibeId)
  }, [])
  const pick = useCallback((next: VibeId) => {
    const v = VIBES.find(x => x.id === next)
    if (!v) return
    const d = document.documentElement
    d.setAttribute('data-vibe', v.id)
    d.setAttribute('data-mode', v.mode)
    d.setAttribute('data-skin', v.skin)
    try { localStorage.setItem(VIBE_KEY, v.id) } catch {}
    setId(v.id)
  }, [])
  return [VIBES.find(v => v.id === id) ?? VIBES[0], pick]
}

function Chips({ chips }: { chips: readonly string[] }) {
  return (
    <span aria-hidden className="flex shrink-0 rounded-[3px] overflow-hidden border border-white/15">
      {chips.map(c => <span key={c} className="w-2 h-3.5" style={{ background: c }} />)}
    </span>
  )
}

/* The DIP-switch panel. The cap on the nav wears the current cabinet's
   three chips; pressing it drops the whole set, each row wearing its own.
   No preview and no animation — you flip the switch and the site is a
   different machine, on whichever page you're standing. */
export function VibePicker() {
  const [vibe, pick] = useVibe()
  const [open, setOpen] = useState(false)
  const wrap = useRef<HTMLDivElement>(null)

  // Click-away and Escape both close it. A menu you can only leave by
  // choosing something is a modal, and this isn't one.
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
      <button onClick={() => setOpen(o => !o)} aria-haspopup="menu" aria-expanded={open}
        title={`Vibe: ${vibe.label} — ${vibe.note}`}
        className="flex items-center gap-2 px-3 py-2 rounded-full border border-white/12 text-[11px] font-bold uppercase tracking-widest text-white/68 hover:text-coral hover:border-coral/40 transition-colors">
        <Chips chips={vibe.chips} />
        {/* Below a wide desktop the chips already say which cabinet you're
            in, and the eight-route rail wants every pixel. */}
        <span className="hidden xl:inline">{vibe.label}</span>
      </button>

      {open && (
        <div role="menu" aria-label="Vibe"
          className="absolute right-0 mt-2 w-[17.5rem] p-1.5 glass rounded-2xl shadow-xl z-50">
          {VIBES.map(v => (
            <button key={v.id} role="menuitemradio" aria-checked={v.id === vibe.id}
              onClick={() => { pick(v.id); setOpen(false) }}
              className={`w-full flex items-center gap-2.5 px-2.5 py-2 rounded-xl text-left transition-colors ${
                v.id === vibe.id ? 'bg-coral/[0.12] text-coral' : 'text-white/75 hover:bg-white/[0.06]'}`}>
              <span aria-hidden className="w-3 text-[11px] leading-none">{v.id === vibe.id ? '▸' : ''}</span>
              <span className="flex-1 min-w-0">
                <span className="block text-[12px] font-bold">{v.label}</span>
                <span className="block text-[10px] text-white/45 truncate">{v.note}</span>
              </span>
              <Chips chips={v.chips} />
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
