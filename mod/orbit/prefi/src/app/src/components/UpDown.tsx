'use client'

/**
 * Up / Down — every asset is a card. Pick a direction, not a price.
 *
 * A wager is "closes above the round's strike" or "closes below it", where
 * the strike is the price at the round's open — the same number for everyone
 * on that asset that day. Free calls (signed, one per asset per round) and
 * dollar wagers both land in the book; the book's lean is what `/pool/signal`
 * turns into a bias + conviction + leverage suggestion for the trading layer.
 *
 * The curve on each card is the asset's daily closes with the strike drawn
 * across it, so "up or down from here" is a thing you can see, not a number
 * you have to remember. Hover (or touch) walks the curve.
 */

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { toast } from 'react-toastify'
import { API_BASE_URL } from '@/lib/contracts'
import { fmt, usd, pxq, short, countdown, pairLabel } from '@/lib/fmt'
import { Avatar, SourceTag, Empty, Tag, Spinner } from '@/components/ui'

const API = API_BASE_URL

const get = async (url: string) => {
  try { const r = await fetch(url); return r.ok ? r.json() : null } catch { return null }
}

async function post(url: string) {
  const r = await fetch(url, { method: 'POST' })
  const body = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error(body?.detail || body?.error || `HTTP ${r.status}`)
  return body
}

type Side = 'up' | 'down'
type Mode = 'free' | 'paid'
type SourceFilter = 'all' | 'hyperliquid' | 'bittensor' | 'dex'
const DAY_CHOICES = [7, 30, 90]
const PAGE = 24          // cards per page — 700 markets are listed, and every card fetches a curve

const volumeOf = (m: any) => Number(m?.volume_24h_usd ?? m?.volume_24h ?? m?.volume_usd ?? m?.liquidity_usd ?? 0) || 0
const markOf = (m: any) => (m?.quote === 'TAO' ? m?.price : (m?.price_usd ?? m?.price))

