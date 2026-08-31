'use client'

import { useState, useEffect, useCallback, useMemo } from 'react'
import { ConnectButton } from '@rainbow-me/rainbowkit'
import { useAccount } from 'wagmi'
import { API_BASE_URL } from '@/lib/contracts'
import {
  fmt, fmtUsd, fmtPx, fmtTao, fmtQuoted, fmtVol, fmtTaoVol, pctClass, pctSign, pairLabel, sourceLabel,
  short, countdown, countdownShort,
} from '@/lib/fmt'
import { Section, Stat, Empty, Avatar, SourceTag, Tabs, Tag, Label, Spinner } from '@/components/ui'
import Pool from '@/components/Pool'
import { toast } from 'react-toastify'

const API = API_BASE_URL
// Optional: live pool prices from the uniswap module. Off unless pointed at a
// reachable one — a hardcoded localhost port is mixed content over https.
const UNISWAP_API = process.env.NEXT_PUBLIC_UNISWAP_API

type Tab = 'pool' | 'predict' | 'markets' | 'trade' | 'portfolio' | 'leaderboard'

// Page files may only export the component itself — keep this module-local.
const get = async (url: string) => {
  try { const r = await fetch(url); return r.ok ? r.json() : null } catch { return null }
}

export default function Home() {
  const { address } = useAccount()
  const [tab, setTab] = useState<Tab>('pool')
  const [status, setStatus] = useState<any>(null)
  const [markets, setMarkets] = useState<any[]>([])
  const [treasury, setTreasury] = useState<any>(null)
  const [positions, setPositions] = useState<any[]>([])
  const [stakes, setStakes] = useState<any>(null)
  const [portfolio, setPortfolio] = useState<any>(null)
  const [leaders, setLeaders] = useState<any[]>([])
  const [poolPrices, setPoolPrices] = useState<any[]>([])
  const [balance, setBalance] = useState<any>(null)
  const [predictions, setPredictions] = useState<any[]>([])
  const [scoring, setScoring] = useState<any>(null)
  const [quota, setQuota] = useState<any>(null)

  // Remember the tab across reloads — a console you keep coming back to
  // should open where you left it.
  useEffect(() => {
    const h = (typeof window !== 'undefined' && window.location.hash.slice(1)) as Tab
    if (['pool', 'predict', 'markets', 'trade', 'portfolio', 'leaderboard'].includes(h)) setTab(h)
  }, [])
  const go = (t: Tab) => { setTab(t); if (typeof window !== 'undefined') history.replaceState(null, '', `#${t}`) }

  const fetchAll = useCallback(async () => {
    const [s, m, t, sc] = await Promise.all([
      get(`${API}/status`), get(`${API}/markets`),
      get(`${API}/treasury`), get(`${API}/scoring/models`),
    ])
    if (s) setStatus(s)
    if (m) setMarkets(m)
    if (t) setTreasury(t)
    if (sc) setScoring(sc)
  }, [])

  const fetchUser = useCallback(async () => {
    if (!address) return
    const [pos, stk, port, bal, preds, q] = await Promise.all([
      get(`${API}/positions/${address}`),
      get(`${API}/stakes/${address}`),
      get(`${API}/portfolio/${address}`),
      get(`${API}/balance/${address}`),
      get(`${API}/predictions/${address}`),
      get(`${API}/predictions/free/${address}`),
    ])
    if (pos) setPositions(pos)
    if (stk) setStakes(stk)
    if (port) setPortfolio(port)
    if (bal) setBalance(bal)
    if (preds) setPredictions(preds)
    if (q) setQuota(q)
  }, [address])

  const fetchLeaders = useCallback(async () => {
    try { const r = await fetch(`${API}/leaderboard`); if (r.ok) setLeaders(await r.json()) } catch {}
  }, [])

  // Fetch Uniswap pool prices from the uniswap module
  const fetchPoolPrices = useCallback(async () => {
    if (!UNISWAP_API) return
    try {
      const r = await fetch(`${UNISWAP_API}/tokens?chain=base&limit=20`)
      if (r.ok) setPoolPrices(await r.json())
    } catch {}
  }, [])

  useEffect(() => {
    fetchAll()
    fetchPoolPrices()
    const i = setInterval(fetchAll, 15000)
    const j = setInterval(fetchPoolPrices, 30000)
    return () => { clearInterval(i); clearInterval(j) }
  }, [fetchAll, fetchPoolPrices])

  useEffect(() => { fetchUser() }, [fetchUser])
  useEffect(() => { if (tab === 'leaderboard') fetchLeaders() }, [tab, fetchLeaders])

  const refresh = () => { fetchAll(); fetchUser() }
  const pool = status?.pool

  const tabs: { id: Tab; label: string; count?: number }[] = [
    { id: 'pool', label: 'Pool' },
    { id: 'predict', label: 'Predict' },
    { id: 'markets', label: 'Markets', count: markets?.length },
    { id: 'trade', label: 'Trade' },
    { id: 'portfolio', label: 'Portfolio' },
    { id: 'leaderboard', label: 'Leaderboard' },
  ]

  return (
    <main className="min-h-screen">
      {/* Header */}
      <div className="topbar">
        <div className="max-w-[1180px] mx-auto px-5 h-[60px] flex items-center justify-between gap-4">
          <button onClick={() => go('pool')} className="flex items-center gap-3 group" title="PreFi">
            <Logo />
            <div className="leading-none text-left">
              <div className="text-[15px] font-bold tracking-tight text-white">PreFi</div>
              <div className="text-[10.5px] t3 mt-1 tracking-wide">call the close · split the pot</div>
            </div>
          </button>

          <div className="flex items-center gap-2.5">
            {pool?.vault && (
              <span className="hidden md:inline-flex tag tag-neutral normal-case tracking-normal font-medium gap-2 py-1.5 px-2.5">
                <span className="dot dot-live" />
                HyperEVM
                <span className="t3">·</span>
                <span className="t2">round {pool.round}</span>
              </span>
            )}
            <Connect />
          </div>
        </div>
        <Ticker markets={markets} onPick={() => go('markets')} />
      </div>

      <div className="max-w-[1180px] mx-auto px-5 pt-4 pb-16">
        <Tabs tabs={tabs} value={tab} onChange={go} />

        <div className="fade-in pt-5" key={tab}>
          {tab === 'pool' && <Pool address={address} markets={markets} onAction={refresh} />}
          {tab === 'predict' && <Predict markets={markets} address={address} balance={balance}
            predictions={predictions} scoring={scoring} quota={quota} onAction={refresh} />}
          {tab === 'markets' && <Markets status={status} markets={markets} treasury={treasury} poolPrices={poolPrices} onMarkets={fetchAll} />}
          {tab === 'trade' && <Trade markets={markets} address={address} onTrade={refresh} />}
          {tab === 'portfolio' && <Portfolio positions={positions} stakes={stakes} portfolio={portfolio} balance={balance} address={address} onAction={refresh} />}
          {tab === 'leaderboard' && <Leaderboard leaders={leaders} />}
        </div>

        <footer className="mt-14 pt-5 border-t border-white/[0.06] flex flex-wrap items-center justify-between gap-3 text-[11px] t3">
          <span>
            Prices: Hyperliquid marks, Bittensor subnet pools, Solana &amp; Base DEX pools, CoinGecko. Pool settles on-chain balances on HyperEVM.
          </span>
          <span className="mono">{status?.markets ?? markets.length} markets · {status?.predictions_total ?? 0} calls · epoch {status?.current_epoch ?? 0}</span>
        </footer>
      </div>
    </main>
  )
}

function Logo() {
  return (
    <span className="relative inline-flex w-9 h-9 rounded-[11px] items-center justify-center"
      style={{ background: 'linear-gradient(145deg, #dfff7a, #b6dd3a)', boxShadow: '0 0 0 1px rgba(255,255,255,0.12) inset, 0 8px 24px -8px rgba(201,242,77,0.7)' }}>
      <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
        {/* a price path landing on a target line */}
        <path d="M3 13.5 L7 9.5 L10 12 L14 6.5 L17 8.5" stroke="#0a0c05" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
        <circle cx="17" cy="8.5" r="2" fill="#0a0c05" />
      </svg>
    </span>
  )
}

/* ─── Wallet button ───────────────────────────────────────────── */

