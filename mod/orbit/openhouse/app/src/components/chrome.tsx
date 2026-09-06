/* The chrome every page wears: testnet strip, the route rail, the footer.

   This is what replaced the anchor nav. The site used to be one document
   with eight `#targets` in it — every visitor downloaded the cap table and
   waited on the landscape's third-party APIs to read the manifesto. Now
   each of those is a route: the rail below is the whole site map, the
   active page is marked, and the browser's back button means something. */

"use client";

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { ReactNode, useEffect, useRef } from 'react'
import { LAUNCH } from '../lib/whitepaper'
import { VibePicker } from './vibe'

/** The site map. `blurb` is the one-line pitch the home page directory
 *  uses, so a route is described in exactly one place. */
export const ROUTES = [
  { href: '/manifesto', label: 'Manifesto', blurb: 'Why a rent cheque should buy something' },
  { href: '/split',     label: 'The Split', blurb: 'Where every payment goes — and the dial that sets it' },
  { href: '/simulator', label: 'Simulator', blurb: 'Scrub the timeline and watch the equity fill up' },
  { href: '/invest',    label: 'Invest',    blurb: 'The building, the float, and how to take a position' },
  { href: '/paper',     label: 'Whitepaper', blurb: 'Six sections, one page each' },
  { href: '/landscape', label: 'Landscape', blurb: 'Every other on-chain housing project, honestly' },
  { href: '/code',      label: 'Code',      blurb: 'The Solidity that holds the shares' },
  { href: '/captable',  label: 'Cap Table', blurb: 'Who owns what, public by default' },
] as const

/** /paper/03-the-take is still the whitepaper as far as the rail cares. */
function isHere(pathname: string, href: string) {
  return pathname === href || pathname.startsWith(href + '/')
}

export function SiteNav() {
  const pathname = usePathname() || '/'
  const home = pathname === '/'
  const rail = useRef<HTMLDivElement>(null)
  const current = useRef<HTMLAnchorElement>(null)

  // On a narrow screen the rail is a window onto eight routes, and the one
  // you're standing in may be off to the right of it. Centre it — by
  // setting scrollLeft rather than scrollIntoView, which would drag the
  // whole document up under the sticky header.
  useEffect(() => {
    const r = rail.current, el = current.current
    if (!r || !el || r.scrollWidth <= r.clientWidth) return
    r.scrollLeft = Math.max(0, el.offsetLeft - (r.clientWidth - el.clientWidth) / 2)
  }, [pathname])

  return (
    <div className="sticky top-0 z-50">
      <div className="banner-strip">
        <div className="max-w-6xl mx-auto px-5 md:px-8 min-h-[2.25rem] py-1.5 flex items-center justify-center gap-2.5 text-center">
          <span className="shrink-0 text-[9px] font-black uppercase tracking-[0.18em] text-paper bg-ink rounded px-1.5 py-0.5">{LAUNCH.stage}</span>
          <p className="text-[11px] leading-tight text-white/85">
            Running on {LAUNCH.chain} — test ETH only, nothing here is real money or a real deed.
            <span className="text-white/68"> Mainnet launch: {LAUNCH.date.toLowerCase()}.</span>
          </p>
        </div>
      </div>

      <nav className="bg-paper/85 backdrop-blur-xl border-b border-white/[0.07]">
        {/* Wider than the 6xl text column above xl: eight routes, a swatch
            and a CTA need more room than a paragraph does, and a header
            that runs past its content is a header, not a mistake. */}
        <div className="max-w-6xl xl:max-w-7xl mx-auto px-5 md:px-8 h-16 flex items-center gap-4">
          <Link href="/" className="flex items-center gap-2.5 shrink-0" aria-current={home ? 'page' : undefined}>
            <span className="w-8 h-8 rounded-md bg-gradient-to-br from-coral to-ember flex items-center justify-center text-onaccent font-black text-sm shadow-lg shadow-coral/20">⌂</span>
            <span className="font-display font-extrabold tracking-tight text-[15px] text-white hidden sm:inline">OpenHouse</span>
          </Link>

          {/* The rail scrolls sideways rather than collapsing into a burger:
              eight destinations you can see beat one you have to open. */}
          <div ref={rail} className="flex-1 min-w-0 flex items-center gap-1 overflow-x-auto no-bar">
            {ROUTES.map(r => {
              const here = isHere(pathname, r.href)
              return (
                <Link key={r.href} href={r.href} title={r.blurb}
                  ref={here ? current : undefined}
                  aria-current={here ? 'page' : undefined}
                  className={`shrink-0 px-2.5 py-1.5 rounded-full text-[11px] font-bold uppercase tracking-widest transition-colors ${
                    here ? 'bg-coral text-onaccent' : 'text-white/60 hover:text-coral'}`}>
                  {r.label}
                </Link>
              )
            })}
          </div>

          <div className="flex items-center gap-3 shrink-0">
            <VibePicker />
            <Link href="/invest" className="btn-shine px-4 py-2 rounded-full bg-ink text-paper text-[11px] font-bold uppercase tracking-widest hover:bg-pink transition-colors">
              Try Testnet
            </Link>
          </div>
        </div>
      </nav>
    </div>
  )
}