export default function UpDown({ address, markets, round, cfg, balance, signFor, onDone }: any) {
  const [signal, setSignal] = useState<any>(null)
  const [quota, setQuota] = useState<any>(null)
  const [mode, setMode] = useState<Mode | null>(null)
  const [amount, setAmount] = useState('')
  const [q, setQ] = useState('')
  const [src, setSrc] = useState<SourceFilter>('all')
  const [days, setDays] = useState(30)
  const [busy, setBusy] = useState<string | null>(null)
  const [limit, setLimit] = useState(PAGE)
  useEffect(() => { setLimit(PAGE) }, [q, src])

  const refresh = useCallback(async () => {
    const [s, qu] = await Promise.all([
      get(`${API}/pool/signal`),
      address ? get(`${API}/pool/side/${address}`) : Promise.resolve(null),
    ])
    if (s) setSignal(s)
    setQuota(qu)
  }, [address])

  useEffect(() => {
    refresh()
    const t = setInterval(refresh, 15000)
    return () => clearInterval(t)
  }, [refresh])

  const freeOn = (cfg?.side_free_per_round ?? quota?.limit ?? 0) > 0
  const freeLeft = quota?.remaining ?? cfg?.side_free_per_round ?? 0
  const funded = (balance?.available || 0) >= (cfg?.min_stake || 0) && (balance?.available || 0) > 0
  const effective: Mode = mode ?? (freeOn && freeLeft > 0 ? 'free' : 'paid')
  const free = effective === 'free'
  const open = round?.entries_open !== false && signal?.entries_open !== false
  const sigByAsset: Record<string, any> = useMemo(() => {
    const out: Record<string, any> = {}
    for (const a of signal?.assets || []) out[a.asset] = a
    return out
  }, [signal])
  const mine: Record<string, any> = useMemo(() => {
    const out: Record<string, any> = {}
    for (const e of round?.mine || []) if (e.side) out[e.asset] = e
    return out
  }, [round])

  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase()
    return (markets || [])
      .filter((m: any) => src === 'all' || m.source === src)
      .filter((m: any) => !needle || pairLabel(m).toLowerCase().includes(needle)
        || (m.bt_name || '').toLowerCase().includes(needle))
      .map((m: any) => ({ m, sig: sigByAsset[m.symbol], me: mine[m.symbol] }))
      .sort((a: any, b: any) =>
        (b.me ? 1 : 0) - (a.me ? 1 : 0)
        || (b.sig?.calls || 0) - (a.sig?.calls || 0)
        || (b.sig?.conviction || 0) - (a.sig?.conviction || 0)
        || volumeOf(b.m) - volumeOf(a.m)
        || a.m.symbol.localeCompare(b.m.symbol))
  }, [markets, q, src, sigByAsset, mine])
  const visible = rows.slice(0, limit)

  const call = async (asset: string, side: Side) => {
    if (!address) return toast.error('Connect a wallet first')
    const stake = free ? 0 : Number(amount)
    if (!free && !(stake > 0)) return toast.error('Enter an amount, or switch to free')
    setBusy(asset)
    try {
      // Signed exactly as the server re-renders it: amount to 6 places,
      // fields in alphabetical order (amount, asset, round, side).
      const amountStr = stake.toFixed(6)
      const roundStr = String(round?.index ?? signal?.round ?? 0)
      const { signature, nonce } = await signFor('side_stake', {
        amount: amountStr, asset, round: roundStr, side,
      })
      const query = new URLSearchParams({ address, asset, side, amount: amountStr, nonce: String(nonce) })
      if (signature) query.set('signature', signature)
      const out = await post(`${API}/pool/side?${query}`)
      toast.success(out.free
        ? `Called ${out.asset} ${side.toUpperCase()} — free, judged against ${pxq(out.strike, out.quote)} at the close`
        : `${usd(out.staked)} on ${out.asset} ${side.toUpperCase()} — the winning side splits the book at the close`)
      await refresh()
      onDone?.()
    } catch (err: any) {
      toast.error(err.message || 'Call failed')
    } finally { setBusy(null) }
  }

  const count = (s: SourceFilter) => (markets || []).filter((m: any) => s === 'all' || m.source === s).length

  return (
    <div className="space-y-4 fade-in">
      <WagerBar address={address} mode={effective} setMode={setMode} freeOn={freeOn} freeLeft={freeLeft}
                amount={amount} setAmount={setAmount} balance={balance} cfg={cfg} funded={funded}
                open={open} round={round} />

      <SignalStrip signal={signal} />

      <div className="flex items-center gap-2 flex-wrap">
        <input className="input !w-56 !py-2 text-sm" placeholder="Find an asset…" value={q}
               onChange={e => setQ(e.target.value)} />
        {([['all', 'All'], ['hyperliquid', 'Hyperliquid'], ['bittensor', 'Bittensor'], ['dex', 'Solana · Base']] as [SourceFilter, string][])
          .filter(([id]) => id === 'all' || count(id) > 0)
          .map(([id, label]) => (
            <button key={id} onClick={() => setSrc(id)} className={`chip ${src === id ? 'active' : ''}`}>
              {label} <span className="mono t3">{count(id)}</span>
            </button>
          ))}
        <span className="flex-1" />
        <div className="seg">
          {DAY_CHOICES.map(d => (
            <button key={d} onClick={() => setDays(d)} className={`seg-btn mono ${days === d ? 'active' : ''}`}>{d}d</button>
          ))}
        </div>
      </div>

      {rows.length === 0 ? (
        <div className="card"><Empty title="Nothing to call" msg="List a Hyperliquid pair, a Bittensor subnet or a Solana/Base token from the Markets tab — every one of them gets a card here." /></div>
      ) : (
        <>
          <div className="grid sm:grid-cols-2 xl:grid-cols-3 gap-4">
            {visible.map(({ m, sig, me }: any) => (
              <AssetCard key={m.symbol} m={m} sig={sig} me={me} days={days} mode={effective} amount={amount}
                         cfg={cfg} round={round} signal={signal} open={open} address={address}
                         quota={quota} busy={busy === m.symbol} onCall={(side: Side) => call(m.symbol, side)} />
            ))}
          </div>
          {rows.length > visible.length && (
            <div className="flex items-center justify-center gap-3 pt-1">
              <button className="btn btn-secondary" onClick={() => setLimit(l => l + PAGE * 2)}>
                Show more <span className="mono t3 ml-1">{visible.length} / {rows.length}</span>
              </button>
              <span className="text-xs t3">or search for the one you want</span>
            </div>
          )}
        </>
      )}
    </div>
  )
}


