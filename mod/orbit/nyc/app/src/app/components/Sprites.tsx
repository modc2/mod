/**
 * The HUD's small icon set. The exports keep the names they had under the old
 * 8-bit theme (Coin, QuestionBlock, Mushroom) so no call site had to move,
 * but they are ordinary line icons now: 1.5px strokes, rounded caps, drawn on
 * a 16×16 grid.
 */

/** Busy indicator — a ring with a gap. Wrap in `.coin-spin` to rotate it. */
export function Coin({ size = 14 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="none" aria-hidden>
      <circle cx="8" cy="8" r="6" stroke="rgba(232,182,76,0.25)" strokeWidth="2" />
      <path d="M8 2a6 6 0 0 1 6 6" stroke="#e8b64c" strokeWidth="2" strokeLinecap="round" />
    </svg>
  )
}

/** The layer-rail toggle: a stack of map layers. */
export function QuestionBlock({ size = 18 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="none" aria-hidden>
      <path d="M8 1.8 14.6 5.4 8 9 1.4 5.4 8 1.8Z"
            fill="rgba(232,182,76,0.35)" stroke="#e8b64c" strokeWidth="1.3"
            strokeLinejoin="round" />
      <path d="M1.4 8.6 8 12.2l6.6-3.6" stroke="#e8b64c" strokeWidth="1.3"
            strokeLinecap="round" strokeLinejoin="round" opacity="0.7" />
      <path d="M1.4 11.6 8 15.2l6.6-3.6" stroke="#e8b64c" strokeWidth="1.3"
            strokeLinecap="round" strokeLinejoin="round" opacity="0.4" />
    </svg>
  )
}

/** Shown when the map can't reach its API. */
export function Mushroom({ size = 40 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden>
      <path d="M12 3 2.8 19.5a1 1 0 0 0 .87 1.5h16.66a1 1 0 0 0 .87-1.5L12 3Z"
            stroke="#f0564a" strokeWidth="1.6" strokeLinejoin="round" />
      <path d="M12 9.5v5" stroke="#f0564a" strokeWidth="1.8" strokeLinecap="round" />
      <circle cx="12" cy="17.6" r="1.1" fill="#f0564a" />
    </svg>
  )
}
