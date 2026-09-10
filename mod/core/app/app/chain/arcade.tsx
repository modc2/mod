"use client"

// The cabinet. Everything that makes the chain console look like an arcade
// machine rather than a terminal lives here: the pixel type scale, the neon
// palette, the CRT overlay, and the marquee across the top.
//
// The rule the rest of the console follows: pixel type is for CHROME (titles,
// labels, buttons, tabs) and never for DATA. Press Start 2P at a readable size
// eats four times the width of VT323, and an address or a Solidity file has to
// stay legible — so addresses, source and logs keep the terminal font.

import { CSSProperties, ReactNode, createContext } from 'react'
import { ethers } from 'ethers'
import { ACCENT } from './shared'

export const PIXEL = "var(--font-pixel), 'Press Start 2P', monospace"

/**
 * Cabinet palette. The console is a working tool wearing an arcade shirt, so
 * the neons are rationed: ACCENT carries the interface, and these five say one
 * thing each — whose turn, what it costs, is it alive, is it dead. A control
 * that is merely present gets no colour at all; that restraint is what keeps
 * four lit pills in a row from reading as a fruit machine.
 */
export const NEON = {
  p1: '#ff5c9d',
  p2: '#5ee7f5',
  coin: '#ffcc33',
  life: '#4ade80',
  dead: '#ff5a52',
}

/**
 * Pixel type is drawn on a fixed 8px grid, so it only looks right at whole
 * multiples. These are the four sizes the console uses.
 */
export const PX = { xs: '8px', sm: '9px', md: '11px', lg: '14px' }

/**
 * True inside the marquee's one-row strip, where four pills share what the
 * title and score leave over — so a pill sets denser type and drops its
 * secondary hints (see Hint in ui.tsx) instead of clipping them mid-word.
 */
export const Strip = createContext(false)