/* ─── Wager bar: one control, every card obeys it ──────────────── */

function WagerBar({ address, mode, setMode, freeOn, freeLeft, amount, setAmount, balance, cfg, funded, open, round }: any) {
  const free = mode === 'free'
  return (
    <div className="card p-4 flex items-center gap-4 flex-wrap">
      <div className="flex items-center gap-3">
        <span className="step on">▲▼</span>
        <div>
          <div className="text-sm text-white font-medium">Pick a side on any card</div>
          <div className="text-xs t3">
            judged against the price at today&apos;s open ·{' '}
            {open ? <>entries close in <span className="mono t2">{countdown(round?.seconds_to_deadline)}</span></> : <span className="warn">entries closed — next round at the close</span>}
          </div>
        </div>
      </div>
      <span className="flex-1" />
      <div className="seg">
        <button className={`seg-btn ${free ? 'active violet' : ''}`} disabled={!freeOn} onClick={() => setMode('free')}>
          Free{freeOn ? ` · ${freeLeft} left` : ' · off'}
        </button>
        <button className={`seg-btn ${!free ? 'active accent' : ''}`} onClick={() => setMode('paid')}>
          Dollars
        </button>
      </div>
      {free ? (
        <div className="text-xs t3 max-w-xs">
          <span className="violet font-medium">No money down.</span> Scored for the record and the signal, pays nothing.
        </div>
      ) : (
        <div className="flex items-center gap-2">
          <div className="field-group w-40">
            <input className="input !py-2 mono pr-12" placeholder={`min ${fmt(cfg?.min_stake || 0, 0)}`} inputMode="decimal"
                   value={amount} onChange={e => setAmount(e.target.value)} />
            <span className="suffix">USD</span>
          </div>
          <button className="btn btn-ghost btn-xs" disabled={!(balance?.available > 0)}
                  onClick={() => setAmount(String(balance.available))}>MAX</button>
          <span className="text-xs t3">
            balance <span className="mono t2">{usd(balance?.available || 0)}</span>
            {address && !funded && <> · <span className="warn">deposit first</span></>}
          </span>
        </div>
      )}
    </div>
  )
}


/* ─── Signal strip: what the leverage layer reads ──────────────── */

function SignalStrip({ signal }: any) {
  const live = (signal?.assets || []).filter((a: any) => a.bias !== 'flat')
  const crowd = signal?.crowd
  return (
    <div className="card px-4 py-3 flex items-center gap-3 flex-wrap text-xs">
      <Tag tone="accent">signal</Tag>
      {live.length === 0 ? (
        <span className="t3">No lean yet this round — the first calls on a card set it.</span>
      ) : (
        <div className="flex items-center gap-2 flex-wrap">
          {live.slice(0, 8).map((a: any) => (
            <span key={a.asset} className={`tag ${a.bias === 'long' ? 'tag-up' : 'tag-down'}`}
                  title={`lean ${a.lean > 0 ? '+' : ''}${a.lean} · conviction ${(a.conviction * 100).toFixed(0)}% · ${a.calls} calls`}>
              {a.asset} {a.bias} ×{a.leverage}
            </span>
          ))}
          {live.length > 8 && <span className="t3">+{live.length - 8} more</span>}
        </div>
      )}
      <span className="flex-1" />
      <span className="t3">
        crowd record{' '}
        <span className="mono t2">{crowd?.rounds ? `${crowd.hits}/${crowd.rounds} · ${(crowd.hit_rate * 100).toFixed(0)}%` : 'none yet'}</span>
        {' · '}max ×{signal?.max_leverage ?? '—'}
        {' · '}<code className="mono">GET /pool/signal</code>
      </span>
    </div>
  )
}


/* ─── One asset, one card ──────────────────────────────────────── */

