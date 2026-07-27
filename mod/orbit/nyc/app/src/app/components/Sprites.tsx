/**
 * NES sprites, drawn on a 16×16 grid the way the originals were.
 *
 * `shapeRendering="crispEdges"` everywhere: these are pixel art, and letting
 * the renderer antialias a 1-unit rect turns a hard sprite edge into mush at
 * the sizes we actually draw them (13–22px).
 */

/** Busy indicator. Wrap in `.coin-spin` to make it flip. */
export function Coin({ size = 14 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" aria-hidden>
      <ellipse cx="8" cy="8" rx="5.5" ry="7" fill="#fbd000" stroke="#000" strokeWidth="1.5" />
      <ellipse cx="8" cy="8" rx="2.4" ry="4" fill="none" stroke="#c88f00" strokeWidth="1.5" />
    </svg>
  )
}

/** The layer-rail toggle, and the per-layer "what is this?" affordance. */
export function QuestionBlock({ size = 18 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" shapeRendering="crispEdges" aria-hidden>
      <rect width="16" height="16" fill="#000" />
      <rect x="1" y="1" width="14" height="14" fill="#e39d25" />
      <rect x="1" y="1" width="14" height="2" fill="#f7c95c" />
      <rect x="1" y="13" width="14" height="2" fill="#a85f0d" />
      {/* corner rivets */}
      <g fill="#000">
        <rect x="2" y="2" width="2" height="2" />
        <rect x="12" y="2" width="2" height="2" />
        <rect x="2" y="12" width="2" height="2" />
        <rect x="12" y="12" width="2" height="2" />
      </g>
      {/* the ? — drawn twice, black underneath, for the sprite's drop shadow */}
      <g fill="#000" transform="translate(0,1)">
        <QMark />
      </g>
      <g fill="#fff">
        <QMark />
      </g>
    </svg>
  )
}

function QMark() {
  return (
    <>
      <rect x="6" y="3" width="4" height="1" />
      <rect x="5" y="4" width="1" height="2" />
      <rect x="10" y="4" width="1" height="2" />
      <rect x="9" y="6" width="2" height="1" />
      <rect x="8" y="7" width="2" height="1" />
      <rect x="7" y="8" width="2" height="2" />
      <rect x="7" y="11" width="2" height="2" />
    </>
  )
}

/** Shown when the map can't reach its API — a lost life. */
export function Mushroom({ size = 40 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" aria-hidden>
      <path d="M1.5 8.5a6.5 6 0 0 1 13 0v1h-13z" fill="#e52521" stroke="#000" strokeWidth="1.2"
            strokeLinejoin="round" />
      <circle cx="5.2" cy="6.2" r="1.5" fill="#fff" />
      <circle cx="10.8" cy="6.2" r="1.5" fill="#fff" />
      <path d="M4.5 9.5h7v2.5a2 2 0 0 1-2 2H6.5a2 2 0 0 1-2-2z" fill="#f7d9b0" stroke="#000"
            strokeWidth="1.2" strokeLinejoin="round" />
    </svg>
  )
}
