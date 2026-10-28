'use client'

import { useEffect, useMemo, useState } from 'react'
import {
  api, REPORT_URL, type CrimeSummary, type MarketSummary, type NewsFeed,
} from '@/lib/api'

/**
 * The city's vital signs, in the rail: crime and the listing market as four
 * stat tiles, then the latest headlines from NYC newsrooms with a topic
 * filter. Everything here is fetched once when the section is first opened —
 * the numbers behind the tiles move daily at most, and the news endpoint is
 * itself cached a quarter hour server-side.
 */
export default function PulsePanel() {
  const [news, setNews] = useState<NewsFeed | null>(null)
  const [crime, setCrime] = useState<CrimeSummary | null>(null)
  const [market, setMarket] = useState<MarketSummary | null>(null)
  const [failed, setFailed] = useState(false)
  const [topic, setTopic] = useState('')

  useEffect(() => {
    let alive = true
    // Three independent reads: one failing must not blank the other two.
    api.news({ limit: 60 }).then((n) => alive && setNews(n)).catch(() => setFailed(true))
    api.crime().then((c) => alive && setCrime(c)).catch(() => {})
    api.market().then((m) => alive && setMarket(m)).catch(() => {})
    return () => { alive = false }
  }, [])

  const items = useMemo(() => {
    const all = news?.items ?? []
    return (topic ? all.filter((x) => x.topic === topic) : all).slice(0, 12)
  }, [news, topic])

  const year = crime?.window?.since?.slice(0, 4)

  return (
    <div className="px-3 pb-3">
      {/* ── vital signs ─────────────────────────────────────────────── */}
      {(crime || market) && (
        <div className="grid grid-cols-2 gap-1.5 pt-2.5">
          <Tile
            label={`Crime, ${year ?? 'YTD'}`}
            value={fmtInt(crime?.complaints?.total)}
            change={crime?.complaints?.change_pct}
            downIsGood
          />
          <Tile
            label="Shootings"
            value={fmtInt(crime?.shootings?.this_year?.incidents)}
            change={crime?.shootings?.change_pct}
            downIsGood
          />
          <Tile
            label="Asking rent"
            value={fmtUsd(market?.city?.asking_rent?.value)}
            change={market?.city?.asking_rent?.yoy_pct}
          />
          <Tile
            label="Asking price"
            value={fmtUsd(market?.city?.asking_price?.value)}
            change={market?.city?.asking_price?.yoy_pct}
          />
        </div>
      )}

      {/* ── headlines ───────────────────────────────────────────────── */}
      <div className="mt-3 flex flex-wrap items-center gap-1">
        {TOPICS.map(([id, label]) => (
          <button
            key={id}
            onClick={() => setTopic(id)}
            className={`btn pixel tap px-2 py-1.5 text-[9.5px] ${topic === id ? 'btn-on' : ''}`}
          >
            {label}
          </button>
        ))}
      </div>

      {failed && !news && (
        <p className="mt-2 text-[11.5px] text-nes-ink3">News feeds unreachable.</p>
      )}
      {news && !items.length && (
        <p className="mt-2 text-[11.5px] text-nes-ink3">Nothing under this topic right now.</p>
      )}

      <ul className="mt-2 space-y-2">
        {items.map((it) => (
          <li key={it.url} className="leading-snug">
            <a
              href={it.url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-[12px] text-nes-ink2 hover:text-white hover:underline"
            >
              {it.title}
            </a>
            <div className="mt-0.5 text-[10.5px] text-nes-ink3">
              {it.source}
              {it.published ? ` · ${when(it.published)}` : ''}
              {it.topic ? ` · ${it.topic}` : ''}
            </div>
          </li>
        ))}
      </ul>

      <a
        href={REPORT_URL}
        target="_blank"
        rel="noopener noreferrer"
        className="btn pixel tap mt-3 block px-3 py-2.5 text-center text-[10px]"
      >
        FULL CITY BRIEF
      </a>
    </div>
  )
}

const TOPICS: [string, string][] = [
  ['', 'ALL'], ['housing', 'HOUSING'], ['crime', 'CRIME'],
  ['transit', 'TRANSIT'], ['government', 'GOVT'],
]

/** One vital sign: a number and its year-over-year drift. */
function Tile({ label, value, change, downIsGood }: {
  label: string; value: string; change?: number | null; downIsGood?: boolean
}) {
  // Colour marks direction only where direction has a reading (crime falling
  // is good news); market moves stay neutral — dearer is not "worse" for
  // every reader of this panel.
  const tone = change == null || !downIsGood
    ? 'text-nes-ink3'
    : change <= 0 ? 'text-nes-green' : 'text-nes-red'
  return (
    <div className="rounded-md border border-white/10 bg-black/30 px-2 py-1.5">
      <div className="text-[9.5px] uppercase tracking-wide text-nes-ink3">{label}</div>
      <div className="mt-0.5 text-[13px] font-semibold tabular-nums text-nes-ink">
        {value}
      </div>
      <div className={`text-[10px] tabular-nums ${tone}`}>
        {change == null ? '—' : `${change > 0 ? '+' : ''}${change.toFixed(1)}% in a year`}
      </div>
    </div>
  )
}

function fmtInt(v?: number | null): string {
  return v == null ? '—' : v.toLocaleString('en-US')
}

function fmtUsd(v?: number | null): string {
  if (v == null) return '—'
  return v >= 10_000 ? `$${Math.round(v / 1000).toLocaleString('en-US')}k` : `$${v.toLocaleString('en-US')}`
}

/** "19:31 Oct 7" without pulling in a date library. */
function when(iso: string): string {
  const d = new Date(iso.length === 16 ? `${iso}:00Z` : iso)
  if (isNaN(d.getTime())) return iso.slice(0, 10)
  return d.toLocaleString('en-US', {
    month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
  })
}
