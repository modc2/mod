/* The section rail the whitepaper pages wear under the site nav.

   The site rail (components/chrome.tsx) says which of the eight pages you
   are on; this one says which of the six sections. Server component —
   nothing here is interactive, so the section pages ship as static HTML. */

import Link from 'next/link'
import { SECTIONS } from '../../lib/whitepaper'

/** `here` is the slug of the page you're on — omit it on the index. */
export function PaperRail({ here }: { here?: string }) {
  return (
    <div className="border-b border-white/[0.07] bg-white/[0.02]">
      <div className="max-w-5xl mx-auto px-5 md:px-8 h-12 flex items-center gap-1 overflow-x-auto no-bar">
        <Link href="/paper"
          className={`shrink-0 px-2.5 py-1.5 rounded-full text-[11px] font-bold uppercase tracking-widest transition-colors ${here ? 'text-white/55 hover:text-coral' : 'text-coral'}`}>
          Contents
        </Link>
        {SECTIONS.map(s => (
          <Link key={s.slug} href={`/paper/${s.slug}`} title={`${s.kicker} — ${s.title}`}
            className={`shrink-0 px-2.5 py-1.5 rounded-full text-[11px] font-bold transition-colors ${
              here === s.slug ? 'bg-coral text-onaccent' : 'text-white/55 hover:text-coral'}`}>
            <span className="tabular-nums">{s.no}</span>
            <span className="hidden md:inline uppercase tracking-widest"> · {s.kicker}</span>
          </Link>
        ))}
      </div>
    </div>
  )
}
