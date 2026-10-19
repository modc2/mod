/* /simulator — play the deal, years at a time, through the real code.

   The old page did the arithmetic in the browser. This one asks the node
   to actually run the protocol (POST /simulate): a throwaway store with a
   clock of its own, the owner setting terms, renters paying by sandbox bank
   transfer, the reconciler booking it, the fee pooling, a quarter closing
   every 90 days, and the ledger refusing the first payment past the price.
   Every number here came out of the same functions a live node runs.

   Order on the page:
     1. The knobs     — presets, then the dials; a change re-runs the story.
     2. The readout   — the month under the crosshair: % yours, the tiles.
     3. The chart     — where every Ξ went, month by month; scrub it.
     4. The story     — the moments: option fee, skipped months, quarters, YOURS.
     5. Versus        — the same checks under a plain lease and a 15% platform.
     6. The ledger    — every month and every quarter as a table.
     7. Stories       — the step-by-step walkthroughs (real tool calls).
     8. Any bank · builders. */

"use client";

import dynamic from 'next/dynamic'
import Link from 'next/link'
import { ReactNode, useEffect, useMemo, useRef, useState } from 'react'
import { NextUp, PageHead, Shell } from '../../components/chrome'
import { Reveal, SplitBar } from '../../components/motion'
import { SimChart, SimLegend, VersusBars } from '../../components/simchart'
import { API_URL, BankKind, ExampleMeta, ExampleRun, ModelPreset, SimPreset, SimResult, api, useResource } from '../../lib/api'
import { CurrencyPicker, useCurrency } from '../../lib/currency'
import { STAGE } from '../../lib/whitepaper'

/* ── the knobs ───────────────────────────────────────────── */

interface Knobs {
  years: number; home_price: number; monthly_rent: number; model: string
  fee_pct: number; credit_pct: number | null; renters: number; apy: number; skip_every: number
}
const DEFAULT: Knobs = { years: 20, home_price: 120, monthly_rent: 0.62, model: 'full_credit', fee_pct: 2.5, credit_pct: null, renters: 1, apy: 5, skip_every: 0 }
const SKIPS: [number, string][] = [[0, 'Never'], [24, 'Rarely'], [11, 'Once a year'], [6, 'Often']]

function Field({ label, value, set, min, max, step, unit, hint }: {
  label: string; value: number; set: (n: number) => void; min: number; max: number; step: number; unit?: string; hint?: string | null
}) {
  return (
    <div className="min-w-[7.5rem] flex-1">
      <label className="text-[10px] uppercase tracking-widest text-white/60 font-bold mb-1.5 block">{label}</label>
      <div className="flex items-center rounded-xl border border-white/10 bg-white/5 focus-within:border-coral/50 transition-colors">
        <input type="number" min={min} max={max} step={step} value={value}
          onChange={e => set(Math.max(min, Math.min(max, parseFloat(e.target.value) || 0)))}
          className="w-full bg-transparent text-white text-sm px-3 py-2.5 font-mono focus:outline-none" />
        {unit && <span className="text-white/55 text-xs pr-3 font-mono">{unit}</span>}
      </div>
      {hint && <div className="text-[10px] text-white/50 mt-1 font-mono tabular-nums">≈ {hint}</div>}
    </div>
  )
}

function Select({ label, value, set, options }: { label: string; value: string; set: (v: string) => void; options: [string, string][] }) {
  return (
    <div className="min-w-[8.5rem] flex-1">
      <label className="text-[10px] uppercase tracking-widest text-white/60 font-bold mb-1.5 block">{label}</label>
      <div className="rounded-xl border border-white/10 bg-white/5 focus-within:border-coral/50 transition-colors">
        <select value={value} onChange={e => set(e.target.value)}
          className="w-full bg-transparent text-white text-sm px-3 py-2.5 font-mono focus:outline-none cursor-pointer appearance-none">
          {options.map(([v, l]) => <option key={v} value={v} className="bg-[#16121f] text-white">{l}</option>)}
        </select>
      </div>
    </div>
  )
}

