/* /paper/<slug> — one whitepaper section, one page.

   Slugs come from SECTIONS in lib/whitepaper.ts, so the six pages are
   generated from the same copy the contents page lists. Add a section
   there and its page exists; there is nothing to wire up here. */

import type { Metadata } from 'next'
import Link from 'next/link'
import { notFound } from 'next/navigation'
import { Shell } from '../../../components/chrome'
import { SECTIONS, paperSection } from '../../../lib/whitepaper'
import { PaperRail } from '../chrome'

export function generateStaticParams() {
  return SECTIONS.map(s => ({ slug: s.slug }))
}

export function generateMetadata({ params }: { params: { slug: string } }): Metadata {
  const found = paperSection(params.slug)
  if (!found) return { title: 'Not found — OpenHouse' }
  const { section } = found
  return {
    title: `${section.no} · ${section.title} — OpenHouse`,
    // the pull quote is the section in one line; it's the best blurb we have
    description: section.pull || section.body[0].slice(0, 180),
    openGraph: { title: `${section.kicker}: ${section.title}`, description: section.pull || section.body[0].slice(0, 180) },
  }
}

export default function PaperSectionPage({ params }: { params: { slug: string } }) {
  const found = paperSection(params.slug)
  if (!found) notFound()
  const { section, prev, next } = found

  return (
    <Shell>
      <PaperRail here={section.slug} />

      <article className="relative z-10 max-w-3xl mx-auto px-5 md:px-8">
        <header className="pt-16 pb-10 md:pt-24 md:pb-14">
          <Link href="/paper" className="text-[11px] font-bold uppercase tracking-widest text-white/45 hover:text-coral transition-colors">
            ← The whitepaper
          </Link>
          <div className="headline text-[7rem] md:text-[10rem] text-white/[0.13] leading-[0.8] mt-6">{section.no}</div>
          <div className="text-coral text-[11px] font-bold uppercase tracking-[0.25em] mt-2">{section.kicker}</div>
          <h1 className="font-serif-ed text-4xl md:text-6xl text-white leading-[1.05] mt-5">{section.title}</h1>
        </header>

        <div className="space-y-6">
          {section.body.map((p, i) => (
            <p key={i} className="text-white/75 text-lg md:text-xl leading-relaxed">{p}</p>
          ))}
        </div>

        {section.pull && (
          <p className="font-serif-ed italic text-2xl md:text-4xl text-surf-grad mt-14 leading-snug border-l-2 border-coral/40 pl-6">
            “{section.pull}”
          </p>
        )}

        {/* ── Turn the page ─────────────────────────────── */}
        <nav className="grid gap-4 md:grid-cols-2 mt-20">
          {prev ? (
            <Link href={`/paper/${prev.slug}`} className="glass glass-hover rounded-2xl p-6 group">
              <div className="text-[10px] font-bold uppercase tracking-widest text-white/45 group-hover:text-coral transition-colors">← {prev.no} · {prev.kicker}</div>
              <div className="font-serif-ed text-xl text-white mt-2 leading-snug">{prev.title}</div>
            </Link>
          ) : <div className="hidden md:block" />}
          {next ? (
            <Link href={`/paper/${next.slug}`} className="glass glass-hover rounded-2xl p-6 md:text-right group">
              <div className="text-[10px] font-bold uppercase tracking-widest text-white/45 group-hover:text-coral transition-colors">{next.no} · {next.kicker} →</div>
              <div className="font-serif-ed text-xl text-white mt-2 leading-snug">{next.title}</div>
            </Link>
          ) : (
            /* last section — the paper ends where the product starts */
            <Link href="/invest" className="glass glass-hover rounded-2xl p-6 md:text-right group">
              <div className="text-[10px] font-bold uppercase tracking-widest text-coral">That's the paper →</div>
              <div className="font-serif-ed text-xl text-white mt-2 leading-snug">Now go run it on testnet.</div>
            </Link>
          )}
        </nav>
      </article>
    </Shell>
  )
}
