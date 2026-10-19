/* The whitepaper's figures — one picture per section, so the argument lands
   without reading a word. Server-rendered markup only: no state, no fetch,
   so the paper pages stay static.

   Color follows the entity, everywhere, matching SplitBar:
     coral/ember = the renter's equity · mint/em-600 = the owner's income ·
     pink = the fee / extraction / what's forbidden · sky = the institution.
   Every fill is direct-labeled and gapped — no figure reads by color alone,
   which is also what lets the eight vibe palettes repaint them safely. */

import { BENCHMARKS } from '../../lib/whitepaper'

/* entity fills, shared across every figure */
const EQUITY = 'linear-gradient(90deg, var(--coral), var(--ember))'
const INCOME = 'linear-gradient(90deg, var(--mint), rgb(var(--em-600-rgb)))'
const FEE = 'linear-gradient(90deg, var(--pink), var(--pink-deep))'

function Fig({ n, caption, children }: { n: string; caption: string; children: React.ReactNode }) {
  return (
    <figure className="glass rounded-2xl p-6 md:p-8 my-12">
      {children}
      <figcaption className="mt-6 pt-4 border-t border-white/10">
        <span className="text-coral text-[10px] font-bold uppercase tracking-[0.25em] mr-3">Figure {n}</span>
        <span className="text-white/60 text-sm">{caption}</span>
      </figcaption>
    </figure>
  )
}

/** One stacked bar: gapped, ringed, and labeled below — never color alone. */
function Stack({ legs, h = 'h-6' }: { legs: { k: string; v: number; bg: string; t: string }[]; h?: string }) {
  const shown = legs.filter(l => l.v > 0)
  return (
    <div>
      <div className={`flex gap-[3px] ${h}`}>
        {shown.map(l => (
          <div key={l.k} title={`${l.k}: ${l.v}%`}
            className="rounded-md ring-1 ring-inset ring-white/15 transition-all duration-500"
            style={{ width: `${l.v}%`, background: l.bg }} />
        ))}
      </div>
      <div className="flex flex-wrap gap-x-5 gap-y-1 mt-2">
        {shown.map(l => (
          <span key={l.k} className="flex items-baseline gap-1.5">
            <span className="w-2 h-2 rounded-full shrink-0 ring-1 ring-inset ring-white/15" style={{ background: l.bg }} />
            <span className={`text-[11px] font-bold tabular-nums ${l.t}`}>{l.v}%</span>
            <span className="text-[10px] uppercase tracking-widest text-white/55">{l.k}</span>
          </span>
        ))}
      </div>
    </div>
  )
}

/* ── 01 · The Problem — ten years of rent, two ledgers ─────────────── */

function FigProblem() {
  return (
    <Fig n="01" caption="Ten years of rent at $2,000 a month. The money is real. The ownership never is.">
      <div className="space-y-6">
        <div>
          <div className="flex items-baseline justify-between mb-2">
            <span className="text-[10px] font-bold uppercase tracking-widest text-white/55">What you paid</span>
            <span className="font-display font-extrabold text-pink tabular-nums">$240,000</span>
          </div>
          <div className="h-6 rounded-md ring-1 ring-inset ring-white/15" style={{ background: FEE }}
            title="120 checks, $240,000 — all of it someone else's asset" />
          <div className="text-[11px] text-white/55 mt-1.5">120 checks → their equity, their appreciation, their write-off</div>
        </div>
        <div>
          <div className="flex items-baseline justify-between mb-2">
            <span className="text-[10px] font-bold uppercase tracking-widest text-white/55">What you own</span>
            <span className="font-display font-extrabold text-white/70 tabular-nums">$0</span>
          </div>
          <div className="h-6 rounded-md border-2 border-dashed border-white/20" title="Ten years in: you own 0% of where you live" />
          <div className="text-[11px] text-white/55 mt-1.5">0% of the place you live — a decade of receipts</div>
        </div>
      </div>
    </Fig>
  )
}

/* ── 02 · The Model — the house fills up as you pay ────────────────── */