function Connect() {
  return (
    <ConnectButton.Custom>
      {({ account, chain, openAccountModal, openChainModal, openConnectModal, mounted }) => {
        const ready = mounted
        const connected = ready && account && chain
        return (
          <div style={ready ? {} : { opacity: 0, pointerEvents: 'none' }} className="flex items-center gap-2">
            {!connected ? (
              <button onClick={openConnectModal} className="btn btn-primary">Connect wallet</button>
            ) : chain.unsupported ? (
              <button onClick={openChainModal} className="btn btn-secondary down">Wrong network</button>
            ) : (
              <>
                <button onClick={openChainModal} className="btn btn-ghost btn-sm hidden sm:inline-flex" title={chain.name}>
                  {chain.hasIcon && chain.iconUrl && (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img alt={chain.name ?? ''} src={chain.iconUrl} className="w-3.5 h-3.5 rounded-full" />
                  )}
                  {chain.name}
                </button>
                <button onClick={openAccountModal} className="btn btn-secondary btn-sm mono">
                  <span className="dot" style={{ background: `hsl(${parseInt(account.address.slice(2, 8), 16) % 360} 70% 60%)` }} />
                  {account.displayName}
                </button>
              </>
            )}
          </div>
        )
      }}
    </ConnectButton.Custom>
  )
}

/* ─── Ticker ──────────────────────────────────────────────────── */

function Ticker({ markets, onPick }: { markets: any[]; onPick: () => void }) {
  const items = useMemo(() => (markets || []).filter(m => m.active && (m.price_usd || m.price)), [markets])
  if (!items.length) return null
  const loop = items.length > 6
  const list = loop ? [...items, ...items] : items
  return (
    <div className="ticker" onClick={onPick}>
      <div className={`ticker-track ${loop ? '' : 'justify-center w-full'}`}
        style={{ ['--ticker-duration' as any]: `${Math.max(30, items.length * 4)}s`, animation: loop ? undefined : 'none' }}>
        {list.map((m, i) => (
          <span key={`${m.symbol}-${i}`} className="ticker-item">
            <span className="dot" style={{ background: m.source === 'hyperliquid' ? 'var(--teal)' : m.source === 'bittensor' ? 'var(--violet)' : 'var(--pink)' }} />
            <span className="t2 font-medium">{pairLabel(m)}</span>
            <span className="mono text-white">{fmtQuoted(m.quote === 'TAO' ? m.price : m.price_usd, m.quote)}</span>
            {m.change_24h != null && (
              <span className={`mono ${pctClass(m.change_24h)}`}>{pctSign(m.change_24h)}{m.change_24h.toFixed(1)}%</span>
            )}
          </span>
        ))}
      </div>
    </div>
  )
}

/* ─── Markets (was Dashboard) ─────────────────────────────────── */

type SourceFilter = 'all' | 'hyperliquid' | 'bittensor' | 'solana' | 'base'

// A DEX market files under its chain; the old Uniswap/CoinGecko markets are Base too.
const sourceKey = (m: any): SourceFilter =>
  m.source === 'hyperliquid' || m.source === 'bittensor' ? m.source
    : m.source === 'dex' && m.chain === 'solana' ? 'solana' : 'base'

function Markets({ status, markets, treasury, poolPrices, onMarkets }: any) {
  const s = status || {}
  const t = treasury || {}
  const [addOpen, setAddOpen] = useState<'' | 'hl' | 'bt' | 'solana' | 'base'>('')
  const [filter, setFilter] = useState<SourceFilter>('all')
  const [q, setQ] = useState('')
  const toggle = (which: 'hl' | 'bt' | 'solana' | 'base') => setAddOpen(o => (o === which ? '' : which))

  const counts = useMemo(() => {
    const c = { all: markets.length, hyperliquid: 0, bittensor: 0, solana: 0, base: 0 } as Record<SourceFilter, number>
    for (const m of markets) c[sourceKey(m)]++
    return c
  }, [markets])

  const rows = useMemo(() => markets.filter((m: any) => {
    if (filter !== 'all' && sourceKey(m) !== filter) return false
    if (!q) return true
    const needle = q.toLowerCase()
    return [m.symbol, m.bt_name, m.hl_key, pairLabel(m)].some(x => x && String(x).toLowerCase().includes(needle))
  }), [markets, filter, q])

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <Stat label="Markets" value={markets.length}
          sub={`${counts.hyperliquid} Hyperliquid · ${counts.bittensor} Bittensor · ${counts.solana} Solana · ${counts.base} Base`} />
        <Stat label="Treasury" value={fmtUsd(t.balance || 0)}
          sub={`${fmtUsd(t.total_captured || 0)} captured · ${fmtUsd(t.total_distributed || 0)} paid · epoch ${t.current_epoch || 0}`} />
        <Stat label="PREFI supply" value={fmt(t.prefi_supply || 0, 0)}
          sub={`${fmt(s.total_prefi_minted || 0, 2)} minted · ${fmt(s.total_prefi_burned || 0, 2)} burned`} />
        <Stat label="Calls" value={s.predictions_total || 0}
          sub={`${s.predictions_open || 0} open · ${s.forecasters || 0} forecasters · ${fmtUsd(s.total_volume || 0, 0)} traded`} />
      </div>

      <Section title="Listed markets" count={markets?.length} action={
        <div className="flex items-center gap-2">
          <button onClick={() => toggle('hl')} className={`btn btn-sm ${addOpen === 'hl' ? 'btn-primary' : 'btn-ghost'}`}>
            {addOpen === 'hl' ? 'Close' : '+ Hyperliquid'}
          </button>
          <button onClick={() => toggle('bt')} className={`btn btn-sm ${addOpen === 'bt' ? 'btn-primary' : 'btn-ghost'}`}>
            {addOpen === 'bt' ? 'Close' : '+ Bittensor'}
          </button>
          <button onClick={() => toggle('solana')} className={`btn btn-sm ${addOpen === 'solana' ? 'btn-primary' : 'btn-ghost'}`}>
            {addOpen === 'solana' ? 'Close' : '+ Solana'}
          </button>
          <button onClick={() => toggle('base')} className={`btn btn-sm ${addOpen === 'base' ? 'btn-primary' : 'btn-ghost'}`}>
            {addOpen === 'base' ? 'Close' : '+ Base'}
          </button>
        </div>
      }>
        {addOpen === 'hl' && <HyperliquidAdd onAdded={() => { onMarkets?.(); }} />}
        {addOpen === 'bt' && <BittensorAdd onAdded={() => { onMarkets?.(); }} />}
        {(addOpen === 'solana' || addOpen === 'base') && (
          <DexAdd key={addOpen} chain={addOpen} onAdded={() => { onMarkets?.(); }} />
        )}

        <div className="px-[18px] py-3 border-b border-white/[0.06] flex flex-wrap items-center gap-2">
          {([['all', 'All'], ['hyperliquid', 'Hyperliquid'], ['bittensor', 'Bittensor'], ['solana', 'Solana'], ['base', 'Base']] as [SourceFilter, string][]).map(([id, label]) => (
            <button key={id} onClick={() => setFilter(id)} className={`chip ${filter === id ? 'active' : ''}`}>
              {id !== 'all' && <span className="dot" style={{ background: id === 'hyperliquid' ? 'var(--teal)' : id === 'bittensor' ? 'var(--violet)' : id === 'solana' ? 'var(--pink)' : 'var(--blue, #3b82f6)' }} />}
              {label} <span className="mono t3">{counts[id]}</span>
            </button>
          ))}
          <input value={q} onChange={e => setQ(e.target.value)} placeholder="Filter…"
            className="input ml-auto max-w-[200px] py-1.5 px-3 text-xs" />
        </div>

        {rows.length > 0 ? (
          <div>
            <div className="thead grid-cols-[1fr_130px_90px_70px_80px]">
              <span>Asset</span><span className="text-right">Price</span>
              <span className="text-right">Volume</span><span className="text-right">Trades</span>
              <span className="text-right">Win rate</span>
            </div>
            {rows.map((m: any) => (
              <div key={`${m.source}-${m.symbol}`} className="trow grid-cols-[1fr_130px_90px_70px_80px]">
                <div className="flex items-center gap-3 min-w-0">
                  <Avatar symbol={m.symbol} />
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-sm text-white font-medium truncate">{pairLabel(m)}</span>
                      <SourceTag m={m} />
                    </div>
                    <div className="text-[11px] t3 truncate mt-0.5">{sourceLabel(m)}</div>
                  </div>
                </div>
                <div className="text-right">
                  <div className="num text-sm text-white">{m.quote === 'TAO' ? fmtTao(m.price) : fmtPx(m.price_usd)}</div>
                  {m.quote === 'TAO' && m.price_usd > 0 && <div className="num text-[11px] t3">{fmtPx(m.price_usd)}</div>}
                </div>
                <span className="text-right num text-xs t2">{m.total_volume ? fmtUsd(m.total_volume, 0) : <span className="t3">—</span>}</span>
                <span className="text-right num text-xs t2">{m.total_positions || <span className="t3">—</span>}</span>
                <span className={`text-right num text-xs ${(m.win_rate || 0) >= 50 ? 'up' : m.total_positions ? 't2' : 't3'}`}>
                  {m.total_positions ? `${m.win_rate || 0}%` : '—'}
                </span>
              </div>
            ))}
          </div>
        ) : markets.length ? (
          <Empty title="Nothing matches" msg={`No listed market matches “${q}”.`} />
        ) : (
          <Empty title="No markets listed yet" msg="List a Hyperliquid pair, a Bittensor subnet, or a Solana/Base token that clears the liquidity floor — every one of them is stakeable in the pool.">
            <div className="flex justify-center gap-2">
              <button onClick={() => toggle('hl')} className="btn btn-primary btn-sm">+ Hyperliquid</button>
              <button onClick={() => toggle('bt')} className="btn btn-secondary btn-sm">+ Bittensor</button>
              <button onClick={() => toggle('solana')} className="btn btn-secondary btn-sm">+ Solana</button>
              <button onClick={() => toggle('base')} className="btn btn-secondary btn-sm">+ Base</button>
            </div>
          </Empty>
        )}
      </Section>

      {/* Uniswap pool prices — only when the uniswap module is wired up */}
      {UNISWAP_API && (
        <Section title="Uniswap Base pools" count={poolPrices?.length} sub="live on-chain">
          {poolPrices?.length > 0 ? (
            <div>
              <div className="thead grid-cols-[1fr_120px_120px_100px]">
                <span>Token</span><span className="text-right">Price</span>
                <span className="text-right">Volume 24h</span><span className="text-right">Liquidity</span>
              </div>
              {poolPrices.slice(0, 15).map((t: any, i: number) => (
                <div key={i} className="trow grid-cols-[1fr_120px_120px_100px]">
                  <div className="flex items-center gap-2.5">
                    <Avatar symbol={t.symbol || '?'} size="sm" />
                    <span className="text-sm text-white font-medium">{t.symbol || t.id?.slice(0, 8)}</span>
                    {t.name && <span className="text-[11px] t3">{t.name}</span>}
                  </div>
                  <span className="text-right num text-sm text-white">
                    {t.derivedETH ? fmtUsd(parseFloat(t.derivedETH) * 2500, t.derivedETH > 0.001 ? 2 : 6) : '—'}
                  </span>
                  <span className="text-right num text-xs t2">{t.volumeUSD ? fmtUsd(parseFloat(t.volumeUSD), 0) : '—'}</span>
                  <span className="text-right num text-xs t2">{t.totalValueLockedUSD ? fmtUsd(parseFloat(t.totalValueLockedUSD), 0) : '—'}</span>
                </div>
              ))}
            </div>
          ) : <Empty msg="No pool data from the uniswap module yet." />}
        </Section>
      )}
    </div>
  )
}