export function SiteFooter() {
  return (
    <footer className="border-t border-white/[0.07] mt-24">
      <div className="max-w-6xl mx-auto px-5 md:px-8 pt-10 pb-6 flex flex-col md:flex-row items-center justify-between gap-4">
        <Link href="/" className="flex items-center gap-2.5">
          <span className="w-7 h-7 rounded-md bg-gradient-to-br from-coral to-ember flex items-center justify-center text-onaccent font-black text-xs">⌂</span>
          <span className="font-display font-extrabold text-white text-sm">OpenHouse</span>
        </Link>
        <p className="text-white/55 text-xs text-center">Rent-to-own on-chain · Fractional property via smart contracts · Base</p>
        <p className="text-white/45 text-[11px] uppercase tracking-widest">Equity for everybody</p>
      </div>

      {/* The site map again, at the bottom of every page — the same eight
          routes, so you never have to scroll back up to leave. */}
      <div className="max-w-6xl mx-auto px-5 md:px-8 pb-8">
        <div className="flex flex-wrap gap-x-6 gap-y-2 justify-center border-t border-white/[0.05] pt-6">
          {ROUTES.map(r => (
            <Link key={r.href} href={r.href}
              className="text-[11px] font-bold uppercase tracking-widest text-white/50 hover:text-coral transition-colors">
              {r.label}
            </Link>
          ))}
        </div>
      </div>

      <div className="max-w-6xl mx-auto px-5 md:px-8 pb-10">
        <p className="text-white/55 text-[11px] leading-relaxed text-center max-w-2xl mx-auto">
          <span className="text-coral font-bold uppercase tracking-widest">{LAUNCH.stage} — </span>
          {LAUNCH.notice} Nothing on this page is an offer to sell securities or financial advice.
        </p>
      </div>
    </footer>
  )
}

/** Page frame: grain, aurora, nav, content, footer. */
export function Shell({ children }: { children: ReactNode }) {
  return (
    <div className="relative grain vignette min-h-screen">
      <div className="aurora" />
      <SiteNav />
      <div className="relative z-10">{children}</div>
      <SiteFooter />
    </div>
  )
}

/** The masthead each interior page opens with — kicker, headline, standfirst. */
export function PageHead({ kicker, title, children, aside }: {
  kicker: string; title: ReactNode; children?: ReactNode; aside?: ReactNode
}) {
  return (
    <header className="max-w-6xl mx-auto px-5 md:px-8 pt-16 pb-10 md:pt-24 md:pb-14">
      <div className="flex items-end justify-between flex-wrap gap-6">
        <div className="max-w-3xl">
          <div className="text-coral text-[11px] font-bold uppercase tracking-[0.3em] mb-4">{kicker}</div>
          <h1 className="headline text-5xl md:text-7xl text-white leading-[0.95]">{title}</h1>
          {children && <div className="font-serif-ed text-xl text-white/68 mt-7 leading-snug">{children}</div>}
        </div>
        {aside}
      </div>
    </header>
  )
}

/** The "where to next" card pair at the foot of every interior page. Given
 *  the route you're on, it offers the two either side of it in the rail —
 *  the paged equivalent of just carrying on scrolling. */
export function NextUp({ here }: { here: string }) {
  const i = ROUTES.findIndex(r => r.href === here)
  const prev = i > 0 ? ROUTES[i - 1] : null
  const next = i >= 0 && i < ROUTES.length - 1 ? ROUTES[i + 1] : null
  if (!prev && !next) return null
  return (
    <nav className="max-w-6xl mx-auto px-5 md:px-8 grid gap-4 md:grid-cols-2 mt-20">
      {prev ? (
        <Link href={prev.href} className="glass glass-hover rounded-2xl p-6 group">
          <div className="text-[10px] font-bold uppercase tracking-widest text-white/45 group-hover:text-coral transition-colors">← {prev.label}</div>
          <div className="font-serif-ed text-xl text-white mt-2 leading-snug">{prev.blurb}</div>
        </Link>
      ) : <div className="hidden md:block" />}
      {next && (
        <Link href={next.href} className="glass glass-hover rounded-2xl p-6 md:text-right group">
          <div className="text-[10px] font-bold uppercase tracking-widest text-white/45 group-hover:text-coral transition-colors">{next.label} →</div>
          <div className="font-serif-ed text-xl text-white mt-2 leading-snug">{next.blurb}</div>
        </Link>
      )}
    </nav>
  )
}