function House({ id, pct, label, sub, done = false }: { id: string; pct: number; label: string; sub: string; done?: boolean }) {
  // simple gable house, 100×90: roof peak at (50,4), walls from y=34 down
  const shape = 'M50 4 L96 34 L88 34 L88 88 L12 88 L12 34 L4 34 Z'
  const fillH = 84 * (pct / 100) // house spans y 4..88
  return (
    <div className="flex-1 min-w-[130px] text-center">
      <svg viewBox="0 0 100 90" className="w-full max-w-[150px] mx-auto" role="img"
        aria-label={`House ${pct}% paid off`}>
        <defs>
          <clipPath id={id}><path d={shape} /></clipPath>
          <linearGradient id={`${id}g`} x1="0" y1="1" x2="0" y2="0">
            <stop offset="0%" stopColor="var(--ember)" />
            <stop offset="100%" stopColor="var(--coral)" />
          </linearGradient>
        </defs>
        <path d={shape} fill="rgb(var(--ink-rgb) / 0.06)" />
        <rect x="0" y={88 - fillH} width="100" height={fillH} clipPath={`url(#${id})`} fill={`url(#${id}g)`} />
        <path d={shape} fill="none" stroke="rgb(var(--ink-rgb) / 0.45)" strokeWidth="2.5" strokeLinejoin="round" />
        {/* the paid-off line, so the fill level reads as a measurement */}
        {pct < 100 && (
          <line x1="12" y1={88 - fillH} x2="88" y2={88 - fillH}
            stroke="var(--ember)" strokeWidth="1.5" strokeDasharray="3 3" />
        )}
      </svg>
      <div className={`font-display font-extrabold text-lg tabular-nums mt-2 ${done ? 'text-coral' : 'text-white'}`}>{label}</div>
      <div className="text-[11px] text-white/55 mt-0.5">{sub}</div>
    </div>
  )
}

function FigModel() {
  return (
    <Fig n="02" caption="Every dollar of principal fills the house. Your share is simply principal ÷ price — when it hits 100%, the title transfers.">
      <div className="flex flex-wrap items-end justify-center gap-6">
        <House id="fh1" pct={12} label="12% yours" sub="year 2 — same rent as before" />
        <House id="fh2" pct={55} label="55% yours" sub="year 9 — majority owner" />
        <House id="fh3" pct={100} label="100% — title" sub="paid off, own it outright" done />
      </div>
    </Fig>
  )
}

/* ── 03 · The Take — what the middleman keeps ──────────────────────── */

function FigTake() {
  const max = Math.max(...BENCHMARKS.map(b => b.take))
  const label: Record<string, string> = {
    Airbnb: '~15%', 'Vrbo / Booking': '~13%', 'Property manager': '8–12%', OpenHouse: '0–5%',
  }
  return (
    <Fig n="03" caption="The platform cut, per dollar of rent. Everyone else's number is policy; ours is a constant in the contract.">
      <div className="space-y-3">
        {BENCHMARKS.map(b => (
          <div key={b.name} className="grid grid-cols-[104px_1fr_64px] md:grid-cols-[150px_1fr_76px] items-center gap-3">
            <span className={`text-[11px] font-bold uppercase tracking-widest truncate ${b.ours ? 'text-coral' : 'text-white/55'}`}>{b.name}</span>
            <div className="h-5 rounded-md bg-white/[0.05]" title={`${b.name}: ${b.note}`}>
              <div className={`h-full rounded-md ${b.ours ? '' : 'bg-white/25'}`}
                style={{ width: `${(b.take / max) * 100}%`, ...(b.ours ? { background: EQUITY } : {}) }} />
            </div>
            <span className={`text-sm font-display font-extrabold tabular-nums text-right whitespace-nowrap ${b.ours ? 'text-coral' : 'text-white/70'}`}>
              {label[b.name] ?? `${b.take}%`}
            </span>
          </div>
        ))}
      </div>
    </Fig>
  )
}

/* ── 04 · The Models — four splits, one dial ───────────────────────── */

function FigModels() {
  // illustrated at the 5% cap so the fee is visible; at 0% the pink leg vanishes
  const rows = [
    { name: 'Full credit', note: 'owner earns from yield instead', equity: 95, income: 0 },
    { name: 'Hybrid', note: 'the deal when the owner has a mortgage', equity: 47.5, income: 47.5 },
    { name: 'Lease-option', note: 'the classic 25% rent credit', equity: 24, income: 71 },
    { name: 'Plain lease', note: 'no equity — but no 15% platform either', equity: 0, income: 95 },
  ]
  return (
    <Fig n="04" caption="One rent check under each model, drawn at the 5% fee cap. The split you see quoted is the split that executes.">
      <div className="space-y-5">
        {rows.map(r => (
          <div key={r.name}>
            <div className="flex items-baseline justify-between mb-1.5 gap-3">
              <span className="text-[11px] font-bold uppercase tracking-widest text-white">{r.name}</span>
              <span className="text-[11px] text-white/50 text-right">{r.note}</span>
            </div>
            <Stack h="h-4" legs={[
              { k: 'your equity', v: r.equity, bg: EQUITY, t: 'text-coral' },
              { k: 'owner income', v: r.income, bg: INCOME, t: 'text-emerald-400' },
              { k: 'fee', v: 5, bg: FEE, t: 'text-pink' },
            ]} />
          </div>
        ))}
      </div>
    </Fig>
  )
}