/* ─── Trade ───────────────────────────────────────────────────── */

function Trade({ markets, address, onTrade }: any) {
  const [asset, setAsset] = useState('')
  const [amount, setAmount] = useState('')
  const [loading, setLoading] = useState(false)

  const activeMarkets = (markets || []).filter((m: any) => m.active)
  const market = activeMarkets.find((m: any) => m.symbol === asset)

  const handleOpen = async () => {
    if (!address || !asset || !amount) return
    setLoading(true)
    try {
      const r = await fetch(`${API}/position/open?asset=${encodeURIComponent(asset)}&amount=${amount}&address=${address}`, { method: 'POST' })
      const d = await r.json()
      if (r.ok) {
        toast.success(`Opened ${asset} position — ${fmt(d.asset_amount, 6)} tokens at ${fmtPx(d.entry_price)}`)
        setAmount('')
        onTrade?.()
      } else toast.error(d.detail || 'Failed')
    } catch (e: any) { toast.error(e.message) }
    setLoading(false)
  }

  return (
    <div className="grid grid-cols-1 lg:grid-cols-[400px_1fr] gap-5">
      <Section title="Open a position" sub="profit mints PREFI">
        <div className="p-[18px] space-y-4">
          <div>
            <Label>Asset</Label>
            <select value={asset} onChange={e => setAsset(e.target.value)} className="input">
              <option value="">Select a market…</option>
              {activeMarkets.map((m: any) => (
                <option key={m.symbol} value={m.symbol}>
                  {pairLabel(m)} {m.price_usd ? `— ${fmtPx(m.price_usd)}` : ''}
                </option>
              ))}
            </select>
          </div>
          <div>
            <Label>Amount</Label>
            <div className="field-group">
              <input type="number" value={amount} onChange={e => setAmount(e.target.value)}
                placeholder="0.00" className="input input-lg" />
              <span className="suffix">USDC</span>
            </div>
          </div>
          {asset && amount && (
            <div className="receipt">
              <div className="kv"><span>Entry price</span><span>{market?.price_usd ? fmtPx(market.price_usd) : '—'}</span></div>
              <div className="kv"><span>If profitable</span><span className="up !font-sans">profit → treasury · you mint PREFI</span></div>
              <div className="kv"><span>If loss</span><span className="down !font-sans">you absorb it · no PREFI</span></div>
            </div>
          )}
          <button onClick={handleOpen} disabled={!address || !asset || !amount || loading}
            className="btn btn-primary btn-lg w-full">
            {loading ? <Spinner /> : 'Open position'}
          </button>
          {!address && <p className="note text-center">Connect a wallet to trade.</p>}
        </div>
      </Section>

      <Section title="Prices" count={activeMarkets.length}>
        <div className="max-h-[560px] overflow-y-auto">
          {activeMarkets.length > 0 ? activeMarkets.map((m: any) => (
            <button key={`${m.source}-${m.symbol}`} onClick={() => setAsset(m.symbol)}
              className={`trow w-full text-left grid-cols-[1fr_auto] ${asset === m.symbol ? 'mine' : ''}`}>
              <div className="flex items-center gap-3 min-w-0">
                <Avatar symbol={m.symbol} />
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-white">{pairLabel(m)}</span>
                    <SourceTag m={m} />
                  </div>
                  <div className="text-[11px] t3 truncate mt-0.5">{sourceLabel(m)}</div>
                </div>
              </div>
              <div className="text-right">
                <div className="num text-sm text-white">{m.quote === 'TAO' ? fmtTao(m.price) : fmtPx(m.price_usd)}</div>
                <div className="text-[11px] t3">{m.total_positions || 0} trades</div>
              </div>
            </button>
          )) : <Empty title="No markets" msg="List one from the Markets tab." />}
        </div>
      </Section>
    </div>
  )
}

/* ─── Hyperliquid pair browser ─────────────────────────────────── */
//
// Hyperliquid quotes ~900 pairs: ~180 perps and ~700 spot books. All of them
// are listable here, so the picker is a search over the whole universe rather
// than a row of majors — sorted by 24h volume, because the liquid end is what
// anyone is actually going to stake on.

const PAGE = 60

