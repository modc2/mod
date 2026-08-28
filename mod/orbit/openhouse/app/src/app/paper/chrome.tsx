/* Chrome shared by the whitepaper pages. Server components — nothing here
   is interactive, so the section pages ship as static HTML.

   No VIBE picker: layout.tsx stamps the saved vibe on <html> before first
   paint on every route, so a GAMEBOY reader stays in GAMEBOY here. The
   picker itself lives on the home page, one click away. */

import Link from 'next/link'
import { SECTIONS, LAUNCH } from '../../lib/whitepaper'

/** Testnet strip + the section rail. `here` is the slug of the page you're on
 *  (omit it on the index) so the rail can mark its place. */
export function PaperNav({ here }: { here?: string }) {
  return (
    <div className="sticky top-0 z-50">
      <div className="banner-strip">
        <div className="max-w-5xl mx-auto px-5 md:px-8 min-h-[2.25rem] py-1.5 flex items-center justify-center gap-2.5 text-center">
          <span className="shrink-0 text-[9px] font-black uppercase tracking-[0.18em] text-paper bg-ink rounded px-1.5 py-0.5">{LAUNCH.stage}</span>
          <p className="text-[11px] leading-tight text-white/85">
            Running on {LAUNCH.chain} — test ETH only, nothing here is real money or a real deed.
            <span className="text-white/68"> Mainnet launch: {LAUNCH.date.toLowerCase()}.</span>
          </p>
        </div>
      </div>
      <nav className="bg-paper/85 backdrop-blur-xl border-b border-white/[0.07]">
        <div className="max-w-5xl mx-auto px-5 md:px-8 h-14 flex items-center justify-between gap-4">
          <Link href="/" className="flex items-center gap-2.5 shrink-0">
            <span className="w-7 h-7 rounded-md bg-gradient-to-br from-coral to-ember flex items-center justify-center text-onaccent font-black text-xs shadow-lg shadow-coral/20">⌂</span>
            <span className="font-display font-extrabold tracking-tight text-[14px] text-white">OpenHouse</span>
          </Link>
          <div className="flex items-center gap-1 overflow-x-auto">
            <Link href="/paper" className={`px-2.5 py-1.5 rounded-full text-[11px] font-bold uppercase tracking-widest transition-colors ${here ? 'text-white/55 hover:text-coral' : 'text-coral'}`}>
              Paper
            </Link>
            {SECTIONS.map(s => (
              <Link key={s.slug} href={`/paper/${s.slug}`} title={s.kicker}
                className={`px-2.5 py-1.5 rounded-full text-[11px] font-bold tabular-nums transition-colors ${here === s.slug ? 'bg-coral text-onaccent' : 'text-white/55 hover:text-coral'}`}>
                {s.no}
              </Link>
            ))}
          </div>
          <Link href="/#invest" className="btn-shine shrink-0 px-3.5 py-2 rounded-full bg-ink text-paper text-[11px] font-bold uppercase tracking-widest hover:bg-pink transition-colors">
            Try Testnet
          </Link>
        </div>
      </nav>
    </div>
  )
}

export function PaperFooter() {
  return (
    <footer className="border-t border-white/[0.07] mt-24">
      <div className="max-w-5xl mx-auto px-5 md:px-8 py-10 flex flex-wrap items-center justify-between gap-4">
        <p className="text-white/45 text-[11px] max-w-xl leading-relaxed">
          <span className="text-coral font-bold uppercase tracking-widest">{LAUNCH.stage} — </span>
          {LAUNCH.notice} Nothing on this page is an offer to sell securities or financial advice.
        </p>
        <Link href="/" className="text-[11px] font-bold uppercase tracking-widest text-white/60 hover:text-coral transition-colors">
          ← openhouse.home
        </Link>
      </div>
    </footer>
  )
}
