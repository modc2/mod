/* What a Ξ is worth, in the reader's own money.

   One hook + one picker, shared by any page that shows an amount of Ξ.
   Rates come from the module's own /fx rail (keyless CoinGecko behind a
   15-minute cache with offline fallbacks), the chosen currency lives in
   localStorage, and formatting is the browser's own Intl — no library,
   no key, nothing that stops working when an upstream does. */

"use client";

import { useEffect, useState } from 'react'
import { useResource } from './api'

export interface FxData {
  rates: Record<string, number>
  currencies: Record<string, { symbol: string; name: string; decimals?: number }>
  base: string
  fetched: number
  source: 'live' | 'cache' | 'stale' | 'fallback'
}

/* The picker must render before (or without) the API, so the menu is baked
   in here too; live rates simply light the numbers up when they arrive. */
export const CURRENCY_MENU: [string, string][] = [
  ['usd', 'US Dollar'], ['eur', 'Euro'], ['gbp', 'British Pound'],
  ['jpy', 'Japanese Yen'], ['cad', 'Canadian Dollar'], ['aud', 'Australian Dollar'],
  ['chf', 'Swiss Franc'], ['cny', 'Chinese Yuan'], ['inr', 'Indian Rupee'],
  ['brl', 'Brazilian Real'], ['mxn', 'Mexican Peso'], ['krw', 'South Korean Won'],
]

const LS_KEY = 'openhouse_currency'

function format(amount: number, code: string) {
  try {
    return new Intl.NumberFormat(undefined, {
      style: 'currency', currency: code.toUpperCase(), currencyDisplay: 'narrowSymbol',
      maximumFractionDigits: Math.abs(amount) >= 100 ? 0 : 2,
    }).format(amount)
  } catch {
    return `${amount.toLocaleString(undefined, { maximumFractionDigits: 0 })} ${code.toUpperCase()}`
  }
}

export function useCurrency() {
  const { data: fx } = useResource<FxData | null>('fx', null)
  const [code, setCodeState] = useState('usd')

  useEffect(() => {
    try {
      const saved = localStorage.getItem(LS_KEY)
      if (saved && CURRENCY_MENU.some(([c]) => c === saved)) setCodeState(saved)
    } catch { /* private mode — USD is fine */ }
  }, [])

  const setCode = (c: string) => {
    setCodeState(c)
    try { localStorage.setItem(LS_KEY, c) } catch { /* same */ }
  }

  const rate = fx?.rates?.[code] ?? null

  /** Ξ → the chosen currency, formatted. Null while rates are loading. */
  const fiat = (eth: number) => (rate == null ? null : format(eth * rate, code))

  return { code, setCode, rate, fiat, fx, approximate: !fx || fx.source === 'fallback' || fx.source === 'stale' }
}

/** A select dressed like the simulator's input fields. */
export function CurrencyPicker({ code, setCode, className = '' }: {
  code: string; setCode: (c: string) => void; className?: string
}) {
  return (
    <div className={`min-w-[120px] ${className}`}>
      <label className="text-[10px] uppercase tracking-widest text-white/60 font-bold mb-1.5 block">Currency</label>
      <div className="rounded-xl border border-white/10 bg-white/5 focus-within:border-coral/50 transition-colors">
        <select value={code} onChange={e => setCode(e.target.value)}
          className="w-full bg-transparent text-white text-sm px-3 py-2.5 font-mono focus:outline-none cursor-pointer appearance-none">
          {CURRENCY_MENU.map(([c, name]) => (
            <option key={c} value={c} className="bg-[#16121f] text-white">{c.toUpperCase()} · {name}</option>
          ))}
        </select>
      </div>
    </div>
  )
}