/* ── the readout ─────────────────────────────────────────── */

function Tile({ k, v, sub, c = 'text-white' }: { k: string; v: ReactNode; sub?: string | null; c?: string }) {
  return (
    <div className="rounded-xl bg-white/[0.03] border border-white/[0.06] p-4">
      <div className={`font-display font-extrabold text-xl ${c}`}>{v}</div>
      {sub && <div className="text-[11px] text-white/60 font-mono tabular-nums mt-0.5">≈ {sub}</div>}
      <div className="text-[10px] uppercase tracking-widest text-white/58 mt-0.5">{k}</div>
    </div>
  )
}

/** The house, as a hundred bricks — the readout's small picture. */
function House({ pct }: { pct: number }) {
  const filled = Math.round(pct)
  return (
    <div className="grid grid-cols-10 gap-1 w-[8.5rem]" aria-hidden>
      {Array.from({ length: 100 }, (_, i) => (
        <div key={i} className="aspect-[3/2] rounded-[2px] transition-colors duration-300"
          style={{ background: i < filled ? 'linear-gradient(135deg,var(--peach),var(--coral))' : 'rgb(var(--ink-rgb) / 0.07)', transitionDelay: `${Math.min(i, 60) * 3}ms` }} />
      ))}
    </div>
  )
}

/* ── the walkthroughs (ported from the old sandbox page) ──── */

const LEVEL: Record<string, string> = { start: 'The basics', bank: 'Banks', civic: 'Cities' }

function Json({ value }: { value: any }) {
  return (
    <pre className="text-[11px] leading-relaxed text-white/70 bg-black/30 rounded-lg p-3 overflow-x-auto max-h-80 whitespace-pre-wrap break-all">
      {JSON.stringify(value, null, 2)}
    </pre>
  )
}

function Transcript({ run }: { run: ExampleRun }) {
  return (
    <div className="glass rounded-3xl p-6 md:p-8">
      <div className="flex flex-wrap items-baseline justify-between gap-3 mb-6">
        <h3 className="font-serif-ed text-2xl text-white">{run.title}</h3>
        <span className={`text-[11px] font-bold uppercase tracking-widest px-3 py-1 rounded-full border ${run.ok ? 'text-emerald-400 border-emerald-500/30' : 'text-pink border-pink/40'}`}>
          {run.ok ? 'Passed' : 'Failed'} · {run.summary}
        </span>
      </div>
      {run.checks.length > 0 && (
        <ul className="grid gap-2 md:grid-cols-2 mb-8">
          {run.checks.map((c, i) => (
            <li key={i} className="flex gap-2 text-sm text-white/80">
              <span className={c.ok ? 'text-emerald-400 font-bold' : 'text-pink font-bold'}>{c.ok ? '✓' : '×'}</span>
              <span>{c.claim}</span>
            </li>
          ))}
        </ul>
      )}
      {run.error && <p className="text-pink text-sm mb-6">{run.error}</p>}
      <ol className="space-y-3">
        {run.steps.map(s => (
          <li key={s.n} className="rounded-xl border border-white/[0.08] p-4">
            <div className="flex flex-wrap items-center gap-3">
              <span className="text-white/40 text-xs font-bold w-6">{s.n}</span>
              <span className="text-white text-sm flex-1 min-w-[12rem]">{s.note}</span>
              <code className="text-[10px] text-coral bg-coral/10 rounded px-2 py-0.5">{s.tool}</code>
              {s.error && <span className="text-[10px] font-bold uppercase tracking-widest text-pink">refused</span>}
            </div>
            <details className="mt-2 ml-9">
              <summary className="text-[11px] text-white/50 cursor-pointer hover:text-white/80">details</summary>
              <div className="grid gap-3 md:grid-cols-2 mt-2">
                <div><div className="text-[10px] uppercase tracking-widest text-white/40 mb-1">arguments</div><Json value={s.args} /></div>
                <div>
                  <div className="text-[10px] uppercase tracking-widest text-white/40 mb-1">{s.error ? 'refused with' : 'result'}</div>
                  {s.error ? <p className="text-sm text-pink">{s.error}</p> : <Json value={s.result} />}
                </div>
              </div>
            </details>
          </li>
        ))}
      </ol>
      <p className="text-[11px] text-white/45 mt-6 leading-relaxed">{run.store}. {run.replay}.</p>
    </div>
  )
}

