"use client";

import { useMemo, useState } from 'react'
import {
  LockClosedIcon, SparklesIcon, GiftIcon, DocumentTextIcon, ChatBubbleLeftRightIcon,
  ExclamationTriangleIcon, RocketLaunchIcon, BuildingStorefrontIcon, DocumentDuplicateIcon,
} from '@heroicons/react/24/outline'

// The landing page: what BlocTime is, for someone who has never staked
// anything. Plain words, one worked calculator, the honest risks, and three
// ways forward (stake, read the paper, ask the guide). The deep version of
// every claim here lives in whitepaper.md.

const DAY = 86_400
const SPANS: [string, number][] = [
  ['1 day', DAY],
  ['1 week', DAY * 7],
  ['1 month', DAY * 30],
  ['1 year', DAY * 365],
  ['4 years', DAY * 365 * 4],
]

const QUESTIONS = [
  'What is BlocTime, in plain English?',
  'Can I get my money out early?',
  'How does the weekly pot pay out?',
  'What can the owner of a deployment do?',
]

function fmtBig(n: number): string {
  if (!isFinite(n)) return '--'
  if (n >= 1e12) return `${(n / 1e12).toFixed(2)} trillion`
  if (n >= 1e9) return `${(n / 1e9).toFixed(2)} billion`
  if (n >= 1e6) return `${(n / 1e6).toFixed(2)} million`
  return Math.round(n).toLocaleString()
}

export interface IntroLive {
  totalStakes?: number | string
  totalBloc?: string
  network?: string
  priceUsd?: number
}

