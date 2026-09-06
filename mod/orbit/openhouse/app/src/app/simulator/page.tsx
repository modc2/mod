/* /simulator — drag the timeline and watch the equity fill up.

   This used to be a widget buried two thirds of the way down the home
   page, with an "expand to fullscreen" button because it never had room.
   A page of its own IS the fullscreen, so the portal, the Escape handler
   and the body-scroll lock all went away with the scroll. */

"use client";

import dynamic from 'next/dynamic'
import Link from 'next/link'
import { useEffect, useRef, useState } from 'react'
import { NextUp, PageHead, Shell } from '../../components/chrome'
import { SplitBar } from '../../components/motion'
import { TermsData, useResource } from '../../lib/api'

function SimField({ label, value, set, min, max, step, unit }: {
  label: string; value: number; set: (n: number) => void; min: number; max: number; step: number; unit: string
}) {
  return (
    <div className="flex-1 min-w-[120px]">
      <label className="text-[10px] uppercase tracking-widest text-white/60 font-bold mb-1.5 block">{label}</label>
      <div className="flex items-center rounded-xl border border-white/10 bg-white/5 focus-within:border-coral/50 transition-colors">
        <input type="number" min={min} max={max} step={step} value={value}
          onChange={e => set(Math.max(min, Math.min(max, parseFloat(e.target.value) || 0)))}
          className="w-full bg-transparent text-white text-sm px-3 py-2.5 font-mono focus:outline-none" />
        <span className="text-white/55 text-xs pr-3 font-mono">{unit}</span>
      </div>
    </div>
  )
}

