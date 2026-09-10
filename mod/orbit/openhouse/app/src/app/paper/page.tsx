/* /paper — the whitepaper's table of contents.

   Six sections, one page each, plus the two reference tables that belong
   with the paper rather than the product: the five numbers and the road. */

import type { Metadata } from 'next'
import Link from 'next/link'
import { NextUp, Shell } from '../../components/chrome'
import { SECTIONS, ABSTRACT, TOKENOMICS, ROADMAP, LAUNCH } from '../../lib/whitepaper'
import { PaperRail } from './chrome'

export const metadata: Metadata = {
  title: 'The Whitepaper — OpenHouse',
  description: 'Rent-to-own, on-chain. Six sections: the problem, the model, the take, the models, redistribution, the yield.',
}

export default function PaperIndex() {
  return (
    <Shell>
      <PaperRail />

      <div className="max-w-5xl mx-auto px-5 md:px-8">
        <header className="pt-16 pb-14 md:pt-24 md:pb-20">
          <div className="text-coral text-[11px] font-bold uppercase tracking-[0.3em] mb-5">The Whitepaper</div>
          <h1 className="headline text-6xl md:text-8xl text-white leading-[0.85]">RENT<br /><span className="text-surf-grad">→ OWN</span></h1>
          <p className="font-serif-ed text-xl md:text-2xl text-white/72 max-w-2xl mt-8 leading-snug border-l-2 border-coral/40 pl-6">{ABSTRACT}</p>
          <p className="text-white/50 text-xs mt-6">
            Six sections · one page each · {LAUNCH.short.toLowerCase()}
          </p>
        </header>

        <div className="grid gap-4 md:grid-cols-2">
          {SECTIONS.map(sec => (
            <Link key={sec.slug} href={`/paper/${sec.slug}`}
              className="glass glass-hover rounded-2xl p-7 flex flex-col group">
              <div className="flex items-baseline gap-4">
                <span className="headline text-5xl text-white/[0.16] leading-none group-hover:text-coral/30 transition-colors">{sec.no}</span>
                <span className="text-coral text-[11px] font-bold uppercase tracking-[0.2em]">{sec.kicker}</span>
              </div>
              <h2 className="font-serif-ed text-2xl md:text-3xl text-white leading-tight mt-4">{sec.title}</h2>
              {sec.pull && <p className="font-serif-ed italic text-white/60 text-base mt-4 leading-snug">“{sec.pull}”</p>}
              <span className="text-[11px] font-bold uppercase tracking-widest text-white/45 group-hover:text-coral transition-colors mt-6">
                Read section {sec.no} →
              </span>
            </Link>
          ))}
        </div>

        <section className="mt-24">
          <h2 className="headline text-3xl text-white mb-8 text-center">The mechanics, in five numbers</h2>
          <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
            {TOKENOMICS.map((t, i) => (
              <div key={i} className="glass rounded-2xl p-6 text-center">
                <div className="text-[10px] uppercase tracking-widest text-white/58 font-bold mb-2">{t.label}</div>
                <div className="font-display font-extrabold text-xl text-coral mb-1">{t.value}</div>
                <div className="text-[11px] text-white/55">{t.note}</div>
              </div>
            ))}
          </div>
          <p className="text-white/55 text-sm mt-6 text-center">
            Or put your own numbers in — <Link href="/simulator" className="text-coral hover:underline">the simulator →</Link>
          </p>
        </section>

        <section className="mt-20">
          <h2 className="headline text-3xl text-white mb-3 text-center">The road to a city you can own</h2>
          <p className="text-white/60 text-sm text-center max-w-lg mx-auto mb-10">
            Where we actually are today: {LAUNCH.stage.toLowerCase()} on {LAUNCH.chain}.
            Mainnet launch date — {LAUNCH.date.toLowerCase()}.
          </p>
          <div className="space-y-3">
            {ROADMAP.map((r, i) => (
              <div key={i} className={`flex items-start gap-5 rounded-2xl p-5 border ${r.done ? 'border-emerald-500/25 bg-emerald-500/[0.04]' : 'border-white/[0.07] bg-white/[0.015]'}`}>
                <div className={`mt-0.5 w-8 h-8 shrink-0 rounded-full flex items-center justify-center text-sm font-black ${r.done ? 'bg-emerald-400 text-onaccent' : 'bg-white/10 text-white/60'}`}>{r.done ? '✓' : i + 1}</div>
                <div className="flex-1">
                  <div className="flex items-center gap-3 flex-wrap">
                    <span className="text-[10px] font-bold uppercase tracking-widest text-coral">{r.phase}</span>
                    <h3 className="font-display font-bold text-white">{r.title}</h3>
                  </div>
                  <p className="text-white/65 text-sm mt-1">{r.detail}</p>
                </div>
              </div>
            ))}
          </div>
        </section>

        <div className="text-center mt-20">
          <Link href="/invest" className="btn-shine inline-block px-9 py-4 rounded-full bg-gradient-to-r from-peach to-coral text-onaccent font-bold uppercase tracking-widest text-sm hover:shadow-2xl hover:shadow-coral/40 transition-shadow">
            Try it on testnet →
          </Link>
        </div>
      </div>

      <NextUp here="/paper" />
    </Shell>
  )
}