function ReferenceLookup() {
  const [addr, setAddr] = useState('')
  const [code, setCode] = useState<string | null>(null)
  const [err, setErr] = useState('')
  const look = async () => {
    setErr(''); setCode(null)
    try { setCode((await api(`bank/reference/${encodeURIComponent(addr.trim())}`)).reference) }
    catch (e: any) { setErr(e?.message || 'lookup failed') }
  }
  return (
    <div className="flex flex-wrap gap-2 items-center">
      <input value={addr} onChange={e => setAddr(e.target.value)} placeholder="your 0x address"
        className="flex-1 min-w-[14rem] bg-white/[0.04] border border-white/12 rounded-xl px-4 py-3 text-sm text-white placeholder-white/35 focus:outline-none focus:border-coral/60" />
      <button onClick={look} disabled={!addr.trim()}
        className="px-5 py-3 rounded-xl bg-coral text-onaccent text-xs font-bold uppercase tracking-widest disabled:opacity-30">
        Get my code
      </button>
      {code && <div className="w-full text-sm text-white/80 mt-2">Put <code className="text-coral font-bold text-base">{code}</code> in your transfer memo.</div>}
      {err && <div className="w-full text-sm text-pink mt-2">{err}</div>}
    </div>
  )
}

/* ── the page ────────────────────────────────────────────── */