/* ── 05 · Redistribution — the cap table, four quarters in ─────────── */

function FigRedistribution() {
  const quarters = [
    { q: 'Q1', you: 8 }, { q: 'Q2', you: 16 }, { q: 'Q3', you: 24 }, { q: 'Q4', you: 32 },
  ]
  return (
    <Fig n="05" caption="One renter's first year. Every 90 days the contract recomputes the cap table from principal actually paid — no appraisal, no negotiation.">
      <div className="space-y-3">
        {quarters.map(r => (
          <div key={r.q} className="grid grid-cols-[34px_1fr] items-center gap-3">
            <span className="text-[11px] font-bold uppercase tracking-widest text-white/55">{r.q}</span>
            <div className="flex gap-[3px] h-5" title={`${r.q}: you ${r.you}%, owner ${100 - r.you}%`}>
              <div className="rounded-md ring-1 ring-inset ring-white/15 flex items-center justify-end pr-2"
                style={{ width: `${r.you}%`, background: EQUITY }}>
                <span className="text-[10px] font-bold tabular-nums text-onaccent hidden md:inline">{r.you}%</span>
              </div>
              <div className="flex-1 rounded-md ring-1 ring-inset ring-white/15" style={{ background: INCOME, opacity: 0.55 }} />
            </div>
          </div>
        ))}
      </div>
      <div className="flex flex-wrap gap-x-5 gap-y-1 mt-3">
        <span className="flex items-baseline gap-1.5">
          <span className="w-2 h-2 rounded-full ring-1 ring-inset ring-white/15" style={{ background: EQUITY }} />
          <span className="text-[10px] uppercase tracking-widest text-white/55">yours — grows with every payment</span>
        </span>
        <span className="flex items-baseline gap-1.5">
          <span className="w-2 h-2 rounded-full ring-1 ring-inset ring-white/15" style={{ background: INCOME, opacity: 0.55 }} />
          <span className="text-[10px] uppercase tracking-widest text-white/55">still the owner's — for now</span>
        </span>
      </div>
    </Fig>
  )
}

/* ── 06 · The Yield — where one check goes ─────────────────────────── */

function FlowCard({ accent, title, sub }: { accent: string; title: string; sub: string }) {
  return (
    <div className="rounded-xl border border-white/12 bg-white/[0.03] p-4 flex-1">
      <div className="h-1.5 rounded-full mb-3 ring-1 ring-inset ring-white/15" style={{ background: accent }} />
      <div className="font-display font-bold text-white text-sm">{title}</div>
      <div className="text-[11px] text-white/55 mt-1 leading-relaxed">{sub}</div>
    </div>
  )
}

function FigYield() {
  return (
    <Fig n="06" caption="Nobody's money sleeps. You build equity, the pooled cash earns yield, the owner is paid for fronting the asset — and only 0–5% ever leaves.">
      <div className="rounded-xl border border-white/12 bg-white/[0.03] p-4 text-center">
        <div className="text-[10px] font-bold uppercase tracking-widest text-white/55">One rent check</div>
        <div className="font-display font-extrabold text-white text-xl tabular-nums mt-0.5">$2,000</div>
      </div>
      <div className="text-center text-white/40 text-lg leading-none my-2" aria-hidden>↓</div>
      <div className="flex flex-col md:flex-row gap-3">
        <FlowCard accent={EQUITY} title="Your equity"
          sub="Principal, recorded on-chain the moment it lands. This is the part that buys the house." />
        <FlowCard accent={INCOME} title="Pooled cash → lowfi yield → owner"
          sub="Funds in flight earn low-risk on-chain interest. The owner is paid in yield, not in your equity." />
        <FlowCard accent={FEE} title="Protocol fee, 0–5%"
          sub="Owner-set, hard-capped in the contract. The only part that leaves the property." />
      </div>
    </Fig>
  )
}

/* ── 07 · The Trust — dollars in, share of the house out ───────────── */