function SimulatorInner() {
  const { data: terms } = useResource<TermsData | null>('terms', null)
  const feeInit = terms?.fee_pct ?? 2.5
  const creditInit = terms?.credit_pct ?? 100

  const [price, setPrice] = useState(120)     // home price, Ξ
  const [monthly, setMonthly] = useState(2)   // monthly payment, Ξ
  const [apy, setApy] = useState(5)           // lowfi APY, %
  const [feePct, setFeePct] = useState(feeInit)          // protocol take, 1–5%
  const [creditPct, setCreditPct] = useState(creditInit) // of the net payment → principal
  // Follow the live deal until someone drags a slider of their own.
  const touched = useRef(false)
  useEffect(() => { if (!touched.current) { setFeePct(feeInit); setCreditPct(creditInit) } }, [feeInit, creditInit])

  // Seed the home price and payment from the live deal too, once.
  const seeded = useRef(false)
  useEffect(() => {
    if (!terms || seeded.current) return
    seeded.current = true
    if (terms.home_price > 0) setPrice(terms.home_price)
    if (terms.monthly_rent > 0) setMonthly(terms.monthly_rent)
  }, [terms])

  // One month of rent, split the way the contract splits it.
  const perMonthFee = monthly * (feePct / 100)
  const perMonthCredit = (monthly - perMonthFee) * (creditPct / 100)
  const canOwn = perMonthCredit > 0
  // A plain lease never pays it off — cap the timeline at 50 years so the scrubber still works.
  const monthsToOwn = canOwn ? Math.max(1, Math.ceil(price / perMonthCredit)) : 600
  const [month, setMonth] = useState(18)

  useEffect(() => { setMonth(m => Math.min(m, monthsToOwn)) }, [monthsToOwn])

  const principalPaid = Math.min(month * perMonthCredit, price)
  const ownPct = price > 0 ? (principalPaid / price) * 100 : 0
  const remaining = Math.max(price - principalPaid, 0)
  const grossPaid = month * monthly
  const feesPaid = month * perMonthFee
  const ownerIncome = Math.max(grossPaid - feesPaid - principalPaid, 0)

  // Owner's lowfi yield: credited principal compounds at the APY while it sits.
  let bal = 0, yieldEarned = 0
  const r = apy / 100 / 12
  for (let i = 0; i < month; i++) {
    if (i * perMonthCredit < price) bal += Math.min(perMonthCredit, price - i * perMonthCredit)
    const interest = bal * r
    yieldEarned += interest
    bal += interest
  }

  const filled = Math.round(ownPct)
  const owned = ownPct >= 100

  const stats = [
    { v: `${principalPaid.toFixed(1)} Ξ`, c: 'text-white', k: 'your equity' },
    { v: `${remaining.toFixed(1)} Ξ`, c: 'text-emerald-400', k: 'left to own' },
    { v: canOwn ? `${monthsToOwn} mo` : 'never', c: 'text-coral', k: 'to own outright' },
    { v: `${ownerIncome.toFixed(2)} Ξ`, c: 'text-white/80', k: "owner's rent income" },
    { v: `${feesPaid.toFixed(2)} Ξ`, c: 'text-pink', k: `protocol fee · ${feePct}%` },
    { v: `${yieldEarned.toFixed(2)} Ξ`, c: 'text-pink', k: "owner's lowfi yield" },
  ]

  return (
    <Shell>
      <PageHead kicker="Drag the timeline" title={<>Watch your equity<br /><span className="text-surf-grad">fill up.</span></>}>
        Every payment lays a brick. Scrub through the months and watch the house become yours —
        while the owner's idle funds earn lowfi yield.
      </PageHead>

      <div className="max-w-6xl mx-auto px-5 md:px-8">
        <div className="glass rounded-3xl p-6 md:p-9 border-coral/15">
          <div className="flex flex-wrap items-end gap-4 mb-5">
            <SimField label="Home price" value={price} set={setPrice} min={1} max={100000} step={1} unit="Ξ" />
            <SimField label="Monthly payment" value={monthly} set={setMonthly} min={0.01} max={10000} step={0.1} unit="Ξ" />
            <SimField label="lowfi APY" value={apy} set={setApy} min={0} max={50} step={0.5} unit="%" />
            <SimField label="Protocol fee" value={feePct} set={n => { touched.current = true; setFeePct(n) }} min={1} max={5} step={0.1} unit="%" />
            <SimField label="Rent credit" value={creditPct} set={n => { touched.current = true; setCreditPct(n) }} min={0} max={100} step={1} unit="%" />
          </div>

          {/* One month of rent, cut three ways */}
          <SplitBar amount={monthly} fee={perMonthFee} credit={perMonthCredit}
            owner={monthly - perMonthFee - perMonthCredit} className="mb-7" />

          <div className="grid gap-7 items-center lg:grid-cols-[1.6fr_1fr]">
            {/* Brick wall */}
            <div>
              <div className="grid grid-cols-10 gap-2 md:gap-2.5 mb-4">
                {Array.from({ length: 100 }, (_, i) => (
                  <div key={i} className="aspect-[3/2] rounded-[3px] transition-all duration-300"
                    style={{
                      background: i < filled ? 'linear-gradient(135deg,var(--peach),var(--coral))' : 'rgb(var(--ink-rgb) / 0.06)',
                      boxShadow: i < filled ? '0 0 8px rgb(var(--coral-rgb) / 0.45)' : 'none',
                      transitionDelay: `${Math.min(i, 60) * 4}ms`,
                    }} />
                ))}
              </div>
              <input type="range" min={0} max={monthsToOwn} value={month}
                onChange={e => setMonth(parseInt(e.target.value))}
                className="w-full accent-coral cursor-pointer" />
              <div className="flex justify-between text-[11px] text-white/58 mt-1.5 font-mono">
                <span>Move in</span>
                <span className="text-coral font-bold">Month {month} · year {(month / 12).toFixed(1)}</span>
                <span>Owned</span>
              </div>
            </div>

            {/* Live readout */}
            <div className="space-y-4">
              <div>
                <div className="headline text-surf-grad leading-none text-7xl md:text-8xl">{ownPct.toFixed(1)}<span className="text-4xl">%</span></div>
                <div className="text-[11px] uppercase tracking-widest text-white/60 font-bold mt-1">{owned ? 'The house is yours' : 'of the home is yours'}</div>
              </div>
              <div className="grid grid-cols-2 gap-3">
                {stats.map((s, i) => (
                  <div key={i} className="rounded-xl bg-white/[0.03] border border-white/[0.06] p-4">
                    <div className={`font-bold tabular-nums text-xl ${s.c}`}>{s.v}</div>
                    <div className="text-[10px] uppercase tracking-widest text-white/58 mt-0.5">{s.k}</div>
                  </div>
                ))}
              </div>
              <p className="text-[10px] text-white/50 leading-relaxed">
                Illustrative projection from your inputs — not live data or financial advice. Real stakes come straight from the contract.
              </p>
            </div>
          </div>
        </div>

        <p className="text-white/55 text-sm mt-10 text-center">
          {terms && terms.updated > 0
            ? <>Seeded from the live deal on <Link href="/split" className="text-coral hover:underline">the split desk</Link>. Drag anything and it becomes yours.</>
            : <>No deal has been set yet, so these start at the defaults. <Link href="/split" className="text-coral hover:underline">Set the terms →</Link></>}
        </p>
      </div>

      <NextUp here="/simulator" />
    </Shell>
  )
}

export default dynamic(() => Promise.resolve(SimulatorInner), { ssr: false })