function SimulatorInner() {
  const { data: presets } = useResource<SimPreset[]>('simulate/presets', [])
  const { data: modelsData } = useResource<{ models: ModelPreset[] } | null>('models', null)
  const { data: examples } = useResource<ExampleMeta[]>('examples', [])
  const { data: kinds } = useResource<BankKind[]>('bank/kinds', [])
  const { code, setCode, fiat, rate, approximate } = useCurrency()
  const models = modelsData?.models ?? []

  const [knobs, setKnobs] = useState<Knobs>(DEFAULT)
  const [preset, setPreset] = useState<string | null>('first_home')
  const [sim, setSim] = useState<SimResult | null>(null)
  const [busy, setBusy] = useState(true)
  const [err, setErr] = useState('')
  const [month, setMonth] = useState(0)
  const runId = useRef(0)

  const set = (patch: Partial<Knobs>) => { setPreset(null); setKnobs(k => ({ ...k, ...patch })) }
  const pickPreset = (p: SimPreset) => {
    setPreset(p.id)
    setKnobs(k => ({ ...k, years: p.years, model: p.model, fee_pct: p.fee_pct, renters: p.renters, apy: p.apy, skip_every: p.skip_every ?? 0, credit_pct: null }))
  }

  // Run it. Debounced, and a stale answer never overwrites a newer one; the
  // previous render stays on screen at reduced opacity while the node works.
  useEffect(() => {
    const id = ++runId.current
    setBusy(true)
    const t = setTimeout(async () => {
      try {
        const body: any = { ...knobs, rate: rate ?? 2500, currency: code, bank: true }
        if (body.credit_pct == null) delete body.credit_pct
        const r: SimResult = await api('simulate', { method: 'POST', body })
        if (id !== runId.current) return
        setSim(r); setErr('')
        setMonth(m => (m === 0 || m > r.months.length ? r.months.length : m))
      } catch (e: any) {
        if (id === runId.current) setErr(e?.message || 'the node did not answer')
      }
      if (id === runId.current) setBusy(false)
    }, 350)
    return () => clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [knobs, rate, code])

  const rows = sim?.months ?? []
  const cur = rows[Math.min(Math.max(month, 1), rows.length) - 1]
  const terms = sim?.terms
  const monthly = knobs.monthly_rent
  const fee = monthly * ((terms?.fee_pct ?? knobs.fee_pct) / 100)
  const credit = (monthly - fee) * ((terms?.credit_pct ?? 100) / 100)
  const ownedAt = sim?.end.owned_month ?? null

  const events = useMemo(() => {
    if (!sim) return []
    // Every quarter close in twenty years is 80 lines; keep the first few, the
    // skips, the option fee and the ending, and count the rest.
    const q = sim.events.filter(e => e.kind === 'quarter')
    const keep = sim.events.filter(e => e.kind !== 'quarter' || q.indexOf(e) < 2)
    return { list: keep, moreQuarters: Math.max(q.length - 2, 0) } as any
  }, [sim]) as { list: SimResult['events']; moreQuarters: number } | never[]
  const ev = Array.isArray(events) ? { list: [], moreQuarters: 0 } : events

  const [run, setRun] = useState<ExampleRun | null>(null)
  const [playing, setPlaying] = useState('')
  const play = async (name: string) => {
    setPlaying(name)
    try { setRun(await api(`examples/${name}`)) }
    catch (e: any) { setRun({ name, title: name, shows: '', level: '', ok: false, error: e?.message || 'failed', summary: '', store: '', replay: '', steps: [], checks: [] }) }
    setPlaying('')
    setTimeout(() => document.getElementById('transcript')?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 50)
  }

  return (
    <Shell>
      <PageHead kicker="Simulation · real code, fake money" title={<>Play the deal.<br /><span className="text-surf-grad">Twenty years in two seconds.</span></>}>
        This is not a spreadsheet of the deal — it is the deal, played. The node runs the real protocol in a throwaway store with
        a clock of its own: rent arrives by bank transfer, the reconciler books it, the fee pools, a quarter closes every ninety days,
        and the ledger refuses the first payment past the price.
      </PageHead>

      <div className="max-w-6xl mx-auto px-5 md:px-8 space-y-8">
        {/* 1 — the knobs, one row above everything they scope */}
        <Reveal>
          <div className="glass rounded-3xl p-6 md:p-8 border-coral/15">
            <div className="flex flex-wrap gap-2 mb-6">
              {presets.map(p => (
                <button key={p.id} onClick={() => pickPreset(p)} title={p.shows}
                  className={`px-3.5 py-1.5 rounded-full text-[11px] font-bold uppercase tracking-widest border transition-colors ${
                    preset === p.id ? 'bg-coral text-onaccent border-coral' : 'border-white/12 text-white/65 hover:text-coral hover:border-coral/40'}`}>
                  {p.title}
                </button>
              ))}
            </div>
            <div className="flex flex-wrap items-start gap-3">
              <Field label="Years" value={knobs.years} set={n => set({ years: Math.round(n) })} min={1} max={40} step={1} />
              <Field label="Home price" value={knobs.home_price} set={n => set({ home_price: n })} min={0.01} max={100000} step={1} unit="Ξ" hint={fiat(knobs.home_price)} />
              <Field label="Monthly rent" value={knobs.monthly_rent} set={n => set({ monthly_rent: n })} min={0.001} max={10000} step={0.01} unit="Ξ" hint={fiat(knobs.monthly_rent)} />
              <Select label="Model" value={knobs.model} set={v => set({ model: v, credit_pct: null })}
                options={models.length ? models.map(m => [m.id, `${m.name} · ${m.credit_pct}%`]) : [['full_credit', 'Full credit'], ['hybrid', 'Hybrid'], ['classic', 'Classic'], ['lease', 'Lease']]} />
              <Field label="Protocol fee" value={knobs.fee_pct} set={n => set({ fee_pct: n })} min={0} max={5} step={0.1} unit="%" />
              <Field label="Rent credit" value={knobs.credit_pct ?? terms?.credit_pct ?? 100} set={n => set({ credit_pct: n })} min={0} max={100} step={1} unit="%" />
              <Field label="Renters" value={knobs.renters} set={n => set({ renters: Math.round(n) })} min={1} max={4} step={1} />
              <Field label="lowfi APY" value={knobs.apy} set={n => set({ apy: n })} min={0} max={50} step={0.5} unit="%" />
              <Select label="Missed months" value={String(knobs.skip_every)} set={v => set({ skip_every: parseInt(v) })} options={SKIPS.map(([v, l]) => [String(v), l])} />
              <CurrencyPicker code={code} setCode={setCode} />
            </div>
            <div className="mt-5">
              <SplitBar amount={monthly} fee={fee} credit={credit} owner={monthly - fee - credit} />
            </div>
          </div>
        </Reveal>

        {err && <p className="text-pink text-sm">The node is not answering ({err}).</p>}

        {/* 2 + 3 — the readout and the chart, as one card */}
        <Reveal delay={60}>
          <div className={`glass rounded-3xl p-6 md:p-8 transition-opacity duration-300 ${busy ? 'opacity-60' : 'opacity-100'}`}>
            <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_19rem]">
              <div className="min-w-0">
                <div className="flex items-baseline justify-between flex-wrap gap-3 mb-4">
                  <div>
                    <div className="text-[11px] font-bold uppercase tracking-[0.25em] text-coral">Where every Ξ went</div>
                    <div className="text-white/55 text-xs mt-1">
                      {sim ? `${sim.end.months_run} months played · ${sim.bank?.booked ?? sim.end.payments} bank transfers booked · ${sim.end.quarters_closed} quarters closed · ${sim.elapsed_ms} ms` : 'running…'}
                    </div>
                  </div>
                  <SimLegend hasSkips={rows.some(r => r.skipped.length > 0)} hasQuarters={(sim?.quarters.length ?? 0) > 0} />
                </div>
                <SimChart rows={rows} price={sim?.terms.home_price ?? knobs.home_price} month={month} setMonth={setMonth}
                  quarters={sim?.quarters ?? []} ownedMonth={ownedAt} fiat={fiat} />
                <input type="range" min={1} max={Math.max(rows.length, 1)} value={Math.min(Math.max(month, 1), Math.max(rows.length, 1))}
                  onChange={e => setMonth(parseInt(e.target.value))} aria-label="Month"
                  className="w-full accent-coral cursor-pointer mt-2" />
                <div className="flex justify-between text-[11px] text-white/58 font-mono">
                  <span>Move in</span>
                  <span className="text-coral font-bold">{cur ? `Month ${cur.m} · year ${(cur.m / 12).toFixed(1)} · ${String(cur.period).slice(0, 4)}-${String(cur.period).slice(4)}` : ''}</span>
                  <span>{ownedAt ? 'Yours' : `Year ${knobs.years}`}</span>
                </div>
              </div>

              <div className="space-y-4">
                <div className="flex items-end gap-4">
                  <div>
                    <div className="headline text-surf-grad leading-none text-6xl md:text-7xl">{(cur?.owned_pct ?? 0).toFixed(1)}<span className="text-3xl">%</span></div>
                    <div className="text-[11px] uppercase tracking-widest text-white/60 font-bold mt-1.5">
                      {cur && cur.owned_pct >= 100 ? 'The house is yours' : 'of the home is yours'}
                    </div>
                  </div>
                  <House pct={cur?.owned_pct ?? 0} />
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <Tile k="your equity" v={`${(cur?.equity ?? 0).toFixed(1)} Ξ`} sub={cur ? fiat(cur.equity) : null} c="text-white" />
                  <Tile k="left to own" v={`${(cur?.remaining ?? knobs.home_price).toFixed(1)} Ξ`} sub={cur ? fiat(cur.remaining) : null} c="text-emerald-400" />
                  <Tile k={ownedAt ? 'owned outright at' : 'owned outright'} v={ownedAt ? `${ownedAt} mo` : credit > 0 ? `${Math.ceil(knobs.home_price / credit)} mo` : 'never'} c="text-coral" />
                  <Tile k="owner's rent income" v={`${(cur?.owner_income ?? 0).toFixed(2)} Ξ`} sub={cur ? fiat(cur.owner_income) : null} c="text-white/80" />
                  <Tile k={`protocol fee · ${terms?.fee_pct ?? knobs.fee_pct}%`} v={`${(cur?.fees ?? 0).toFixed(2)} Ξ`} sub={cur ? fiat(cur.fees) : null} c="text-pink" />
                  <Tile k="owner's lowfi yield · modelled" v={`${(cur?.yield ?? 0).toFixed(2)} Ξ`} sub={cur ? fiat(cur.yield) : null} c="text-pink" />
                </div>
                <p className="text-[10px] text-white/50 leading-relaxed">
                  {STAGE.claim} Yield and the fiat rate are modelled; everything else was played.
                  {rate && <span className="block mt-1 font-mono tabular-nums">1 Ξ ≈ {fiat(1)}{approximate ? ' · approximate' : ''}</span>}
                </p>
              </div>
            </div>
          </div>
        </Reveal>

        {/* 4 — the moments */}
        {sim && (
          <Reveal delay={80}>
            <div className="grid gap-6 lg:grid-cols-[1fr_1fr]">
              <div className="glass rounded-3xl p-6 md:p-8">
                <div className="text-[11px] font-bold uppercase tracking-[0.25em] text-white/60 mb-4">What happened</div>
                <ol className="space-y-2.5">
                  {ev.list.map((e, i) => (
                    <li key={i} className="flex gap-3 text-sm">
                      <button onClick={() => setMonth(Math.max(e.month, 1))} className="shrink-0 w-14 text-left font-mono text-[11px] text-white/45 hover:text-coral tabular-nums">mo {e.month}</button>
                      <span className={e.kind === 'owned' ? 'text-coral font-bold' : e.kind === 'skipped' ? 'text-white/70' : 'text-white/80'}>{e.says}</span>
                    </li>
                  ))}
                  {ev.moreQuarters > 0 && (
                    <li className="flex gap-3 text-sm"><span className="w-14" /><span className="text-white/50">… and {ev.moreQuarters} more quarters closed, each splitting the pool by bloctime.</span></li>
                  )}
                  {!sim.end.owned && (
                    <li className="flex gap-3 text-sm"><span className="shrink-0 w-14 font-mono text-[11px] text-white/45">end</span>
                      <span className="text-white/80">{knobs.years} years in, {sim.end.owned_pct.toFixed(1)}% of the home is paid for. {credit > 0 ? 'Keep going.' : 'A plain lease never gets there.'}</span></li>
                  )}
                </ol>
              </div>

              {/* 5 — versus */}
              <div className="glass rounded-3xl p-6 md:p-8">
                <div className="text-[11px] font-bold uppercase tracking-[0.25em] text-white/60 mb-1">The same checks, somewhere else</div>
                <p className="text-white/55 text-xs mb-5">{sim.end.gross_paid.toFixed(1)} Ξ paid over {sim.end.months_run} months{fiat(sim.end.gross_paid) ? ` · ≈ ${fiat(sim.end.gross_paid)}` : ''}. Headline rates, not a quote.</p>
                <VersusBars gross={sim.end.gross_paid} equity={sim.end.equity} ownerIncome={sim.end.owner_income} fees={sim.end.fees} />
                <div className="grid grid-cols-3 gap-3 mt-6">
                  {[
                    ['OpenHouse', sim.versus.openhouse],
                    ['15% platform', sim.versus.platform_15],
                    ['Plain lease', sim.versus.plain_lease],
                  ].map(([name, v]: any) => (
                    <div key={name} className="rounded-xl bg-white/[0.03] border border-white/[0.06] p-3">
                      <div className="text-[10px] uppercase tracking-widest text-white/50">{name}</div>
                      <div className="font-display font-extrabold text-white mt-1">{v.equity.toFixed(1)} Ξ</div>
                      <div className="text-[10px] text-white/50">equity · {v.platform_take.toFixed(2)} Ξ taken</div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </Reveal>
        )}

        {/* 6 — the ledger, as tables */}
        {sim && (
          <Reveal delay={100}>
            <div className="grid gap-4 md:grid-cols-2">
              <details className="glass rounded-2xl p-5">
                <summary className="cursor-pointer text-white font-bold text-sm">Every month, as a table ({rows.length})</summary>
                <div className="overflow-x-auto mt-4 max-h-96 overflow-y-auto">
                  <table className="w-full text-[11px] tabular-nums">
                    <thead className="text-white/50 uppercase tracking-widest text-[9px]">
                      <tr>{['mo', 'period', 'paid', 'equity', 'owner', 'fee', 'yours'].map(h => <th key={h} className="text-right first:text-left py-1 pr-3">{h}</th>)}</tr>
                    </thead>
                    <tbody className="text-white/80">
                      {rows.map(r => (
                        <tr key={r.m} className={`border-t border-white/[0.05] ${r.m === cur?.m ? 'text-coral' : ''} ${r.skipped.length ? 'italic' : ''}`}>
                          <td className="py-1 pr-3 text-left">{r.m}</td><td className="py-1 pr-3 text-right">{r.period}</td>
                          <td className="py-1 pr-3 text-right">{r.paid.toFixed(2)}</td><td className="py-1 pr-3 text-right">{r.equity.toFixed(2)}</td>
                          <td className="py-1 pr-3 text-right">{r.owner_income.toFixed(2)}</td><td className="py-1 pr-3 text-right">{r.fees.toFixed(3)}</td>
                          <td className="py-1 pr-3 text-right">{r.owned_pct.toFixed(1)}%</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </details>
              <details className="glass rounded-2xl p-5">
                <summary className="cursor-pointer text-white font-bold text-sm">Every quarter the protocol closed ({sim.quarters.length})</summary>
                {sim.quarters.length === 0 ? (
                  <p className="text-sm text-white/60 mt-3">No fee, no pool, nothing to split — the owner took 0%.</p>
                ) : (
                  <div className="overflow-x-auto mt-4 max-h-96 overflow-y-auto">
                    <table className="w-full text-[11px] tabular-nums">
                      <thead className="text-white/50 uppercase tracking-widest text-[9px]">
                        <tr>{['q', 'closed', 'pool Ξ', 'to owner', 'to renters'].map(h => <th key={h} className="text-right first:text-left py-1 pr-3">{h}</th>)}</tr>
                      </thead>
                      <tbody className="text-white/80">
                        {sim.quarters.map(q => {
                          const owner = q.allocations.find(a => a.who === 'owner')
                          const renters = q.allocations.filter(a => a.who !== 'owner').reduce((s, a) => s + a.share_pct, 0)
                          return (
                            <tr key={q.quarter} className="border-t border-white/[0.05]">
                              <td className="py-1 pr-3">{q.quarter}</td><td className="py-1 pr-3 text-right">mo {q.month}</td>
                              <td className="py-1 pr-3 text-right">{q.pool.toFixed(4)}</td>
                              <td className="py-1 pr-3 text-right">{(owner?.share_pct ?? 0).toFixed(1)}%</td>
                              <td className="py-1 pr-3 text-right">{renters.toFixed(1)}%</td>
                            </tr>
                          )
                        })}
                      </tbody>
                    </table>
                    <p className="text-[11px] text-white/50 mt-3 leading-relaxed">
                      Each close splits that quarter's fees by bloctime — Ξ × seconds locked. Early on the owner holds nearly the whole
                      house, so nearly the whole pool; as principal moves across, so does the pool.
                    </p>
                  </div>
                )}
              </details>
            </div>
            <p className="text-white/50 text-xs mt-4 leading-relaxed">
              <span className="text-coral font-bold uppercase tracking-widest">Played — </span>{sim.played.join(' · ')}.
              <span className="text-pink font-bold uppercase tracking-widest"> Modelled — </span>{sim.modeled.join('; ')}. {sim.store}.
            </p>
          </Reveal>
        )}

        {/* 7 — the stories */}
        <section id="stories" className="pt-12 scroll-mt-24">
          <h2 className="headline text-4xl md:text-5xl text-white mb-3">Step by step.</h2>
          <p className="font-serif-ed text-lg text-white/68 mb-8 max-w-3xl">
            Nine short stories, each told entirely in tool calls — pick one, press Run, read every step the node took and what it refused.
          </p>
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
            {examples.map((e, i) => (
              <Reveal key={e.name} delay={i * 30}>
                <div className="glass glass-hover rounded-2xl p-5 h-full flex flex-col">
                  <div className="text-[10px] font-bold uppercase tracking-widest text-white/45 mb-2">{LEVEL[e.level] || e.level}</div>
                  <h3 className="font-display font-bold text-white text-base mb-2">{e.title}</h3>
                  <p className="text-sm text-white/65 leading-snug flex-1">{e.shows}</p>
                  <button onClick={() => play(e.name)} disabled={!!playing}
                    className="mt-4 w-full py-2.5 rounded-xl bg-gradient-to-r from-peach to-coral text-onaccent text-xs font-bold uppercase tracking-widest disabled:opacity-40">
                    {playing === e.name ? 'Running…' : 'Run'}
                  </button>
                </div>
              </Reveal>
            ))}
          </div>
          <div id="transcript" className="mt-8 scroll-mt-24">{run && <Transcript run={run} />}</div>
        </section>

        {/* 8 — any bank, builders */}
        <section className="pt-12 grid gap-6 lg:grid-cols-2">
          <div className="glass rounded-3xl p-6 md:p-8">
            <h2 className="headline text-3xl text-white mb-2">Pay rent from any bank.</h2>
            <p className="text-sm text-white/65 leading-snug mb-5">
              No wallet needed. Every renter gets a short code; send rent the way you already do, with the code in the memo,
              and the owner&apos;s node books it to you — once. That is exactly what the simulation above did {sim?.bank?.booked ?? ''} times.
            </p>
            <ReferenceLookup />
            <details className="mt-5">
              <summary className="cursor-pointer text-white/70 text-xs font-bold uppercase tracking-widest">The {kinds.length} kinds of bank the node can read</summary>
              <ul className="mt-3 space-y-2">
                {kinds.map(k => (
                  <li key={k.kind} className="text-[13px] text-white/65 leading-snug"><span className="text-white font-bold">{k.label}</span> — {k.doc}</li>
                ))}
              </ul>
            </details>
          </div>
          <div className="glass rounded-3xl p-6 md:p-8">
            <h2 className="headline text-3xl text-white mb-2">For builders.</h2>
            <p className="text-sm text-white/65 leading-snug mb-4">The simulation is one call. So is every story above.</p>
            <pre className="text-[11px] text-white/75 bg-black/30 rounded-lg p-3 overflow-x-auto mb-3">{`GET ${API_URL}/simulate?years=20&model=hybrid&renters=3

POST ${API_URL}/mcp
{"jsonrpc":"2.0","id":1,"method":"tools/call",
 "params":{"name":"openhouse_simulate",
           "arguments":{"preset":"housemates","months":true}}}`}</pre>
            <p className="text-sm text-white/65 leading-snug mb-2">On a real EVM, with zero setup — a local anvil chain:</p>
            <pre className="text-[11px] text-white/75 bg-black/30 rounded-lg p-3 overflow-x-auto">{`cd contracts && ./localchain.sh`}</pre>
            <Link href="/code" className="inline-block mt-4 text-[11px] font-bold uppercase tracking-widest text-coral hover:underline">Read the source →</Link>
          </div>
        </section>
      </div>

      <NextUp here="/simulator" />
    </Shell>
  )
}

export default dynamic(() => Promise.resolve(SimulatorInner), { ssr: false })