function FigTrust() {
  const members = [
    { name: 'Ana', paid: '$600', pct: 60, bg: EQUITY },
    { name: 'Bo', paid: '$300', pct: 30, bg: 'var(--sky)' },
    { name: 'Cy', paid: '$100', pct: 10, bg: 'var(--sun)' },
  ]
  return (
    <Fig n="07" caption="Three people, one mortgage. Shares mint one-per-dollar the servicer confirms, so the house divides exactly as the money did — there is no other rule.">
      <div className="grid grid-cols-3 gap-3 mb-5">
        {members.map(m => (
          <div key={m.name} className="rounded-xl border border-white/12 bg-white/[0.03] p-3 text-center">
            <div className="text-[10px] font-bold uppercase tracking-widest text-white/55">{m.name} paid in</div>
            <div className="font-display font-extrabold text-white tabular-nums mt-0.5">{m.paid}</div>
          </div>
        ))}
      </div>
      <div className="text-[10px] font-bold uppercase tracking-widest text-white/55 mb-2">…so the house is owned</div>
      <div className="flex gap-[3px] h-8">
        {members.map(m => (
          <div key={m.name} title={`${m.name}: ${m.paid} of $1,000 → ${m.pct}%`}
            className="rounded-md ring-1 ring-inset ring-white/20 flex items-center justify-center overflow-hidden"
            style={{ width: `${m.pct}%`, background: m.bg }}>
            <span className="text-[10px] font-bold tabular-nums text-onaccent whitespace-nowrap">
              {m.pct >= 20 ? `${m.name} ${m.pct}%` : `${m.pct}%`}
            </span>
          </div>
        ))}
      </div>
      <div className="flex flex-wrap gap-x-5 gap-y-1 mt-2">
        {members.map(m => (
          <span key={m.name} className="flex items-baseline gap-1.5">
            <span className="w-2 h-2 rounded-full shrink-0 ring-1 ring-inset ring-white/15" style={{ background: m.bg }} />
            <span className="text-[10px] uppercase tracking-widest text-white/55">{m.name} — {m.paid} → {m.pct}%</span>
          </span>
        ))}
      </div>
      <p className="text-white/60 text-sm mt-4 text-center font-display">
        your dollars ÷ everyone&apos;s dollars = your share of the house
      </p>
    </Fig>
  )
}

/* ── 08 · The Oracle — escrow until the bank says posted ───────────── */

function FigOracle() {
  const steps = [
    { n: '1', title: 'You wire money', sub: 'into the trust contract', accent: 'rgb(var(--ink-rgb) / 0.3)' },
    { n: '2', title: 'It sits in escrow', sub: 'not equity yet — just held', accent: 'rgb(var(--ink-rgb) / 0.3)' },
    { n: '3', title: 'The oracle confirms', sub: 'the bank’s own feed: “the servicer posted it”', accent: 'var(--sky)' },
    { n: '4', title: 'Shares mint', sub: 'now it’s yours, and the chain says so', accent: EQUITY },
  ]
  return (
    <Fig n="08" caption="A payment becomes equity only when the bank's own signed feed confirms the loan was credited. Until then it's escrow — and escrow has exactly two exits: the servicer, or back to you.">
      <div className="grid md:grid-cols-4 gap-2 items-stretch">
        {steps.map((s, i) => (
          <div key={s.n} className="relative rounded-xl border border-white/12 bg-white/[0.03] p-4">
            <div className="h-1.5 rounded-full mb-3 ring-1 ring-inset ring-white/15" style={{ background: s.accent }} />
            <div className="text-[10px] font-bold uppercase tracking-widest text-white/45">Step {s.n}</div>
            <div className="font-display font-bold text-white text-sm mt-1">{s.title}</div>
            <div className="text-[11px] text-white/55 mt-1 leading-relaxed">{s.sub}</div>
            {i < steps.length - 1 && (
              <span className="hidden md:block absolute top-1/2 -right-[9px] -translate-y-1/2 text-white/35 z-10" aria-hidden>→</span>
            )}
          </div>
        ))}
      </div>
      <div className="rounded-xl border border-pink/25 bg-pink/[0.04] p-3.5 mt-3">
        <span className="text-pink text-[11px] font-bold uppercase tracking-widest mr-2">If step 3 never comes</span>
        <span className="text-white/65 text-sm">the money stays escrowed or returns to you. A stale feed stops the trust taking money at all — oracle liveness is the bank&apos;s problem, not yours.</span>
      </div>
    </Fig>
  )
}

/* ── 09 · The Bank — the levers it holds, the functions that don't exist ── */