export default function Intro({ live, onStake, onPaper, onAsk, onDeploy, onMarket }: {
  live: IntroLive | null
  onStake: () => void
  onPaper: () => void
  onAsk: (question?: string) => void
  onDeploy: () => void
  onMarket: () => void
}) {
  const [dollars, setDollars] = useState('100')
  const [span, setSpan] = useState(DAY * 30)
  const bloc = useMemo(() => (Number(dollars) || 0) * span, [dollars, span])
  const spanLabel = SPANS.find(s => s[1] === span)?.[0] || ''

  return (
    <div className="space-y-4 animate-fade-up">
      {/* ── Hero ─────────────────────────────────────────────────────── */}
      <section className="card px-5 py-8 md:px-8 md:py-10">
        <p className="lbl text-accent mb-3">BlocTime</p>
        <h1 className="text-2xl md:text-4xl font-bold text-ink leading-tight max-w-2xl">
          Lock money for a while.<br />Get paid for your patience.
        </h1>
        <p className="mt-4 text-sm md:text-[15px] text-ink2 leading-relaxed max-w-2xl">
          BlocTime turns <strong className="text-ink">time</strong> into a token. You lock tokens for as long as you
          choose, and you get <strong className="text-ink">BLOC</strong> back right away: the more you lock and the
          longer you lock it, the more BLOC. BLOC is your vote, and your share of a reward pot that pays out
          every Friday.
        </p>
        <div className="mt-6 flex flex-wrap gap-2">
          <button onClick={onStake} className="btn btn-accent"><LockClosedIcon className="w-4 h-4" />Start staking</button>
          <button onClick={onPaper} className="btn"><DocumentTextIcon className="w-4 h-4" />Read the whitepaper</button>
          <button onClick={() => onAsk()} className="btn"><ChatBubbleLeftRightIcon className="w-4 h-4" />Ask the guide</button>
        </div>
        {live && (
          <div className="mt-6 flex flex-wrap gap-x-5 gap-y-1 text-[11px] font-mono text-mute">
            <span><span className="chip-dot bg-up inline-block mr-1.5 align-middle animate-pulse-dot" />live on {live.network || 'testnet'}</span>
            {live.totalStakes !== undefined && <span>{live.totalStakes} stakes</span>}
            {live.totalBloc && <span>{live.totalBloc} BLOC minted</span>}
            {live.priceUsd !== undefined && <span>1 token = ${live.priceUsd.toFixed(2)}</span>}
          </div>
        )}
      </section>

      {/* ── How it works ─────────────────────────────────────────────── */}
      <section className="grid gap-3 md:grid-cols-3">
        {([
          [LockClosedIcon, '1. Lock', 'Pick an amount and a length of time — a day, a month, years. Your tokens stay locked until that time is up.'],
          [SparklesIcon, '2. Get BLOC', 'BLOC is minted the moment you lock: dollars locked times seconds locked. Long, big locks get the most.'],
          [GiftIcon, '3. Get paid', 'Every Friday at 17:00 UTC the reward pot is split between everyone holding BLOC, by how much they hold.'],
        ] as [any, string, string][]).map(([Icon, title, body]) => (
          <div key={title} className="card p-4">
            <div className="flex items-center gap-2 mb-2">
              <Icon className="w-4 h-4 text-accent" />
              <span className="text-sm font-bold text-ink">{title}</span>
            </div>
            <p className="text-[12.5px] text-ink2 leading-relaxed">{body}</p>
          </div>
        ))}
      </section>

      {/* ── Calculator ───────────────────────────────────────────────── */}
      <section className="card">
        <div className="card-head"><span className="lbl">Try it</span></div>
        <div className="card-body space-y-3">
          <div className="flex flex-wrap items-center gap-2 text-sm text-ink2">
            <span>If I lock</span>
            <div className="relative">
              <span className="absolute left-2.5 top-1/2 -translate-y-1/2 text-mute text-sm">$</span>
              <input
                className="input w-28 pl-6"
                inputMode="decimal"
                value={dollars}
                onChange={e => setDollars(e.target.value.replace(/[^0-9.]/g, ''))}
                aria-label="Dollars to lock"
              />
            </div>
            <span>for</span>
            <div className="flex flex-wrap gap-1">
              {SPANS.map(([label, s]) => (
                <button key={label} onClick={() => setSpan(s)} className={`btn btn-sm ${span === s ? 'btn-accent' : ''}`}>{label}</button>
              ))}
            </div>
          </div>
          <div className="rounded-lg border border-hair bg-field px-4 py-3">
            <p className="text-xl md:text-2xl font-bold text-gold tabular-nums">{fmtBig(bloc)} BLOC</p>
            <p className="mt-1 text-[11px] font-mono text-mute">
              ${Number(dollars) || 0} x {span.toLocaleString()} seconds ({spanLabel}) x 1 multiplier
            </p>
          </div>
          <p className="text-[11px] text-faint">
            BLOC numbers are big because they count seconds. What matters is your share: if you hold 10% of all
            BLOC, you get 10% of Friday's pot.
          </p>
        </div>
      </section>

      {/* ── Why / fine print ─────────────────────────────────────────── */}
      <section className="grid gap-3 md:grid-cols-2">
        <div className="card p-4">
          <p className="lbl mb-2">Why time?</p>
          <p className="text-[12.5px] text-ink2 leading-relaxed">
            Most staking rewards whoever holds the most right now — someone who shows up the day before a vote
            counts the same as someone who stayed for years. BlocTime pays for <strong className="text-ink">commitment</strong>:
            how much you put in <em>and</em> how long you promise to leave it. One payout a week means you can see the pot,
            the countdown and your share before it pays.
          </p>
        </div>
        <div className="card p-4 border-gold/30">
          <p className="lbl text-gold mb-2 flex items-center gap-1.5"><ExclamationTriangleIcon className="w-3.5 h-3.5" />Before you lock</p>
          <ul className="text-[12.5px] text-ink2 leading-relaxed space-y-1 list-disc pl-4 marker:text-faint">
            <li><strong className="text-ink">No early exit.</strong> Locked means locked until the time is up.</li>
            <li><strong className="text-ink">Testnet.</strong> The official instance runs on Base Sepolia — the tokens have no real value.</li>
            <li><strong className="text-ink">Owners have powers.</strong> A deployment's owner sets the price and the curve; a treasury owner can withdraw its reserve.</li>
            <li>Nothing here is investment advice.</li>
          </ul>
        </div>
      </section>

      {/* ── Run your own ─────────────────────────────────────────────── */}
      <section className="card p-4">
        <p className="lbl mb-2">Anyone can run one</p>
        <p className="text-[12.5px] text-ink2 leading-relaxed mb-3">
          BlocTime is a template, not a platform. Deploy your own copy from your wallet on any EVM chain, list it in
          the market, or fork the whole module — contracts, API and this console.
        </p>
        <div className="flex flex-wrap gap-2">
          <button onClick={onDeploy} className="btn btn-sm"><RocketLaunchIcon className="w-3.5 h-3.5" />Deploy</button>
          <button onClick={onMarket} className="btn btn-sm"><BuildingStorefrontIcon className="w-3.5 h-3.5" />Browse market</button>
          <span className="chip font-mono"><DocumentDuplicateIcon className="w-3.5 h-3.5" />m bloctime/fork name=yours</span>
        </div>
      </section>

      {/* ── Questions → the guide ────────────────────────────────────── */}
      <section className="card p-4">
        <p className="lbl mb-2">Still confused? Ask</p>
        <div className="flex flex-wrap gap-2">
          {QUESTIONS.map(q => (
            <button key={q} onClick={() => onAsk(q)} className="chip hover:border-accent/50 hover:text-accent">{q}</button>
          ))}
        </div>
      </section>
    </div>
  )
}
