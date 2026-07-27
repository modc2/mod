'use client'

type Props = {
  title: string
  open: boolean
  onToggle: () => void
  /** Shown on the right of the header — a read of what's inside while folded. */
  summary?: React.ReactNode
  children: React.ReactNode
}

/**
 * A folding block in the left rail. The whole header is the hit target, so a
 * section closes with the same gesture that opened it, and the summary keeps
 * the section legible while it is shut.
 *
 * The arrow is drawn on a pixel grid and snaps between its two states — the
 * chrome elsewhere steps rather than eases, and a spinning chevron would be
 * the one smooth thing on screen.
 */
export default function Section({ title, open, onToggle, summary, children }: Props) {
  return (
    <section className="border-b-[3px] border-black">
      <button
        onClick={onToggle}
        aria-expanded={open}
        className="relative flex w-full items-center gap-2 py-2.5 pl-4 pr-3 text-left hover:bg-white/[0.06]"
      >
        {/* A course of brick down the edge of every header — enough texture to
            read as a level, not so much that it fights the type. */}
        <span className="brick absolute inset-y-0 left-0 w-1.5" aria-hidden />
        <Arrow open={open} />
        <h3 className="pixel flex-1 truncate text-[8px] leading-none text-nes-ink3">
          {title}
        </h3>
        {summary !== undefined && summary !== null && summary !== '' && (
          <span className="max-w-[120px] shrink-0 truncate text-[10.5px] text-nes-ink3">
            {summary}
          </span>
        )}
      </button>
      {open && children}
    </section>
  )
}

/** Two whole-pixel triangles: ▶ closed, ▼ open. */
function Arrow({ open }: { open: boolean }) {
  return (
    <svg width="8" height="8" viewBox="0 0 8 8" className="shrink-0 text-nes-coin"
         shapeRendering="crispEdges" fill="currentColor" aria-hidden>
      {open ? (
        <>
          <rect x="0" y="2" width="8" height="2" />
          <rect x="1" y="4" width="6" height="2" />
          <rect x="3" y="6" width="2" height="2" />
        </>
      ) : (
        <>
          <rect x="2" y="0" width="2" height="8" />
          <rect x="4" y="1" width="2" height="6" />
          <rect x="6" y="3" width="2" height="2" />
        </>
      )}
    </svg>
  )
}