function HyperliquidAdd({ onAdded }: { onAdded: () => void }) {
  const [search, setSearch] = useState('')
  const [kind, setKind] = useState<'all' | 'perp' | 'spot'>('all')
  const [assets, setAssets] = useState<any[]>([])
  const [stats, setStats] = useState<any>(null)
  const [shown, setShown] = useState(PAGE)
  const [loading, setLoading] = useState(true)
  const [adding, setAdding] = useState('')

  useEffect(() => {
    let live = true
    setLoading(true)
    const t = setTimeout(async () => {
      // limit=0 is the whole filtered universe; paging happens in the browser
      // so typing never goes back to the feed.
      const [d, st] = await Promise.all([
        get(`${API}/hyperliquid/assets?search=${encodeURIComponent(search)}&kind=${kind}&limit=0`),
        get(`${API}/hyperliquid/stats`),
      ])
      if (!live) return
      setAssets(d || []); setStats(st); setShown(PAGE); setLoading(false)
    }, 250)
    return () => { live = false; clearTimeout(t) }
  }, [search, kind])

  const add = async (a: any) => {
    setAdding(a.key)
    try {
      const r = await fetch(`${API}/hyperliquid/add?coin=${encodeURIComponent(a.key)}`, { method: 'POST' })
      const d = await r.json()
      if (r.ok) {
        toast.success(`${a.coin} listed — priced from Hyperliquid`)
        setAssets(list => list.map(x => x.key === a.key ? { ...x, listed: true } : x))
        onAdded()
      } else toast.error(d.detail || 'Failed')
    } catch (e: any) { toast.error(e.message) }
    setAdding('')
  }

  // Adding 20 markets one click at a time is the wrong way to stand a pool up.
  const seed = async () => {
    setAdding('seed')
    try {
      const r = await fetch(`${API}/hyperliquid/seed?limit=20&kind=${kind}`, { method: 'POST' })
      const d = await r.json()
      if (!r.ok) throw new Error(d.detail || 'Failed')
      toast.success(d.added?.length ? `Listed ${d.added.length}: ${d.added.slice(0, 6).join(', ')}${
        d.added.length > 6 ? '…' : ''}` : 'Every top pair is already listed')
      const listed = new Set(d.added || [])
      setAssets(list => list.map(x => listed.has(x.coin) ? { ...x, listed: true } : x))
      onAdded()
    } catch (e: any) { toast.error(e.message) }
    setAdding('')
  }

  const KINDS: Array<'all' | 'perp' | 'spot'> = ['all', 'perp', 'spot']

  return (
    <Browser
      search={search} setSearch={setSearch}
      placeholder="Search every Hyperliquid pair — BTC, HYPE/USDC, @107…"
      controls={
        <>
          <div className="seg">
            {KINDS.map(k => (
              <button key={k} onClick={() => setKind(k)} className={`seg-btn uppercase ${kind === k ? 'active' : ''}`}>{k}</button>
            ))}
          </div>
          <button onClick={seed} disabled={adding === 'seed'} className="btn btn-secondary btn-sm"
            title="List the 20 busiest pairs of this kind in one call">
            {adding === 'seed' ? 'Listing…' : 'List top 20'}
          </button>
        </>
      }
      stats={stats && (
        <>{stats.pairs} pairs quoted · {stats.perps} perps · {stats.spot} spot · <b className="t2">{stats.listed} listed here</b>
          {stats.age_seconds != null && <> · updated {Math.round(stats.age_seconds / 60)}m ago</>}</>
      )}
      loading={loading} loadingMsg="Loading the pair universe…"
      empty={stats?.reachable === false
        ? <>Hyperliquid is unreachable — prefi reads it through the local <b>hyperliquid</b> module and falls back to the public API when that one is asleep.</>
        : <>No pair matches “{search}”.</>}
      rows={assets} shown={shown} more={() => setShown(n => n + PAGE)} noun="pairs"
      row={(a: any) => (
        <div key={a.key} className="trow grid-cols-[1fr_110px_80px_80px_70px] py-2 px-[18px]">
          <div className="flex items-center gap-2 min-w-0">
            <span className={`tag ${a.kind === 'perp' ? 'tag-teal' : 'tag-up'}`}>{a.kind}</span>
            <span className="text-sm text-white truncate">{a.coin}</span>
            {a.coin !== a.key && <span className="text-[11px] t3 mono">{a.key}</span>}
          </div>
          <span className="text-right num text-sm text-white">{fmtPx(a.price)}</span>
          <span className={`text-right num text-xs ${a.change_24h == null ? 't3' : pctClass(a.change_24h)}`}>
            {a.change_24h == null ? '—' : `${pctSign(a.change_24h)}${a.change_24h.toFixed(2)}%`}
          </span>
          <span className="text-right num text-xs t3">{fmtVol(a.volume_24h)}</span>
          <div className="text-right">
            <button disabled={a.listed || adding === a.key} onClick={() => add(a)}
              className={`btn btn-xs ${a.listed ? 'btn-ghost' : 'btn-secondary'}`}>
              {a.listed ? 'Listed' : adding === a.key ? '…' : '+ Add'}
            </button>
          </div>
        </div>
      )}
    />
  )
}

/* ─── Solana / Base token browser ──────────────────────────────── */
//
// The universe here is unbounded — anything with a pool — so the default
// list is the chain's busiest pools and the search goes to DexScreener. Every
// row is one pool (the deepest one for that token), and the owner's liquidity
// floor decides which of them can be listed at all: those under it are shown,
// greyed, with the number, rather than hidden.

function DexAdd({ chain, onAdded }: { chain: 'solana' | 'base'; onAdded: () => void }) {
  const [search, setSearch] = useState('')
  const [assets, setAssets] = useState<any[]>([])
  const [stats, setStats] = useState<any>(null)
  const [shown, setShown] = useState(PAGE)
  const [loading, setLoading] = useState(true)
  const [adding, setAdding] = useState('')
  const label = chain === 'solana' ? 'Solana' : 'Base'

  useEffect(() => {
    let live = true
    setLoading(true)
    const t = setTimeout(async () => {
      const [d, st] = await Promise.all([
        get(`${API}/dex/assets?chain=${chain}&search=${encodeURIComponent(search)}&limit=0`),
        get(`${API}/dex/stats?chain=${chain}`),
      ])
      if (!live) return
      setAssets(d || []); setStats(st); setShown(PAGE); setLoading(false)
    }, 300)
    return () => { live = false; clearTimeout(t) }
  }, [search, chain])

  const add = async (a: any) => {
    setAdding(a.key)
    try {
      const r = await fetch(`${API}/dex/add?chain=${chain}&address=${encodeURIComponent(a.key)}`, { method: 'POST' })
      const d = await r.json()
      if (r.ok) {
        toast.success(`${d.market?.symbol || a.coin} listed — priced from its ${a.dex} pool on ${label}`)
        setAssets(list => list.map(x => x.key === a.key ? { ...x, listed: true, symbol: d.market?.symbol } : x))
        onAdded()
      } else toast.error(d.detail || 'Failed')
    } catch (e: any) { toast.error(e.message) }
    setAdding('')
  }

  const seed = async () => {
    setAdding('seed')
    try {
      const r = await fetch(`${API}/dex/seed?chain=${chain}&limit=20`, { method: 'POST' })
      const d = await r.json()
      if (!r.ok) throw new Error(d.detail || 'Failed')
      toast.success(d.added?.length ? `Listed ${d.added.length}: ${d.added.slice(0, 6).join(', ')}${
        d.added.length > 6 ? '…' : ''}` : 'Every eligible top token is already listed')
      const listed = new Set(d.added || [])
      setAssets(list => list.map(x => listed.has(x.coin) ? { ...x, listed: true } : x))
      onAdded()
    } catch (e: any) { toast.error(e.message) }
    setAdding('')
  }

  const floor = stats?.min_liquidity_usd ?? 0
  const eligible = assets.filter(a => a.eligible).length

  return (
    <Browser
      search={search} setSearch={setSearch}
      placeholder={chain === 'solana'
        ? 'Search any Solana token — WIF, BONK, a mint address, a pool…'
        : 'Search any Base token — BRETT, DEGEN, a 0x token or pool address…'}
      controls={
        <button onClick={seed} disabled={adding === 'seed'} className="btn btn-secondary btn-sm"
          title={`List the 20 busiest ${label} tokens that clear the liquidity floor`}>
          {adding === 'seed' ? 'Listing…' : 'List top 20'}
        </button>
      }
      stats={stats && (
        <>{search ? `${assets.length} ${label} tokens match` : `${stats.pools} busiest ${label} pools`}
          {' · '}min liquidity <b className="t2">{floor ? fmtVol(floor) : 'none'}</b> (set by the pool owner)
          {' · '}{eligible} clear it · <b className="t2">{stats.listed} listed here</b>
          {stats.age_seconds != null && !search && <> · updated {Math.round(stats.age_seconds / 60)}m ago</>}</>
      )}
      loading={loading} loadingMsg={`Loading ${label} pools…`}
      empty={stats?.reachable === false && !search
        ? <>DexScreener and GeckoTerminal are unreachable from this host — no {label} pool list to show.</>
        : <>No {label} token matches “{search}”.</>}
      rows={assets} shown={shown} more={() => setShown(n => n + PAGE)} noun="tokens"
      row={(a: any) => (
        <div key={a.key} className={`trow grid-cols-[1fr_110px_80px_80px_70px] py-2 px-[18px] ${a.eligible ? '' : 'opacity-60'}`}>
          <div className="flex items-center gap-2 min-w-0">
            <span className={`tag ${chain === 'solana' ? 'tag-pink' : 'tag-blue'}`}>{a.dex || chain}</span>
            <span className="text-sm text-white truncate">{a.coin}</span>
            {a.name && a.name.toUpperCase() !== a.coin && <span className="text-[11px] t3 truncate">{a.name}</span>}
            <span className={`tag ${a.eligible ? 'tag-neutral' : 'tag-down'} num`}
              title={a.eligible ? 'liquidity in the pool' : `under the ${fmtVol(floor)} liquidity floor`}>
              {fmtVol(a.liquidity_usd)} liq
            </span>
          </div>
          <span className="text-right num text-sm text-white">{fmtPx(a.price)}</span>
          <span className={`text-right num text-xs ${a.change_24h == null ? 't3' : pctClass(a.change_24h)}`}>
            {a.change_24h == null ? '—' : `${pctSign(a.change_24h)}${a.change_24h.toFixed(2)}%`}
          </span>
          <span className="text-right num text-xs t3">{fmtVol(a.volume_24h)}</span>
          <div className="text-right">
            <button disabled={a.listed || !a.eligible || adding === a.key} onClick={() => add(a)}
              title={a.eligible ? '' : `under the ${fmtVol(floor)} liquidity floor the pool owner set`}
              className={`btn btn-xs ${a.listed || !a.eligible ? 'btn-ghost' : 'btn-secondary'}`}>
              {a.listed ? 'Listed' : !a.eligible ? 'Thin' : adding === a.key ? '…' : '+ Add'}
            </button>
          </div>
        </div>
      )}
    />
  )
}