export const ARCADE_CSS = `
/* globals.css pins every <button> to the terminal face with !important, so an
   inline fontFamily can never win. Chrome that wants pixel type asks for it by
   class — and only chrome does: a button showing an address or a project name
   stays on arc-press alone and keeps the readable face. */
.arc-pixel { font-family: var(--font-pixel), 'Press Start 2P', monospace !important; }

/* The shell's dark theme sets tertiary text to 25% white and borders to 12% —
   fine for a text app, mud under a scanline wash. The cabinet lifts its own
   copy of the dials: labels readable, panel edges visible, a hint of slate in
   the borders so the metal reads cool rather than grey. Scoped to dark; the
   light theme keeps the shell's contrast, which is already sufficient. */
:root.dark .arc-cabinet {
  --text-secondary: rgba(255, 255, 255, 0.70);
  --text-tertiary: rgba(255, 255, 255, 0.46);
  --border-color: rgba(148, 163, 184, 0.20);
  /* the edge a control shows when you reach for it — one step up from the
     resting hairline, so hover is legible without anything having to light up */
  --border-strong: rgba(148, 163, 184, 0.46);
}

@keyframes arc-blink { 0%,49% { opacity: 1 } 50%,100% { opacity: 0 } }
@keyframes arc-blink-soft { 0%,49% { opacity: 1 } 50%,100% { opacity: .3 } }
@keyframes arc-sweep { 0% { transform: translateX(-120%) } 100% { transform: translateX(320%) } }
@keyframes arc-pulse { 0%,100% { opacity: .55 } 50% { opacity: 1 } }
@keyframes arc-roll { 0% { background-position: 0 0 } 100% { background-position: 0 -100px } }

/* Hard-edged press: the shadow is the throw of the button, so losing it on
   :active is what makes the thing feel clicked. */
.arc-press { transition: transform .06s steps(2), box-shadow .06s steps(2), filter .12s; }
.arc-press:active:not(:disabled) { transform: translate(3px, 3px); box-shadow: none !important; }
.arc-press:hover:not(:disabled) { filter: brightness(1.35); }

.arc-blink { animation: arc-blink 1.1s steps(1) infinite; }
/* For a call to action: it pulses, but it never disappears — INSERT COIN
   fully off half the time is a control that looks broken half the time. */
.arc-blink-soft { animation: arc-blink-soft 1.1s steps(1) infinite; }
.arc-pulse { animation: arc-pulse 1.6s ease-in-out infinite; }

/* CRT: scanlines over the whole cabinet, plus a vignette. Both are pinned to
   the cabinet box (not the viewport) so the rest of the app stays flat. */
/* isolation makes the cabinet its own stacking context — without it the
   z:-1 backdrop below slips BEHIND the cabinet's opaque background. */
.arc-cabinet { position: relative; isolation: isolate; }
/* The room the cabinet stands in: neon spill off the marquee and a faint
   floor grid, so empty page below the panels reads as depth, not absence. */
/* One light source, not three. The old backdrop lit the page from three
   corners in three colours and every panel sat in a different wash; now a
   single accent spill falls from behind the marquee and fades out, so the
   page has a top and a bottom. */
.arc-cabinet::before {
  content: ''; position: absolute; inset: 0; pointer-events: none; z-index: -1;
  background:
    radial-gradient(1100px 460px at 24% -120px, color-mix(in srgb, var(--accent-primary, #10b981) 14%, transparent), transparent 68%),
    radial-gradient(900px 420px at 82% -60px, rgba(94,231,245,.06), transparent 66%),
    repeating-linear-gradient(90deg, rgba(148,163,184,.03) 0 1px, transparent 1px 64px),
    repeating-linear-gradient(0deg, rgba(148,163,184,.03) 0 1px, transparent 1px 64px);
}
/* Scanlines you feel rather than see. At .35 they crawled over body text and
   greyed the whole console down; the wash is texture, not a filter. */
.arc-cabinet::after {
  content: ''; position: absolute; inset: 0; pointer-events: none; z-index: 30;
  background: repeating-linear-gradient(
    0deg, rgba(0,0,0,.10) 0px, rgba(0,0,0,.10) 1px, transparent 1px, transparent 4px);
  animation: arc-roll 20s linear infinite;
  opacity: .16;
}

/* A clickable card. Set its colour with --c. A grid of these used to throw a
   5px hard shadow in the card's own colour on hover, which at eleven cards
   made the page flash; the card now lifts a little and its edge lights, which
   says the same thing at a tenth of the volume. */
.arc-card { transition: transform .1s ease, box-shadow .1s ease, border-color .1s, background .1s; }
.arc-card:hover:not(:disabled) {
  border-color: var(--c, var(--accent-primary, #10b981)) !important;
  box-shadow: 0 0 0 1px var(--c, var(--accent-primary, #10b981)),
              0 12px 28px -20px var(--c, var(--accent-primary, #10b981)) !important;
  transform: translateY(-1px);
}
.arc-card:active:not(:disabled) { transform: translateY(1px); }

/* Keyboard users get the same answer the pointer gets, in the accent. */
.arc-cabinet :focus-visible {
  outline: 2px solid var(--accent-primary, #10b981);
  outline-offset: 2px;
}

/* An instrument in the control strip: flat until you reach for it. The
   button physics (a 3px travel onto a hard shadow) belong to things that
   DO something; a pill only opens a menu. */
.arc-ctl { transition: border-color .12s, background .12s, color .12s; }
.arc-ctl:hover:not(:disabled) {
  border-color: var(--border-strong, rgba(148,163,184,.55));
  background: rgba(255,255,255,.035);
}

/* A pill's readout: an address, maybe a badge after it. Clipped by overflow
   alone the badge came out as half a letter inside half a border ("METł"),
   because a badge sets flex-shrink:0 and the row ran out of pill. Everything
   BEFORE the last item gives way first, so what truncates is the reading
   (which the dropdown repeats in full) and never the box around it. */
.arc-val > *:not(:last-child) { min-width: 0; overflow: hidden; text-overflow: ellipsis; }

/* Tabs. Eight bevelled boxes in a row read as eight equal shouts and hid
   which one you were on; a tab is now a word on a rail, and the SELECTED
   word is the only one wearing the accent — the same way a cabinet's lit
   button is the only lit button. */
.arc-tab {
  position: relative;
  background: transparent !important;
  border: 0 !important;
  border-radius: 0;
  box-shadow: none !important;
  color: var(--text-tertiary);
  cursor: pointer;
}
.arc-tab::after {
  content: ''; position: absolute; left: 0; right: 0; bottom: -2px; height: 2px;
  background: transparent; transition: background .1s steps(2);
}
.arc-tab[aria-pressed="false"]:hover:not(:disabled) { color: var(--text-primary); }
.arc-tab[aria-pressed="false"]:hover:not(:disabled)::after { background: var(--border-color); }
.arc-tab[aria-pressed="true"] {
  color: var(--accent-primary, #10b981);
  text-shadow: 0 0 12px color-mix(in srgb, var(--accent-primary, #10b981) 55%, transparent);
}
.arc-tab[aria-pressed="true"]::after {
  background: var(--accent-primary, #10b981); height: 3px; bottom: -2px;
  box-shadow: 0 0 10px color-mix(in srgb, var(--accent-primary, #10b981) 70%, transparent);
}
/* A tab is a word, so it must not travel on press the way a button does. */
.arc-tab.arc-press:active:not(:disabled) { transform: none; }

/* Marquee shine — a light bar crossing the title, once every few seconds. */
.arc-shine { position: relative; overflow: hidden; }
.arc-shine::before {
  content: ''; position: absolute; top: 0; bottom: 0; width: 40px;
  background: linear-gradient(90deg, transparent, rgba(255,255,255,.14), transparent);
  animation: arc-sweep 5s linear infinite; pointer-events: none;
}

/* Hover hint in the cabinet's own face. The browser's tooltip is a grey
   system box that belongs to no theme; this one hangs under the control like
   a dropdown would, and stays hidden while the real dropdown is open. */
.arc-tip { position: relative; }
.arc-tip::after {
  content: attr(data-tip); position: absolute; left: 0; top: calc(100% + 8px);
  z-index: 55; width: max-content; max-width: min(360px, calc(100vw - 40px));
  padding: 6px 10px; font-size: 14px; line-height: 1.3; letter-spacing: 0;
  white-space: normal; word-break: break-all; text-align: left;
  border: 2px solid var(--border-color); background: var(--bg-primary);
  color: var(--text-secondary); box-shadow: 3px 3px 0 0 rgba(0,0,0,.5);
  opacity: 0; transform: translateY(-4px); pointer-events: none;
  transition: opacity .12s steps(3), transform .12s steps(3); transition-delay: .25s;
}
.arc-tip:hover::after, .arc-tip:focus-visible::after { opacity: 1; transform: none; }
.arc-tip[aria-expanded="true"]::after, .arc-tip[data-tip=""]::after { display: none; }

/* Corner rivets — four notched brackets that read as a bolted cabinet panel. */
.arc-bolts { position: relative; }
.arc-bolts::before, .arc-bolts::after {
  content: ''; position: absolute; width: 7px; height: 7px; pointer-events: none;
  border: 2px solid currentColor; opacity: .5;
}
.arc-bolts::before { top: 3px; left: 3px; border-right: 0; border-bottom: 0; }
.arc-bolts::after { bottom: 3px; right: 3px; border-left: 0; border-top: 0; }
`

