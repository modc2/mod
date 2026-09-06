/* /manifesto — the argument, with nothing else on the page.

   Server-rendered: no fetch, no state, no client bundle beyond the chrome.
   It used to be the first section of a 1,500-line client component that
   pulled the cap table and the whole peer landscape before it could paint. */

import type { Metadata } from 'next'
import Link from 'next/link'
import { NextUp, PageHead, Shell } from '../../components/chrome'
import { ABSTRACT, LAUNCH, MANIFESTO } from '../../lib/whitepaper'

export const metadata: Metadata = {
  title: 'Manifesto — OpenHouse',
  description: 'Rent should buy something. The case for rent-to-own housing written into a contract instead of a company balance sheet.',
}

export default function ManifestoPage() {
  return (
    <Shell>
      <PageHead kicker="The Manifesto" title={<>Rent should<br /><span className="text-surf-grad">buy something.</span></>} />

      <article className="max-w-5xl mx-auto px-5 md:px-8">
        {MANIFESTO.map((line, i) => (
          <p key={i} className={`font-serif-ed text-4xl md:text-6xl leading-[1.05] mb-3 ${
            i === MANIFESTO.length - 1 ? 'text-surf-grad font-black' : 'text-white/60'}`}>
            {line}
          </p>
        ))}

        <p className="text-white/75 text-lg md:text-xl max-w-2xl mt-14 leading-relaxed border-l-2 border-coral/40 pl-6">
          {ABSTRACT}
        </p>

        <div className="glass rounded-3xl p-7 md:p-9 mt-14">
          <div className="text-coral text-[11px] font-bold uppercase tracking-[0.25em] mb-4">What that means in practice</div>
          <ul className="space-y-4 text-white/72 text-base leading-relaxed">
            <li className="flex gap-4">
              <span className="text-coral font-black shrink-0">1–5%</span>
              is all the protocol may take, and the band is a constant in the contract — not a number on a pricing page. <Link href="/split" className="text-coral hover:underline">See the split →</Link>
            </li>
            <li className="flex gap-4">
              <span className="text-coral font-black shrink-0">95–99%</span>
              stays with the property, divided between the renter's equity and the owner's income by a model the owner picks. <Link href="/simulator" className="text-coral hover:underline">Run the numbers →</Link>
            </li>
            <li className="flex gap-4">
              <span className="text-coral font-black shrink-0">0</span>
              other on-chain housing projects credit the person living there. <Link href="/landscape" className="text-coral hover:underline">Check us on that →</Link>
            </li>
          </ul>
          <p className="text-white/50 text-xs mt-7 leading-relaxed">
            {LAUNCH.stage} on {LAUNCH.chain} — mainnet launch {LAUNCH.date.toLowerCase()}. {LAUNCH.notice}
          </p>
        </div>
      </article>

      <NextUp here="/manifesto" />
    </Shell>
  )
}