function FigBank() {
  const can = ['Appoint the oracle', 'Admit members', 'Freeze the contract', 'Call the default', 'Foreclose on the collateral']
  const cannot = ['Burn your shares — no function', 'Seize your stake — no function', 'Dilute you in a rescue — loans mint nothing', 'Refuse a paid-off discharge — anyone can trigger it']
  return (
    <Fig n="09" caption="Total control is what buys total liability. The bank holds every lever that protects its loan — and the powers that would take your stake were never written into the contract at all.">
      {/* headers and glyphs stay in ink — on GAMEBOY's four greens a sky-tinted
          border or heading vanishes into the field; the ✓/× shapes carry it */}
      <div className="grid md:grid-cols-2 gap-3">
        <div className="rounded-xl border border-white/12 bg-white/[0.03] p-5">
          <div className="h-1.5 rounded-full mb-4 ring-1 ring-inset ring-white/15" style={{ background: 'var(--sky)' }} />
          <div className="text-white text-[11px] font-bold uppercase tracking-widest mb-3">The bank can</div>
          <ul className="space-y-2">
            {can.map(x => (
              <li key={x} className="flex gap-2.5 text-sm text-white/75">
                <span className="text-white/60 font-bold shrink-0">✓</span>{x}
              </li>
            ))}
          </ul>
        </div>
        <div className="rounded-xl border border-white/12 bg-white/[0.03] p-5">
          <div className="h-1.5 rounded-full mb-4 ring-1 ring-inset ring-white/15" style={{ background: FEE }} />
          <div className="text-white text-[11px] font-bold uppercase tracking-widest mb-3">The bank cannot</div>
          <ul className="space-y-2">
            {cannot.map(x => (
              <li key={x} className="flex gap-2.5 text-sm text-white/75">
                <span className="text-white/60 font-bold shrink-0">×</span>{x}
              </li>
            ))}
          </ul>
        </div>
      </div>
    </Fig>
  )
}

/* ── 10 · The City — see, stop, but never take ─────────────────────── */

function FigCity() {
  return (
    <Fig n="10" caption="The civic seat in one line: anyone can see, the city can stop, nobody can take. And a city that wants the whole program just takes the bank seat and sets the fee to zero.">
      <div className="grid md:grid-cols-3 gap-3">
        <div className="rounded-xl border border-white/12 bg-white/[0.03] p-5">
          <div className="h-1.5 rounded-full mb-4 ring-1 ring-inset ring-white/15" style={{ background: 'rgb(var(--ink-rgb) / 0.3)' }} />
          <div className="text-white text-[11px] font-bold uppercase tracking-widest mb-2">Anyone can see</div>
          <p className="text-sm text-white/65 leading-relaxed">Every term, every split of every payment — public, per payment, forever.</p>
        </div>
        <div className="rounded-xl border border-white/12 bg-white/[0.03] p-5">
          <div className="h-1.5 rounded-full mb-4 ring-1 ring-inset ring-white/15" style={{ background: 'var(--sky)' }} />
          <div className="text-white text-[11px] font-bold uppercase tracking-widest mb-2">The city can stop</div>
          <p className="text-sm text-white/65 leading-relaxed">Re-verify every split on its own servers, pause payments, hold a foreclosure for 30 days in the open — powers written as functions.</p>
        </div>
        <div className="rounded-xl border border-white/12 bg-white/[0.03] p-5">
          <div className="h-1.5 rounded-full mb-4 ring-1 ring-inset ring-white/15" style={{ background: FEE }} />
          <div className="text-white text-[11px] font-bold uppercase tracking-widest mb-2">Nobody can take</div>
          <p className="text-sm text-white/65 leading-relaxed">The authority can&apos;t touch a balance, mint a share, or move a cent. A civic pause protects people from the deal — never the deal from its people.</p>
        </div>
      </div>
    </Fig>
  )
}

/* ── the map ───────────────────────────────────────────────────────── */

const FIGURES: Record<string, () => JSX.Element> = {
  'the-problem': FigProblem,
  'rent-to-own': FigModel,
  'the-take': FigTake,
  'the-models': FigModels,
  'redistribution': FigRedistribution,
  'the-yield': FigYield,
  'the-trust': FigTrust,
  'the-oracle': FigOracle,
  'the-bank': FigBank,
  'the-city': FigCity,
}

/** The section's figure, or nothing — a new section without one still ships. */
export function PaperFigure({ slug }: { slug: string }) {
  const F = FIGURES[slug]
  return F ? <F /> : null
}