/** Drop once per page — the keyframes every arcade class here depends on. */
export function ArcadeStyles() {
  return <style dangerouslySetInnerHTML={{ __html: ARCADE_CSS }} />
}

/** Pixel text at one of the four cabinet sizes. */
export function Px({
  children, size = 'sm', color, glow, style,
}: {
  children: ReactNode
  size?: keyof typeof PX
  color?: string
  glow?: boolean
  style?: CSSProperties
}) {
  return (
    <span style={{
      fontFamily: PIXEL,
      fontSize: PX[size],
      lineHeight: 1.7,
      letterSpacing: '0.04em',
      color: color || 'var(--text-primary)',
      textShadow: glow ? `0 0 8px ${color || ACCENT}` : undefined,
      ...style,
    }}>
      {children}
    </span>
  )
}

export type LedState = 'live' | 'warn' | 'dead' | 'idle'

const LED_COLOR: Record<LedState, string> = {
  live: NEON.life, warn: NEON.coin, dead: NEON.dead, idle: 'var(--text-tertiary)',
}

/** A cabinet status lamp. `live` breathes; everything else sits still. */
export function Led({ state, size = 8 }: { state: LedState; size?: number }) {
  const color = LED_COLOR[state]
  return (
    <span
      className={state === 'live' ? 'arc-pulse' : undefined}
      style={{
        display: 'inline-block', width: `${size}px`, height: `${size}px`,
        background: color, boxShadow: `0 0 ${size}px ${color}`, flexShrink: 0,
      }}
    />
  )
}

/**
 * A 5×5 pixel sprite grown from an address — the console's idea of a face.
 * Mirrored left-to-right like every 8-bit character, coloured by the address
 * itself so the same key always wears the same colour.
 */
export function Sprite({ seed, size = 20, style }: { seed: string; size?: number; style?: CSSProperties }) {
  // hash first: raw address hex clusters (vanity prefixes, test keys full of
  // 1s) and a sprite grown from it would be blank or solid
  const hex = ethers.id((seed || '').toLowerCase()).slice(2)
  const hue = parseInt(hex.slice(0, 6), 16) % 360
  const fill = `hsl(${hue}, 85%, 62%)`
  const cells: [number, number][] = []
  // 3 columns × 5 rows of bits, mirrored to 5 wide
  for (let i = 0; i < 15; i++) {
    if (parseInt(hex[6 + i], 16) < 7) continue
    const col = i % 3, row = Math.floor(i / 3)
    cells.push([col, row])
    if (col < 2) cells.push([4 - col, row])
  }
  return (
    <svg
      viewBox="0 0 5 5" width={size} height={size} shapeRendering="crispEdges"
      style={{ flexShrink: 0, background: 'rgba(0,0,0,0.45)', ...style }}
    >
      {cells.map(([x, y]) => <rect key={`${x}${y}`} x={x} y={y} width="1" height="1" fill={fill} />)}
    </svg>
  )
}