/* ─── Bittensor subnet browser ─────────────────────────────────── */
//
// Every subnet has an alpha token priced in TAO by its own pool. The list comes
// from the local bt module's indexer — 128-odd subnets, sorted by 24h volume —
// and a listed subnet settles against the price that indexer snapshotted at
// the round's close.

function BittensorAdd({ onAdded }: { onAdded: () => void }) {
  const [search, setSearch] = useState('')
  const [assets, setAssets] = useState<any[]>([])
  const [stats, setStats] = useState<any>(null)
  const [shown, setShown] = useState(PAGE)
  const [loading, setLoading] = useState(true)
  const [adding, setAdding] = useState('')

  useEffect(() => {
    let live = true
    setLoading(true)
    const t = setTimeout(async () => {
      const [d, st] = await Promise.all([
        get(`${API}/bittensor/assets?search=${encodeURIComponent(search)}&limit=0`),
        get(`${API}/bittensor/stats`),
      ])
      if (!live) return
      setAssets(d || []); setStats(st); setShown(PAGE); setLoading(false)
    }, 250)
    return () => { live = false; clearTimeout(t) }
  }, [search])

  const add = async (a: any) => {
    setAdding(a.key)
    try {
      const r = await fetch(`${API}/bittensor/add?subnet=${encodeURIComponent(a.key)}`, { method: 'POST' })
      const d = await r.json()
      if (r.ok) {
        toast.success(`${a.coin}${a.name ? ` (${a.name})` : ''} listed — priced in TAO from Bittensor`)
        setAssets(list => list.map(x => x.key === a.key ? { ...x, listed: true } : x))
        onAdded()
      } else toast.error(d.detail || 'Failed')
    } catch (e: any) { toast.error(e.message) }
    setAdding('')
  }

  const seed = async () => {
    setAdding('seed')
    try {
      const r = await fetch(`${API}/bittensor/seed?limit=20`, { method: 'POST' })
      const d = await r.json()
      if (!r.ok) throw new Error(d.detail || 'Failed')
      toast.success(d.added?.length ? `Listed ${d.added.length}: ${d.added.slice(0, 6).join(', ')}${
        d.added.length > 6 ? '…' : ''}` : 'Every top subnet is already listed')
      const listed = new Set(d.added || [])
      setAssets(list => list.map(x => listed.has(x.coin) ? { ...x, listed: true } : x))
      onAdded()
    } catch (e: any) { toast.error(e.message) }
    setAdding('')
  }

  return (
    <Browser
      search={search} setSearch={setSearch}
      placeholder="Search every subnet — 64, SN64, lium.io, chutes…"
      controls={
        <button onClick={seed} disabled={adding === 'seed'} className="btn btn-secondary btn-sm"
          title="List the 20 busiest subnets in one call">
          {adding === 'seed' ? 'Listing…' : 'List top 20'}
        </button>
      }
      stats={stats && (
        <>{stats.subnets} subnets quoted · {fmtTaoVol(stats.volume_24h_tao)} 24h volume · <b className="t2">{stats.listed} listed here</b>
          {stats.age_seconds != null && <> · updated {Math.round(stats.age_seconds / 60)}m ago</>}</>
      )}
      loading={loading} loadingMsg="Loading the subnet list…"
      empty={stats?.reachable === false
        ? <>Bittensor is unreachable — prefi reads subnets through the local <b>bt</b> module, which is the price feed the pool settles against.</>
        : <>No subnet matches “{search}”.</>}
      rows={assets} shown={shown} more={() => setShown(n => n + PAGE)} noun="subnets"
      row={(a: any) => (
        <div key={a.key} className="trow grid-cols-[1fr_110px_80px_80px_70px] py-2 px-[18px]">
          <div className="flex items-center gap-2 min-w-0">
            <span className="tag tag-violet mono">{a.coin}</span>
            <span className="text-sm text-white truncate">{a.name || `subnet ${a.netuid}`}</span>
            {a.symbol && <span className="text-[11px] t3">{a.symbol}</span>}
          </div>
          <span className="text-right num text-sm text-white">{fmtTao(a.price)}</span>
          <span className={`text-right num text-xs ${a.change_24h == null ? 't3' : pctClass(a.change_24h)}`}>
            {a.change_24h == null ? '—' : `${pctSign(a.change_24h)}${a.change_24h.toFixed(2)}%`}
          </span>
          <span className="text-right num text-xs t3">{fmtTaoVol(a.volume_24h)}</span>
          <div className="text-right">
            <button disabled={a.listed || adding === a.key} onClick={() => add(a)}
              className={`btn btn-xs ${a.listed ? 'btn-ghost' : 'btn-secondary'}`}>
              {a.listed ? 'Listed' : adding === a.key ? '…' : '+ Add'}
            </button>
          </div>
        </div>
      )}
    />
  )
}

/** The shared frame of the two listing browsers: search, controls, stats, rows, paging. */
function Browser({ search, setSearch, placeholder, controls, stats, loading, loadingMsg, empty, rows, shown, more, noun, row }: any) {
  return (
    <div className="border-b border-white/[0.06] bg-black/20">
      <div className="px-[18px] pt-4 pb-3 space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <input value={search} onChange={e => setSearch(e.target.value)} placeholder={placeholder} autoFocus
            className="input flex-1 min-w-[240px] py-2" />
          {controls}
        </div>
        {stats && <div className="text-[11px] t3">{stats}</div>}
      </div>
      {loading ? (
        <div className="px-[18px] pb-4 text-xs t3 flex items-center gap-2"><span className="spinner" style={{ borderColor: 'rgba(255,255,255,0.15)', borderTopColor: '#fff' }} /> {loadingMsg}</div>
      ) : rows.length === 0 ? (
        <div className="px-[18px] pb-4 note">{empty}</div>
      ) : (
        <>
          <div className="thead grid-cols-[1fr_110px_80px_80px_70px]">
            <span>Pair</span><span className="text-right">Price</span><span className="text-right">24h</span>
            <span className="text-right">Volume</span><span />
          </div>
          <div className="max-h-[360px] overflow-y-auto">{rows.slice(0, shown).map(row)}</div>
          <div className="px-[18px] py-2.5 flex items-center justify-between text-[11px] t3 border-t border-white/[0.06]">
            <span>showing {Math.min(shown, rows.length)} of {rows.length} matching {noun}</span>
            {shown < rows.length && <button onClick={more} className="btn btn-ghost btn-xs">Show more</button>}
          </div>
        </>
      )}
    </div>
  )
}

/* ─── Predict ─────────────────────────────────────────────────── */

const HORIZONS = [
  { label: '1 hour', value: 3600 },
  { label: '12 hours', value: 43200 },
  { label: '1 day', value: 86400 },
  { label: '3 days', value: 259200 },
  { label: '1 week', value: 604800 },
]