function AssetCard({ m, sig, me, days, mode, amount, cfg, round, signal, open, address, quota, busy, onCall }: any) {
  const [hover, setHover] = useState<any>(null)
  const mark = sig?.mark ?? markOf(m)
  const strike = sig?.strike ?? null
  const quote = m.quote
  const book = sig?.book
  const notional = cfg?.free_notional || 0
  const upW = book ? book.up.amount + book.up.free * notional : 0
  const downW = book ? book.down.amount + book.down.free * notional : 0
  const total = upW + downW
  const upPct = total ? upW / total : 0.5
  const delta = sig?.mark_vs_strike_pct ?? (mark && strike ? (mark - strike) / strike * 100 : null)
  const leading = sig?.leading ?? (delta == null ? null : delta > 0 ? 'up' : delta < 0 ? 'down' : null)
  const assetUsed = (quota?.assets_used || []).includes(m.symbol) || !!me
  const free = mode === 'free'
  const thin = m.source === 'dex' && m.eligible === false
  const blocked = !address || !open || busy || thin || assetUsed
    || (free && ((quota?.remaining ?? cfg?.side_free_per_round ?? 0) <= 0))
    || (!free && !(Number(amount) > 0))
  const settled = sig?.settled
  const winner = sig?.winner_side
  const myHit = me ? (settled ? me.hit : leading ? leading === me.side : null) : null

  return (
    <div className={`card updown-card ${me ? 'card-accent' : ''}`}>
      <div className="flex items-start justify-between gap-3 px-4 pt-4">
        <div className="flex items-center gap-3 min-w-0">
          <Avatar symbol={m.symbol} size="lg" />
          <div className="min-w-0">
            <div className="text-sm text-white font-semibold truncate">{pairLabel(m)}</div>
            <div className="flex items-center gap-1.5 mt-1"><SourceTag m={m} />
              {sig?.track?.rounds > 0 && (
                <span className="text-[10.5px] t3" title="how often this asset's crowd picked the winning side">
                  crowd <span className="mono t2">{sig.track.hits}/{sig.track.rounds}</span>
                </span>
              )}
            </div>
          </div>
        </div>
        <div className="text-right shrink-0">
          {hover ? (
            <>
              <div className="num text-base text-white leading-tight">{pxq(hover.price, quote)}</div>
              <div className="text-[11px] t3 mono">{hover.label}
                {hover.vs != null && <span className={`ml-1 ${hover.vs >= 0 ? 'up' : 'down'}`}>{hover.vs >= 0 ? '+' : ''}{hover.vs.toFixed(2)}%</span>}
              </div>
            </>
          ) : (
            <>
              <div className="num text-base text-white leading-tight">{mark ? pxq(mark, quote) : '—'}</div>
              <div className="text-[11px] mono">
                {delta == null ? <span className="t3">no strike yet</span>
                  : <span className={delta >= 0 ? 'up' : 'down'}>{delta >= 0 ? '▲' : '▼'} {Math.abs(delta).toFixed(2)}% vs open</span>}
              </div>
            </>
          )}
        </div>
      </div>

      <div className="px-2 mt-2">
        <Curve asset={m.symbol} days={days} strike={strike} mark={mark} quote={quote} onHover={setHover} />
      </div>

      <div className="px-4 mt-1">
        <BookBar upPct={upPct} book={book} leading={leading} />
        <div className="flex items-center justify-between mt-2 text-[11px]">
          <SignalChip sig={sig} />
          <span className="t3 mono">
            {strike ? <>open {pxq(strike, quote)}</> : 'strike set by the first call'}
          </span>
        </div>
      </div>

      <div className="px-4 pb-4 mt-3">
        {me ? (
          <div className={`receipt !border-solid ${me.side === 'up' ? '!border-[rgba(63,224,138,0.35)]' : '!border-[rgba(255,93,110,0.35)]'}`}>
            <div className="kv">
              <span>you called</span>
              <span className={me.side === 'up' ? 'up' : 'down'}>{me.side === 'up' ? '▲ UP' : '▼ DOWN'} · {me.free ? 'free' : usd(me.amount)}</span>
            </div>
            <div className="kv">
              <span>{settled ? 'result' : 'right now'}</span>
              <span className={myHit == null ? 't3' : myHit ? 'up' : 'down'}>
                {myHit == null ? 'waiting on a price' : settled ? (myHit ? 'won' : 'lost') : (myHit ? 'winning' : 'losing')}
                {!settled && <span className="t3 font-normal"> · settles in {countdown(signal?.seconds_to_close ?? round?.seconds_to_close)}</span>}
              </span>
            </div>
          </div>
        ) : (
          <div className="grid grid-cols-2 gap-2">
            <button className="btn btn-up btn-lg" disabled={blocked} onClick={() => onCall('up')}>
              {busy ? <Spinner /> : <>▲ UP</>}
            </button>
            <button className="btn btn-down btn-lg" disabled={blocked} onClick={() => onCall('down')}>
              {busy ? <Spinner /> : <>▼ DOWN</>}
            </button>
            <div className="col-span-2 text-[10.5px] t3 text-center">
              {!address ? 'connect a wallet to call it'
                : !open ? 'entries closed for this round'
                : thin ? 'under the liquidity floor'
                : assetUsed ? 'one call per asset per round'
                : free ? `free call · judged at the close` : Number(amount) > 0 ? `${usd(Number(amount))} on it · winners split the book` : 'enter an amount above'}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

function SignalChip({ sig }: any) {
  if (!sig || sig.bias === 'flat') return <span className="t3">no lean yet</span>
  const long = sig.bias === 'long'
  return (
    <span className="inline-flex items-center gap-1.5" title={`lean ${sig.lean > 0 ? '+' : ''}${sig.lean} · ${sig.calls} calls · conviction ${(sig.conviction * 100).toFixed(0)}%`}>
      <span className={`tag ${long ? 'tag-up' : 'tag-down'}`}>{sig.bias} ×{sig.leverage}</span>
      <span className="t3">{(sig.conviction * 100).toFixed(0)}% conviction</span>
    </span>
  )
}

function BookBar({ upPct, book, leading }: any) {
  const calls = book?.calls || 0
  return (
    <div>
      <div className="book-bar" title={calls ? `${book.up.count} up · ${book.down.count} down` : 'no calls yet'}>
        <div className="book-up" style={{ width: `${calls ? upPct * 100 : 50}%`, opacity: calls ? 1 : 0.25 }} />
        <div className="book-down" style={{ width: `${calls ? (1 - upPct) * 100 : 50}%`, opacity: calls ? 1 : 0.25 }} />
      </div>
      <div className="flex justify-between text-[10.5px] mono mt-1">
        <span className={`up ${leading === 'up' ? 'font-semibold' : ''}`}>
          ▲ {calls ? `${(upPct * 100).toFixed(0)}%` : '—'}
          {book && book.up.count > 0 && <span className="t3"> · {book.up.count} {book.up.amount > 0 ? usd(book.up.amount, 0) : 'free'}</span>}
        </span>
        <span className={`down ${leading === 'down' ? 'font-semibold' : ''}`}>
          {book && book.down.count > 0 && <span className="t3">{book.down.count} {book.down.amount > 0 ? usd(book.down.amount, 0) : 'free'} · </span>}
          {calls ? `${((1 - upPct) * 100).toFixed(0)}%` : '—'} ▼
        </span>
      </div>
    </div>
  )
}


/* ─── The curve: daily closes, the strike across them ──────────── */

const curveCache: Record<string, { at: number; data: any }> = {}

function Curve({ asset, days, strike, mark, quote, onHover }: any) {
  const ref = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(0)
  const [data, setData] = useState<any>(null)
  const [hoverIdx, setHoverIdx] = useState<number | null>(null)
  const H = 92, PAD_T = 8, PAD_B = 6

  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    const ro = new ResizeObserver(() => setWidth(el.clientWidth))
    ro.observe(el)
    setWidth(el.clientWidth)
    return () => ro.disconnect()
  }, [])

  const [seen, setSeen] = useState(false)
  useEffect(() => {
    const el = ref.current
    if (!el || seen) return
    if (typeof IntersectionObserver === 'undefined') { setSeen(true); return }
    const io = new IntersectionObserver(es => { if (es.some(e => e.isIntersecting)) setSeen(true) }, { rootMargin: '300px' })
    io.observe(el)
    return () => io.disconnect()
  }, [seen])

  useEffect(() => {
    if (!seen) return
    let alive = true
    const key = `${asset}|${days}`
    const hit = curveCache[key]
    if (hit && Date.now() - hit.at < 5 * 60_000) { setData(hit.data); return }
    get(`${API}/pool/curve/${encodeURIComponent(asset)}?days=${days}`).then(d => {
      if (!alive || !d || d.error) return
      curveCache[key] = { at: Date.now(), data: d }
      setData(d)
    })
    return () => { alive = false }
  }, [asset, days, seen])

  const points = useMemo(() => {
    const candles: any[] = data?.candles || []
    const pts = candles.map(c => ({ t: c.t, price: c.c, partial: !!c.partial }))
    // The live mark is today's close-so-far.
    if (mark && pts.length && pts[pts.length - 1].partial) pts[pts.length - 1] = { ...pts[pts.length - 1], price: mark }
    else if (mark && pts.length) pts.push({ t: Math.floor(Date.now() / 86400000) * 86400, price: mark, partial: true })
    return pts
  }, [data, mark])

  if (!width) return <div ref={ref} style={{ height: H }} />
  if (points.length < 2) {
    return (
      <div ref={ref} className="flex items-center justify-center text-[11px] t3" style={{ height: H }}>
        {data ? 'no daily history for this feed yet' : <Spinner />}
      </div>
    )
  }

  const prices = points.map(p => p.price)
  const lo0 = Math.min(...prices, strike || Infinity)
  const hi0 = Math.max(...prices, strike || -Infinity)
  const span = (hi0 - lo0) || hi0 * 0.02 || 1
  const lo = lo0 - span * 0.08, hi = hi0 + span * 0.08
  const x = (i: number) => (i / (points.length - 1)) * width
  const y = (p: number) => PAD_T + (1 - (p - lo) / (hi - lo)) * (H - PAD_T - PAD_B)
  const line = points.map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(p.price).toFixed(1)}`).join(' ')
  const area = `${line} L${width},${H} L0,${H} Z`
  const last = points[points.length - 1]
  const above = strike ? last.price >= strike : prices[prices.length - 1] >= prices[0]
  const color = above ? 'var(--up)' : 'var(--down)'
  const strikeY = strike ? y(strike) : null
  const gid = `g-${asset.replace(/[^a-z0-9]/gi, '')}`

  const move = (clientX: number) => {
    const rect = ref.current!.getBoundingClientRect()
    const i = Math.round(((clientX - rect.left) / rect.width) * (points.length - 1))
    const idx = Math.max(0, Math.min(points.length - 1, i))
    setHoverIdx(idx)
    const p = points[idx]
    const d = new Date(p.t * 1000)
    onHover?.({
      price: p.price,
      label: p.partial ? 'today · live' : d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }),
      vs: strike ? (p.price - strike) / strike * 100 : null,
    })
  }
  const leave = () => { setHoverIdx(null); onHover?.(null) }

  return (
    <div ref={ref} className="curve" style={{ height: H }}
         onPointerMove={e => move(e.clientX)} onPointerLeave={leave} onPointerDown={e => move(e.clientX)}>
      <svg width={width} height={H} className="block">
        <defs>
          <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity="0.22" />
            <stop offset="100%" stopColor={color} stopOpacity="0" />
          </linearGradient>
        </defs>
        {strikeY != null && (
          <>
            <rect x={0} y={0} width={width} height={Math.max(0, strikeY)} fill="rgba(63,224,138,0.035)" />
            <rect x={0} y={strikeY} width={width} height={Math.max(0, H - strikeY)} fill="rgba(255,93,110,0.035)" />
            <line x1={0} x2={width} y1={strikeY} y2={strikeY} stroke="var(--warn)" strokeWidth="1" strokeDasharray="3 3" opacity="0.9" />
            <text x={4} y={strikeY - 3} fontSize="9" fill="var(--warn)" fontFamily="var(--font-mono)" opacity="0.9">open</text>
          </>
        )}
        <path d={area} fill={`url(#${gid})`} />
        <path d={line} fill="none" stroke={color} strokeWidth="1.6" strokeLinejoin="round" strokeLinecap="round" />
        <circle cx={x(points.length - 1)} cy={y(last.price)} r="3" fill={color}>
          <animate attributeName="r" values="3;4.5;3" dur="2s" repeatCount="indefinite" />
        </circle>
        {hoverIdx != null && (
          <>
            <line x1={x(hoverIdx)} x2={x(hoverIdx)} y1={0} y2={H} stroke="rgba(255,255,255,0.18)" strokeWidth="1" />
            <circle cx={x(hoverIdx)} cy={y(points[hoverIdx].price)} r="3.5" fill={color} stroke="#000" strokeWidth="1.5" />
          </>
        )}
      </svg>
    </div>
  )
}
