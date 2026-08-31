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

/** Cabinet palette — player-one pink through to the coin gold. */
export const NEON = {
  p1: '#ff2e88',
  p2: '#22d3ee',
  coin: '#ffcc33',
  life: '#39ff14',
  dead: '#ff3b30',
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
  --text-secondary: rgba(255, 255, 255, 0.68);
  --text-tertiary: rgba(255, 255, 255, 0.45);
  --border-color: rgba(148, 163, 184, 0.28);
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
.arc-cabinet::before {
  content: ''; position: absolute; inset: 0; pointer-events: none; z-index: -1;
  background:
    radial-gradient(1000px 540px at 18% -60px, color-mix(in srgb, var(--accent-primary, #10b981) 20%, transparent), transparent 62%),
    radial-gradient(820px 480px at 88% 40px, rgba(34,211,238,.13), transparent 60%),
    radial-gradient(820px 560px at 50% 105%, rgba(255,46,136,.13), transparent 65%),
    repeating-linear-gradient(90deg, rgba(148,163,184,.045) 0 1px, transparent 1px 56px),
    repeating-linear-gradient(0deg, rgba(148,163,184,.045) 0 1px, transparent 1px 56px);
}
.arc-cabinet::after {
  content: ''; position: absolute; inset: 0; pointer-events: none; z-index: 30;
  background: repeating-linear-gradient(
    0deg, rgba(0,0,0,.13) 0px, rgba(0,0,0,.13) 1px, transparent 1px, transparent 3px);
  animation: arc-roll 14s linear infinite;
  opacity: .35;
}

/* A clickable card. Set its colour with --c; on hover it lifts against its
   own hard shadow, on press it sits down into it — the same physics as
   arc-press, but the throw lights up in the card's colour. */
.arc-card { transition: transform .08s steps(2), box-shadow .08s steps(2), border-color .08s, background .08s; }
.arc-card:hover:not(:disabled) {
  border-color: var(--c, var(--accent-primary, #10b981)) !important;
  box-shadow: 5px 5px 0 0 var(--c, var(--accent-primary, #10b981)) !important;
  transform: translate(-2px, -2px);
}
.arc-card:active:not(:disabled) { transform: translate(3px, 3px); box-shadow: none !important; }

/* A resting tab wakes up under the pointer. The lit tab keeps its own colour. */
.arc-tab[aria-pressed="false"]:hover:not(:disabled) {
  color: var(--text-primary) !important;
  border-color: var(--border-strong, var(--border-color)) !important;
}

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

/**
 * The cabinet marquee: one strip across the top — the game's name at the
 * left, the controls a player reaches for without thinking beside it (which
 * chain, who's signing, what they hold, which project), and the score at the
 * far right, where a cabinet keeps it. The controls stretch to fill whatever
 * the title and score leave, so the strip is always full. On a phone there is
 * no such row: the title takes its own band and the controls wrap under it.
 */
export function Marquee({
  title, subtitle, controls, readouts = [], compact,
}: {
  title: string
  subtitle: string
  /** the control row — pills with dropdowns, so this box must not clip */
  controls?: ReactNode
  readouts?: { label: string; value: string; color?: string; led?: LedState }[]
  compact?: boolean
}) {
  // the shine needs overflow:hidden and the dropdowns need the opposite, so
  // the sweep is scoped to the title alone — never to a box holding controls
  const name = (
    <span className="arc-shine" style={{
      display: 'inline-flex', alignItems: 'flex-end', gap: '10px', flexShrink: 0,
      // room for the glow: the sweep box would otherwise crop it
      padding: '4px 8px 4px 4px', margin: '-4px -8px -4px -4px',
    }}>
      <span style={{
        fontFamily: PIXEL,
        fontSize: compact ? '18px' : '24px',
        letterSpacing: '0.12em',
        lineHeight: 1,
        color: ACCENT,
        // chromatic offset — the ghosting a real cabinet's shadow mask
        // gives. One pixel: at two the fringes detach and the word blurs.
        textShadow: `1px 0 0 ${NEON.p1}, -1px 0 0 ${NEON.p2}, 0 0 16px ${ACCENT}`,
      }}>
        {title}
      </span>
      <span className="arc-blink" style={{
        fontFamily: PIXEL, fontSize: compact ? '18px' : '24px', lineHeight: 1, color: NEON.coin,
      }}>
        ▮
      </span>
    </span>
  )

  const score = readouts.length > 0 && (
    <div style={{ display: 'flex', gap: '22px', alignItems: 'flex-end', marginLeft: 'auto', flexShrink: 0 }}>
      {readouts.map(r => (
        <div key={r.label} style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: '5px' }}>
          <span style={{
            display: 'flex', alignItems: 'center', gap: '6px',
            fontFamily: PIXEL, fontSize: '7px', color: 'var(--text-tertiary)', letterSpacing: '0.18em',
          }}>
            {r.led && <Led state={r.led} size={6} />}
            {r.label}
          </span>
          <span style={{
            fontFamily: PIXEL, fontSize: compact ? PX.lg : '18px', lineHeight: 1,
            color: r.color || 'var(--text-primary)',
            textShadow: `0 0 10px ${r.color || ACCENT}88`,
            whiteSpace: 'nowrap',
          }}>
            {r.value}
          </span>
        </div>
      ))}
    </div>
  )

  const frame: CSSProperties = {
    border: `3px solid ${ACCENT}`,
    boxShadow: `4px 4px 0 0 ${NEON.p1}55, 8px 8px 0 0 ${NEON.p2}33`,
    background: 'linear-gradient(180deg, var(--bg-secondary), var(--bg-primary))',
    marginBottom: '14px',
    color: ACCENT,
  }

  if (compact) {
    return (
      <div className="arc-bolts" style={frame}>
        <div style={{ display: 'flex', alignItems: 'flex-end', gap: '14px', flexWrap: 'wrap', padding: '12px 12px 8px' }}>
          {name}
          {score}
        </div>
        {controls && (
          <div style={{
            display: 'flex', alignItems: 'stretch', gap: '8px', flexWrap: 'wrap',
            padding: '10px 12px 12px',
            borderTop: `2px solid ${ACCENT}33`,
          }}>
            {controls}
          </div>
        )}
      </div>
    )
  }

  return (
    <div className="arc-bolts" style={frame}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '14px', padding: '12px 18px' }}>
        {name}
        {controls && (
          <Strip.Provider value={true}>
            <div style={{
              display: 'flex', alignItems: 'stretch', gap: '10px', flex: '1 1 0', minWidth: 0,
            }}>
              {controls}
            </div>
          </Strip.Provider>
        )}
        {score}
      </div>
    </div>
  )
}