function Predict({ markets, address, balance, predictions, scoring, quota, onAction }: any) {
  const active = (markets || []).filter((m: any) => m.active)
  const params = scoring?.active
  const [asset, setAsset] = useState('')
  const [price, setPrice] = useState('')
  const [burn, setBurn] = useState('')
  const [horizon, setHorizon] = useState(String(params?.horizon || 86400))
  // Free is the default door: a new wallet holds no PREFI and would otherwise
  // have nothing to burn.
  const [mode, setMode] = useState<'free' | 'burn'>('free')
  const [loading, setLoading] = useState(false)
  const [board, setBoard] = useState<any[]>([])

  const market = active.find((m: any) => m.symbol === asset)
  const spot = market?.price_usd
  const available = balance?.available ?? 0
  const free = mode === 'free'
  const freeLeft = quota?.remaining ?? params?.free_per_day ?? 0
  const freeOff = quota ? !quota.enabled : params?.free_per_day === 0
  const freePay = quota?.free_payout ?? params?.free_payout ?? 0

  useEffect(() => { get(`${API}/predictions/board`).then(d => setBoard(d || [])) }, [predictions])

  // Out of free calls but holding PREFI? Put them on the paid door instead.
  useEffect(() => {
    if (free && (freeOff || (quota && freeLeft <= 0)) && available > 0) setMode('burn')
  }, [freeOff, freeLeft, available]) // eslint-disable-line react-hooks/exhaustive-deps

  // Seed the price box with spot so the input is an edit, not a blank guess.
  useEffect(() => { if (spot) setPrice(String(spot)) }, [spot])

  const move = spot && price ? (parseFloat(price) - spot) / spot * 100 : null
  const maxPayout = free ? freePay : (burn && params ? parseFloat(burn) * params.multiplier : 0)
  const ready = !!address && !!asset && !!price && (free ? freeLeft > 0 && !freeOff : !!burn)

  const submit = async () => {
    if (!ready) return
    setLoading(true)
    try {
      const r = await fetch(`${API}/predict?asset=${encodeURIComponent(asset)}&predicted_price=${price}` +
        `&burn=${free ? 0 : burn}&address=${address}&horizon=${horizon}`, { method: 'POST' })
      const d = await r.json()
      if (r.ok) {
        toast.success(free
          ? `Free call on ${asset} @ ${fmtPx(parseFloat(price))} — ${d.free_remaining} left today, ` +
            `resolves ${new Date(d.resolves_at).toLocaleString()}`
          : `Burned ${burn} PREFI on ${asset} @ ${fmtPx(parseFloat(price))} — ` +
            `resolves ${new Date(d.resolves_at).toLocaleString()}`)
        setBurn('')
        onAction?.()
      } else toast.error(d.detail || 'Failed')
    } catch (e: any) { toast.error(e.message) }
    setLoading(false)
  }

  const open = predictions?.filter((p: any) => !p.resolved) || []
  const done = predictions?.filter((p: any) => p.resolved) || []

  // Everything here except placing a call is public — the scoring params and
  // the forecaster board are the protocol's rules and results, not your data.
  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <Stat label="Free calls left" value={freeOff ? 'off' : freeLeft} tone={freeOff ? '' : 'violet'}
          sub={freeOff ? 'free tier disabled'
            : `of ${quota?.limit ?? params?.free_per_day ?? 0} per 24h · a perfect one mints ${fmt(freePay, 2)} PREFI`} />
        <Stat label="PREFI available" value={fmt(available, 2)}
          sub={balance?.from_free ? `${fmt(balance.from_free, 2)} of it earned free` : 'minted by trading profit and good calls'} />
        <Stat label="Burned" value={fmt(balance?.burned || 0, 2)} sub="on predictions, gone either way" />
        <Stat label="Scoring" value={<span className="mono">{params?.model || '—'}</span>}
          sub={params ? `tolerance ${(params.tolerance * 100).toFixed(2)}% · pays up to ${params.multiplier}× the burn` : ''} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[1fr_400px] gap-5">
        {/* Place a call */}
        <Section title="Call the price" action={
          <div className="seg">
            <button onClick={() => setMode('free')} disabled={freeOff} className={`seg-btn ${free ? 'active violet' : ''}`}>
              Free{!freeOff && ` · ${freeLeft}`}
            </button>
            <button onClick={() => setMode('burn')} className={`seg-btn ${!free ? 'active accent' : ''}`}>Burn PREFI</button>
          </div>
        }>
          <div className="p-[18px] space-y-4">
            <div className="grid sm:grid-cols-2 gap-3">
              <div>
                <Label>Asset</Label>
                <select value={asset} onChange={e => setAsset(e.target.value)} className="input">
                  <option value="">Select a market…</option>
                  {active.map((m: any) => (
                    <option key={m.symbol} value={m.symbol}>
                      {pairLabel(m)} {m.price_usd ? `— ${fmtPx(m.price_usd)}` : ''}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <Label>Horizon</Label>
                <select value={horizon} onChange={e => setHorizon(e.target.value)} className="input">
                  {HORIZONS.map(h => <option key={h.value} value={h.value}>{h.label}</option>)}
                </select>
              </div>
            </div>

            <div>
              <Label hint={spot ? <>now <span className="mono t2">{fmtPx(spot)}</span></> : undefined}>Price at resolution</Label>
              <div className="field-group">
                <input type="number" value={price} onChange={e => setPrice(e.target.value)}
                  placeholder="0.00" className="input input-lg" />
                {move != null && (
                  <span className={`suffix mono ${pctClass(move)}`}>{pctSign(move)}{move.toFixed(2)}%</span>
                )}
              </div>
              {spot && (
                <div className="flex flex-wrap gap-1.5 mt-2">
                  {[-5, -2, -1, 0, 1, 2, 5].map(p => (
                    <button key={p} onClick={() => setPrice(p === 0 ? String(spot) : (spot * (1 + p / 100)).toPrecision(8))}
                      className="chip mono">
                      {p === 0 ? 'spot' : `${p > 0 ? '+' : ''}${p}%`}
                    </button>
                  ))}
                </div>
              )}
            </div>

            {!free && (
              <div>
                <Label hint={<>have <span className="mono t2">{fmt(available, 2)}</span></>}>Burn</Label>
                <div className="field-group">
                  <input type="number" value={burn} onChange={e => setBurn(e.target.value)}
                    placeholder={String(params?.min_burn ?? 1)} className="input mono" />
                  <span className="suffix">PREFI</span>
                </div>
              </div>
            )}

            {asset && price && (free || burn) && (
              <div className="receipt">
                <div className="kv"><span>Implied move</span>
                  <span className={move != null ? pctClass(move) : ''}>{move != null ? `${pctSign(move)}${move.toFixed(2)}%` : '—'}</span></div>
                <div className="kv"><span>{free ? 'At risk' : 'Burned now'}</span>
                  {free ? <span className="t2">nothing</span> : <span className="down">−{fmt(parseFloat(burn), 2)} PREFI</span>}</div>
                <div className="kv"><span>Max payout · exact call</span><span className="up">+{fmt(maxPayout, 2)} PREFI</span></div>
                {free && <div className="kv"><span>Free calls after this</span><span>{Math.max(0, freeLeft - 1)}</span></div>}
              </div>
            )}

            <button onClick={submit}
              disabled={!ready || loading || (!free && available <= 0)}
              className={`btn btn-lg w-full ${free ? 'btn-violet' : 'btn-primary'}`}>
              {loading ? <Spinner /> : free ? 'Call it free' : 'Burn & call'}
            </button>
            <p className="note text-center">
              {!address ? 'Connect a wallet to call a price.'
                : free && freeOff ? 'Free calls are switched off — burn PREFI to predict.'
                : free && freeLeft <= 0 ? `Out of free calls${quota?.resets_at ? ` — next one ${new Date(quota.resets_at).toLocaleString()}` : ''}.`
                : free ? <>Costs nothing. A good call still mints up to <b>{fmt(freePay, 2)} PREFI</b>.</>
                : available <= 0 ? 'No PREFI to burn — use a free call, or close a profitable trade to mint some.'
                : <>The burn is destroyed either way; the payout is <b>burn × {params?.multiplier}× × score</b>, freshly minted.</>}
            </p>
          </div>
        </Section>

        <ScoringPanel scoring={scoring} onSaved={onAction} />
      </div>

      <Section title="Your calls" count={predictions?.length}
        sub={predictions?.length ? `${open.length} open · ${done.length} settled` : undefined}>
        <div className="max-h-[460px] overflow-y-auto">
          {predictions?.length > 0
            ? [...open, ...done].map((p: any) => <PredictionRow key={p.id} p={p} />)
            : <Empty title={address ? 'No calls yet' : 'Connect a wallet'} msg={address ? 'Your open and settled price calls land here.' : 'Your calls show up here once you are connected.'} />}
        </div>
      </Section>

      <Section title="Forecasters" count={board?.length} sub="ranked by average score">
        {board?.length > 0 ? (
          <div>
            <div className="thead grid-cols-[36px_1fr_80px_90px_90px_100px]">
              <span>#</span><span>Forecaster</span><span className="text-right">Calls</span>
              <span className="text-right">Avg score</span><span className="text-right">Burned</span>
              <span className="text-right">Net PREFI</span>
            </div>
            {board.map((f: any) => {
              const mine = address && f.address?.toLowerCase() === address.toLowerCase()
              return (
                <div key={f.rank} className={`trow grid-cols-[36px_1fr_80px_90px_90px_100px] ${mine ? 'mine' : ''}`}>
                  <span className={`rank rank-${f.rank}`}>{f.rank}</span>
                  <span className="mono text-sm text-white truncate">{mine ? <span className="accent">you</span> : short(f.address)}
                    <span className="t3 ml-2 text-[11px] hidden md:inline">{f.address}</span></span>
                  <span className="text-right num text-xs t2">{f.resolved}/{f.predictions}</span>
                  <span className="text-right num text-sm text-white">{(f.avg_score * 100).toFixed(1)}%</span>
                  <span className="text-right num text-xs t2">
                    {f.total_burned > 0 ? fmt(f.total_burned, 2) : <span className="violet">free</span>}
                  </span>
                  <span className={`text-right num text-sm ${f.net_prefi >= 0 ? 'up' : 'down'}`}>
                    {f.net_prefi >= 0 ? '+' : ''}{fmt(f.net_prefi, 2)}
                  </span>
                </div>
              )
            })}
          </div>
        ) : <Empty title="No forecasters yet" msg="The first settled call puts someone on the board." />}
      </Section>
    </div>
  )
}

function PredictionRow({ p }: { p: any }) {
  const open = !p.resolved
  const projected = p.projected?.score
  return (
    <div className="trow grid-cols-[1fr_auto]">
      <div className="flex items-center gap-3 min-w-0">
        <Avatar symbol={p.asset} />
        <div className="min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm font-medium text-white">{p.asset}</span>
            <Tag tone={open ? 'accent' : p.net >= 0 ? 'up' : 'down'}>
              {open ? `open · ${countdownShort(p.seconds_remaining)}` : `score ${(p.score * 100).toFixed(1)}%`}
            </Tag>
            {p.free && <Tag tone="violet" title="Placed with a daily free call — nothing was burned">free</Tag>}
            {p.price_mode === 'spot' && <Tag tone="warn" title="No historical price was available — settled at the spot price when it resolved">spot</Tag>}
          </div>
          <div className="text-[11px] t3 mt-1 mono">
            called {fmtPx(p.predicted_price)} · from {fmtPx(p.entry_price)}
            {p.resolved && <> · landed {fmtPx(p.actual_price)}</>}
            <span className="hidden sm:inline"> · {p.params?.model} @ {(p.params?.tolerance * 100).toFixed(2)}%</span>
          </div>
        </div>
      </div>
      <div className="text-right shrink-0">
        {p.resolved ? (
          <>
            <div className={`num text-sm ${p.net >= 0 ? 'up' : 'down'}`}>{p.net >= 0 ? '+' : ''}{fmt(p.net, 2)} PREFI</div>
            <div className="text-[11px] t3 mono">off by {fmtPx(p.abs_error)} ({(p.normalized_error * 100).toFixed(2)}%)</div>
          </>
        ) : (
          <>
            <div className="num text-sm t2">{p.free ? 'free call' : <>−{fmt(p.burn, 2)} burned</>}</div>
            <div className="text-[11px] t3 mono">{projected != null ? `would score ${(projected * 100).toFixed(1)}%` : 'awaiting price'}</div>
          </>
        )}
      </div>
    </div>
  )
}

function ScoringPanel({ scoring, onSaved }: any) {
  const active = scoring?.active
  const models = scoring?.models || {}
  const [draft, setDraft] = useState<any>(null)
  const [curve, setCurve] = useState<any[]>([])
  const p = draft || active

  useEffect(() => { setDraft(null) }, [active])

  // The curve is drawn by the API's own scorer, so what's shown is what pays.
  useEffect(() => {
    if (!p) return
    let live = true
    const misses = [0, 0.005, 0.01, 0.02, 0.05, 0.1]
    Promise.all(misses.map(async miss => {
      const d = await get(`${API}/scoring/preview?predicted=${100 * (1 + miss)}&actual=100` +
        `&model=${p.model}&tolerance=${p.tolerance}`)
      return { miss, score: d?.score ?? 0 }
    })).then(rows => { if (live) setCurve(rows) })
    return () => { live = false }
  }, [p?.model, p?.tolerance]) // eslint-disable-line react-hooks/exhaustive-deps

  if (!p) return <Section title="Scoring"><Empty msg="Loading scoring params…" /></Section>

  const set = (k: string, v: any) => setDraft({ ...p, [k]: v })

  const save = async () => {
    const q = new URLSearchParams({
      model: p.model, tolerance: String(p.tolerance), multiplier: String(p.multiplier),
      horizon: String(p.horizon), min_burn: String(p.min_burn),
      free_per_day: String(p.free_per_day), free_payout: String(p.free_payout),
    })
    try {
      const r = await fetch(`${API}/scoring?${q}`, { method: 'POST' })
      const d = await r.json()
      if (r.ok) { toast.success('Scoring updated — applies to new predictions'); setDraft(null); onSaved?.() }
      else toast.error(d.detail || 'Failed')
    } catch (e: any) { toast.error(e.message) }
  }

  return (
    <Section title="Scoring rules" sub={<span className="mono">score = f(|called − actual| / actual)</span>}>
      <div className="p-[18px] space-y-4">
        <div>
          <Label>Model</Label>
          <select value={p.model} onChange={e => set('model', e.target.value)} className="input mono">
            {Object.keys(models).map(m => <option key={m} value={m}>{m}</option>)}
          </select>
          <p className="note mt-2">{models[p.model]}</p>
        </div>

        {/* What the curve pays, straight from the API scorer */}
        <div>
          <div className="label">Score by miss size</div>
          <div className="space-y-1.5">
            {curve.map(row => (
              <div key={row.miss} className="flex items-center gap-2.5">
                <span className="mono text-[11px] t3 w-10 text-right">{(row.miss * 100).toFixed(1)}%</span>
                <div className="meter flex-1"><div style={{ width: `${Math.max(0, Math.min(1, row.score)) * 100}%` }} /></div>
                <span className="mono text-[11px] t2 w-9">{(row.score * 100).toFixed(0)}%</span>
              </div>
            ))}
          </div>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div>
            <Label>Tolerance %</Label>
            <input type="number" step="0.1" value={(p.tolerance * 100).toFixed(2)}
              onChange={e => set('tolerance', Math.max(1e-6, parseFloat(e.target.value || '0') / 100))}
              className="input mono" />
          </div>
          <div>
            <Label>Payout ×</Label>
            <input type="number" step="0.5" value={p.multiplier}
              onChange={e => set('multiplier', parseFloat(e.target.value || '0'))} className="input mono" />
          </div>
          <div>
            <Label>Default horizon</Label>
            <select value={p.horizon} onChange={e => set('horizon', parseInt(e.target.value))} className="input">
              {HORIZONS.map(h => <option key={h.value} value={h.value}>{h.label}</option>)}
            </select>
          </div>
          <div>
            <Label>Min burn</Label>
            <input type="number" step="1" value={p.min_burn}
              onChange={e => set('min_burn', parseFloat(e.target.value || '0'))} className="input mono" />
          </div>
          <div>
            <Label>Free calls / 24h</Label>
            <input type="number" step="1" min="0" value={p.free_per_day}
              onChange={e => set('free_per_day', parseInt(e.target.value || '0'))} className="input mono" />
          </div>
          <div>
            <Label>Free payout</Label>
            <input type="number" step="0.5" min="0" value={p.free_payout}
              onChange={e => set('free_payout', parseFloat(e.target.value || '0'))} className="input mono" />
          </div>
        </div>
        <p className="note">
          Free calls cost nothing and are scored by the same curve — a perfect one mints <b>{fmt(p.free_payout, 2)} PREFI</b>.
          Set the allowance to 0 to turn the free tier off. Open predictions keep the params they were placed under.
        </p>

        <button onClick={save} disabled={!draft} className={`btn w-full ${draft ? 'btn-primary' : 'btn-ghost'}`}>
          {draft ? 'Save scoring rules' : 'Saved'}
        </button>
      </div>
    </Section>
  )
}

/* ─── Portfolio ───────────────────────────────────────────────── */

function Portfolio({ positions, stakes, portfolio, balance, address, onAction }: any) {
  const p = portfolio || {}
  const trading = p.trading || {}
  const prefi = p.prefi || {}
  const claims = p.treasury_claims || {}

  const handleClose = async (id: number) => {
    try {
      const r = await fetch(`${API}/position/close?position_id=${id}&address=${address}`, { method: 'POST' })
      const d = await r.json()
      if (r.ok) {
        const msg = d.status === 'profitable'
          ? `Closed +${fmtUsd(d.profit)} profit → ${fmt(d.prefi_earned, 2)} PREFI earned`
          : `Closed ${fmtUsd(d.profit)} loss`
        toast[d.status === 'profitable' ? 'success' : 'info'](msg)
        onAction?.()
      } else toast.error(d.detail)
    } catch (e: any) { toast.error(e.message) }
  }

  const [lockAmt, setLockAmt] = useState('')
  const [lockWeeks, setLockWeeks] = useState('1')

  const handleLock = async () => {
    if (!address || !lockAmt) return
    try {
      const dur = parseInt(lockWeeks) * 604800
      const r = await fetch(`${API}/stake/lock?amount=${lockAmt}&duration=${dur}&address=${address}`, { method: 'POST' })
      const d = await r.json()
      if (r.ok) { toast.success(`Locked ${lockAmt} PREFI for ${lockWeeks}w`); setLockAmt(''); onAction?.() }
      else toast.error(d.detail)
    } catch (e: any) { toast.error(e.message) }
  }

  const handleUnlock = async (id: number) => {
    try {
      const r = await fetch(`${API}/stake/unlock?stake_id=${id}&address=${address}`, { method: 'POST' })
      const d = await r.json()
      if (r.ok) { toast.success('PREFI unlocked'); onAction?.() }
      else toast.error(d.detail)
    } catch (e: any) { toast.error(e.message) }
  }

  if (!address) {
    return (
      <div className="card">
        <Empty title="Connect a wallet" msg="Your positions, PREFI balance and locked stakes live here.">
          <Connect />
        </Empty>
      </div>
    )
  }

  const activeStakes = (stakes?.stakes || []).filter((s: any) => !s.withdrawn)

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
        <Stat label="Net P&L" value={`${trading.net_pnl >= 0 ? '+' : ''}${fmtUsd(trading.net_pnl || 0)}`} tone={trading.net_pnl >= 0 ? 'up' : 'down'} />
        <Stat label="Volume" value={fmtUsd(trading.total_volume || 0)} sub={`${trading.win_rate || 0}% win rate`} />
        <Stat label="PREFI available" value={fmt(balance?.available || 0, 2)} sub={`${fmt(balance?.minted || 0, 2)} minted`} />
        <Stat label="PREFI locked" value={fmt(prefi.total_locked || 0, 2)} sub={`${fmt(balance?.burned || 0, 2)} burned`} />
        <Stat label="Claimed" value={fmtUsd(claims.total_claimed || 0)} sub={`${claims.epochs_claimed || 0} epochs`} tone={claims.total_claimed > 0 ? 'up' : ''} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        <Section title="Positions" count={positions?.length}>
          <div className="max-h-[420px] overflow-y-auto">
            {positions?.length > 0 ? positions.map((p: any) => (
              <div key={p.id} className="trow grid-cols-[1fr_auto]">
                <div className="flex items-center gap-3 min-w-0">
                  <Avatar symbol={p.asset} />
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium text-white">{p.asset}</span>
                      <Tag tone={p.closed ? (p.profit > 0 ? 'up' : 'down') : 'accent'}>{p.closed ? (p.profit > 0 ? 'win' : 'loss') : 'open'}</Tag>
                    </div>
                    <div className="text-[11px] t3 mt-0.5 mono">
                      {fmtUsd(p.usdc_in)} in · {p.closed ? `${fmtUsd(p.usdc_out)} out` : `${fmt(p.asset_amount, 6)} tokens`}
                    </div>
                  </div>
                </div>
                <div className="text-right">
                  {p.closed ? (
                    <div className={`num text-sm ${p.profit > 0 ? 'up' : 'down'}`}>{p.profit > 0 ? '+' : ''}{fmtUsd(p.profit)}</div>
                  ) : (
                    <button onClick={() => handleClose(p.id)} className="btn btn-secondary btn-xs">Close</button>
                  )}
                  {p.prefi_earned > 0 && <div className="text-[11px] up mono">+{fmt(p.prefi_earned, 2)} PREFI</div>}
                </div>
              </div>
            )) : <Empty title="No positions" msg="Open one from the Trade tab — profit mints PREFI 1:1." />}
          </div>
        </Section>

        <Section title="Lock PREFI" sub="staketime earns weekly treasury epochs">
          <div className="p-[18px] space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label hint={<span className="mono">{fmt(balance?.available || 0, 2)} free</span>}>Amount</Label>
                <input type="number" value={lockAmt} onChange={e => setLockAmt(e.target.value)} placeholder="0" className="input mono" />
              </div>
              <div>
                <Label>Lock for</Label>
                <select value={lockWeeks} onChange={e => setLockWeeks(e.target.value)} className="input">
                  {[1, 2, 4, 8, 13, 26, 52].map(w => <option key={w} value={w}>{w} week{w > 1 ? 's' : ''} · {w}× weight</option>)}
                </select>
              </div>
            </div>
            {lockAmt && (
              <div className="receipt">
                <div className="kv"><span>Staketime</span><span>{fmt(parseFloat(lockAmt || '0') * parseInt(lockWeeks) * 604800, 0)}</span></div>
                <div className="kv"><span>Unlocks</span><span>{new Date(Date.now() + parseInt(lockWeeks) * 604800e3).toLocaleDateString()}</span></div>
              </div>
            )}
            <button onClick={handleLock} disabled={!lockAmt} className="btn btn-primary w-full">Lock PREFI</button>
          </div>

          {activeStakes.length > 0 && (
            <div className="border-t border-white/[0.06]">
              {activeStakes.map((s: any) => (
                <div key={s.id} className="trow grid-cols-[1fr_auto] py-2.5">
                  <div>
                    <div className="num text-sm text-white">{fmt(s.amount, 2)} PREFI</div>
                    <div className="text-[11px] t3">{s.is_unlockable ? 'Ready to unlock' : `${Math.ceil(s.time_remaining / 86400)}d remaining`}</div>
                  </div>
                  {s.is_unlockable
                    ? <button onClick={() => handleUnlock(s.id)} className="btn btn-up btn-xs">Unlock</button>
                    : <Tag>locked</Tag>}
                </div>
              ))}
            </div>
          )}
        </Section>
      </div>
    </div>
  )
}

/* ─── Leaderboard ─────────────────────────────────────────────── */

function Leaderboard({ leaders }: { leaders: any[] }) {
  return (
    <Section title="Top traders" count={leaders?.length} sub="ranked by net profit">
      {leaders?.length > 0 ? (
        <div>
          <div className="thead grid-cols-[36px_1fr_100px_100px_70px_100px]">
            <span>#</span><span>Trader</span><span className="text-right">Profit</span>
            <span className="text-right">Volume</span><span className="text-right">Win</span>
            <span className="text-right">PREFI</span>
          </div>
          {leaders.map((t: any) => (
            <div key={t.rank} className="trow grid-cols-[36px_1fr_100px_100px_70px_100px]">
              <span className={`rank rank-${t.rank}`}>{t.rank}</span>
              <span className="mono text-sm text-white truncate">{short(t.address)}<span className="t3 ml-2 text-[11px] hidden md:inline">{t.address}</span></span>
              <span className={`text-right num text-sm ${t.net_pnl >= 0 ? 'up' : 'down'}`}>{t.net_pnl >= 0 ? '+' : ''}{fmtUsd(t.net_pnl)}</span>
              <span className="text-right num text-xs t2">{fmtUsd(t.total_volume, 0)}</span>
              <span className={`text-right num text-xs ${t.win_rate >= 50 ? 'up' : 't2'}`}>{t.win_rate}%</span>
              <span className="text-right num text-xs accent">{fmt(t.prefi_earned, 2)}</span>
            </div>
          ))}
        </div>
      ) : <Empty title="No trades yet" msg="The first closed position puts a trader on the board." />}
    </Section>
  )
}
