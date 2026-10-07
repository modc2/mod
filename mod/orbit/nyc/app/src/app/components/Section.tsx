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
    <section className="border-b border-white/10">
      <button
        onClick={onToggle}
        aria-expanded={open}
        className="tap relative flex w-full items-center gap-2 py-2.5 pl-4 pr-3 text-left hover:bg-white/[0.06]"
      >
        {/* A course of brick down the edge of every header — enough texture to
            read as a level, not so much that it fights the type. */}
        <span className="accent-bar absolute inset-y-1.5 left-0 w-[3px]" aria-hidden />
        <Arrow open={open} />
        <h3 className="pixel flex-1 truncate text-[11px] leading-none text-nes-ink3">
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

/** A chevron: ▸ closed, ▾ open. */
function Arrow({ open }: { open: boolean }) {
  return (
    <svg width="10" height="10" viewBox="0 0 10 10" fill="none" aria-hidden
         className={`shrink-0 text-nes-ink3 transition-transform duration-150 ${open ? 'rotate-90' : ''}`}>
      <path d="M3.5 2l4 3-4 3" stroke="currentColor" strokeWidth="1.5"
            strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}
