"use client"

// The cabinet. Everything that makes the chain console look like an arcade
// machine rather than a terminal lives here: the pixel type scale, the neon
// palette, the CRT overlay, and the marquee across the top.
//
// The rule the rest of the console follows: pixel type is for CHROME (titles,
// labels, buttons, tabs) and never for DATA. Press Start 2P at a readable size
// eats four times the width of VT323, and an address or a Solidity file has to
// stay legible — so addresses, source and logs keep the terminal font.

import { CSSProperties, ReactNode } from 'react'
import { ethers } from 'ethers'
import { TERM_FONT, ACCENT } from './shared'

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

export const ARCADE_CSS = `
/* globals.css pins every <button> to the terminal face with !important, so an
   inline fontFamily can never win. Chrome that wants pixel type asks for it by
   class — and only chrome does: a button showing an address or a project name
   stays on arc-press alone and keeps the readable face. */
.arc-pixel { font-family: var(--font-pixel), 'Press Start 2P', monospace !important; }

@keyframes arc-blink { 0%,49% { opacity: 1 } 50%,100% { opacity: 0 } }
@keyframes arc-sweep { 0% { transform: translateX(-120%) } 100% { transform: translateX(320%) } }
@keyframes arc-pulse { 0%,100% { opacity: .55 } 50% { opacity: 1 } }
@keyframes arc-roll { 0% { background-position: 0 0 } 100% { background-position: 0 -100px } }

/* Hard-edged press: the shadow is the throw of the button, so losing it on
   :active is what makes the thing feel clicked. */
.arc-press { transition: transform .06s steps(2), box-shadow .06s steps(2), filter .12s; }
.arc-press:active:not(:disabled) { transform: translate(3px, 3px); box-shadow: none !important; }
.arc-press:hover:not(:disabled) { filter: brightness(1.35); }

.arc-blink { animation: arc-blink 1.1s steps(1) infinite; }
.arc-pulse { animation: arc-pulse 1.6s ease-in-out infinite; }

/* CRT: scanlines over the whole cabinet, plus a vignette. Both are pinned to
   the cabinet box (not the viewport) so the rest of the app stays flat. */
.arc-cabinet { position: relative; }
.arc-cabinet::after {
  content: ''; position: absolute; inset: 0; pointer-events: none; z-index: 30;
  background: repeating-linear-gradient(
    0deg, rgba(0,0,0,.20) 0px, rgba(0,0,0,.20) 1px, transparent 1px, transparent 3px);
  animation: arc-roll 14s linear infinite;
  opacity: .5;
}

/* Marquee shine — a light bar crossing the title, once every few seconds. */
.arc-shine { position: relative; overflow: hidden; }
.arc-shine::before {
  content: ''; position: absolute; top: 0; bottom: 0; width: 40px;
  background: linear-gradient(90deg, transparent, rgba(255,255,255,.14), transparent);
  animation: arc-sweep 5s linear infinite; pointer-events: none;
}

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
 * The cabinet marquee: the game's name across the top, and under it the
 * controls a player reaches for without thinking — which chain, who's
 * signing, what they hold — with the score off to the right.
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
  return (
    <div
      className="arc-bolts"
      style={{
        border: `3px solid ${ACCENT}`,
        boxShadow: `4px 4px 0 0 ${NEON.p1}55, 8px 8px 0 0 ${NEON.p2}33`,
        background: 'linear-gradient(180deg, var(--bg-secondary), var(--bg-primary))',
        marginBottom: '14px',
        color: ACCENT,
      }}
    >
      {/* the shine needs overflow:hidden, and the control row needs the
          opposite — so the title gets its own band */}
      <div className="arc-shine" style={{
        display: 'flex', alignItems: 'flex-end', gap: '14px', flexWrap: 'wrap',
        padding: compact ? '12px 12px 8px' : '14px 18px 10px',
      }}>
        <span style={{
          fontFamily: PIXEL,
          fontSize: compact ? '18px' : '24px',
          letterSpacing: '0.12em',
          color: ACCENT,
          // chromatic offset — the ghosting a real cabinet's shadow mask gives
          textShadow: `2px 0 0 ${NEON.p1}, -2px 0 0 ${NEON.p2}, 0 0 18px ${ACCENT}`,
        }}>
          {title}
        </span>
        <span className="arc-blink" style={{
          fontFamily: PIXEL, fontSize: compact ? '18px' : '24px', color: NEON.coin,
        }}>
          ▮
        </span>
        {!compact && (
          <span style={{
            fontFamily: PIXEL, fontSize: PX.sm, color: 'var(--text-tertiary)',
            letterSpacing: '0.16em', marginLeft: 'auto', paddingBottom: '4px',
          }}>
            {subtitle}
          </span>
        )}
      </div>

      <div style={{
        display: 'flex', alignItems: 'center', gap: compact ? '8px' : '10px', flexWrap: 'wrap',
        padding: compact ? '10px 12px 12px' : '12px 18px 14px',
        borderTop: `2px solid ${ACCENT}33`,
      }}>
        {controls}
        {readouts.length > 0 && (
          <div style={{
            display: 'flex', gap: '18px', flexWrap: 'wrap', alignItems: 'center',
            marginLeft: compact ? 0 : 'auto',
          }}>
            {readouts.map(r => (
              <div key={r.label} style={{ display: 'flex', alignItems: 'center', gap: '7px', minWidth: 0 }}>
                {r.led && <Led state={r.led} />}
                <span style={{
                  fontFamily: PIXEL, fontSize: PX.xs, color: 'var(--text-tertiary)',
                  letterSpacing: '0.12em',
                }}>
                  {r.label}
                </span>
                <span style={{
                  fontFamily: TERM_FONT, fontSize: '16px', color: r.color || 'var(--text-primary)',
                  overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                }}>
                  {r.value}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
