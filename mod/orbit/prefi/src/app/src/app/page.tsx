'use client'

import { useState, useEffect, useCallback } from 'react'
import { ConnectButton } from '@rainbow-me/rainbowkit'
import { useAccount } from 'wagmi'
import { API_BASE_URL } from '@/lib/contracts'
import { toast } from 'react-toastify'

const API = API_BASE_URL
// Optional: live pool prices from the uniswap module. Off unless pointed at a
// reachable one — a hardcoded localhost port is mixed content over https.
const UNISWAP_API = process.env.NEXT_PUBLIC_UNISWAP_API

const fmt = (n: number, d = 2) => n?.toLocaleString(undefined, { maximumFractionDigits: d, minimumFractionDigits: d }) ?? '—'
const fmtUsd = (n: number, d = 2) => `$${fmt(n, d)}`
const pctClass = (n: number) => n >= 0 ? 'up' : 'down'
const pctSign = (n: number) => n >= 0 ? '+' : ''

type Tab = 'dashboard' | 'trade' | 'predict' | 'portfolio' | 'leaderboard'

// Page files may only export the component itself — keep this module-local.
const get = async (url: string) => {
  try { const r = await fetch(url); return r.ok ? r.json() : null } catch { return null }
}

export default function Home() {
  const { address, isConnected } = useAccount()
  const [tab, setTab] = useState<Tab>('dashboard')
  const [status, setStatus] = useState<any>(null)
  const [markets, setMarkets] = useState<any[]>([])
  const [prices, setPrices] = useState<any>(null)
  const [treasury, setTreasury] = useState<any>(null)
  const [positions, setPositions] = useState<any[]>([])
  const [stakes, setStakes] = useState<any>(null)
  const [portfolio, setPortfolio] = useState<any>(null)
  const [leaders, setLeaders] = useState<any[]>([])
  const [poolPrices, setPoolPrices] = useState<any[]>([])
  const [balance, setBalance] = useState<any>(null)
  const [predictions, setPredictions] = useState<any[]>([])
  const [scoring, setScoring] = useState<any>(null)

  const fetchAll = useCallback(async () => {
    const [s, m, p, t, sc] = await Promise.all([
      get(`${API}/status`), get(`${API}/markets`),
      get(`${API}/prices`), get(`${API}/treasury`),
      get(`${API}/scoring/models`),
    ])
    if (s) setStatus(s)
    if (m) setMarkets(m)
    if (p && !p.error) setPrices(p)
    if (t) setTreasury(t)
    if (sc) setScoring(sc)
  }, [])

  const fetchUser = useCallback(async () => {
    if (!address) return
    const [pos, stk, port, bal, preds] = await Promise.all([
      get(`${API}/positions/${address}`),
      get(`${API}/stakes/${address}`),
      get(`${API}/portfolio/${address}`),
      get(`${API}/balance/${address}`),
      get(`${API}/predictions/${address}`),
    ])
    if (pos) setPositions(pos)
    if (stk) setStakes(stk)
    if (port) setPortfolio(port)
    if (bal) setBalance(bal)
    if (preds) setPredictions(preds)
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

  const tabs: { id: Tab; label: string }[] = [
    { id: 'dashboard', label: 'Dashboard' },
    { id: 'trade', label: 'Trade' },
    { id: 'predict', label: 'Predict' },
    { id: 'portfolio', label: 'Portfolio' },
    { id: 'leaderboard', label: 'Leaderboard' },
  ]

  return (
    <main className="min-h-screen">
      <div className="max-w-6xl mx-auto px-4 py-5">
        {/* Header */}
        <header className="flex items-center justify-between mb-6">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-blue-500 to-emerald-500 flex items-center justify-center text-sm font-bold text-white">P</div>
            <div>
              <h1 className="text-lg font-bold text-white leading-none">PreFi</h1>
              <p className="text-[10px] text-zinc-500 leading-none mt-0.5">Trading Protocol</p>
            </div>
          </div>
          <div className="flex items-center gap-2.5">
            {status && (
              <div className="tag bg-emerald-500/10 text-emerald-400 border border-emerald-500/15">
                <div className="pulse-dot bg-emerald-400" />
                {status.network}
              </div>
            )}
            <ConnectButton showBalance={false} chainStatus="icon" accountStatus="address" />
          </div>
        </header>

        {/* Price ticker */}
        {prices && (
          <div className="card px-4 py-2.5 mb-5 flex items-center gap-5 overflow-x-auto text-sm">
            {Object.entries(prices).filter(([k]) => k !== 'timestamp' && k !== 'error').map(([sym, d]: [string, any]) => (
              <div key={sym} className="flex items-center gap-2 min-w-fit">
                <span className="text-zinc-500 text-xs">{sym}</span>
                <span className="text-white font-medium tabular-nums">{fmtUsd(d?.price)}</span>
                {d?.change_24h != null && (
                  <span className={`text-xs tabular-nums ${pctClass(d.change_24h)}`}>
                    {pctSign(d.change_24h)}{d.change_24h?.toFixed(1)}%
                  </span>
                )}
              </div>
            ))}
          </div>
        )}

        {/* Tabs */}
        <nav className="flex items-center gap-1.5 mb-5">
          {tabs.map(t => (
            <button key={t.id} onClick={() => setTab(t.id)}
              className={`btn btn-ghost text-xs px-3 py-1.5 ${tab === t.id ? 'active' : ''}`}>
              {t.label}
            </button>
          ))}
        </nav>

        {/* Content */}
        <div className="fade-in" key={tab}>
          {tab === 'dashboard' && <Dashboard status={status} markets={markets} treasury={treasury} poolPrices={poolPrices} onMarkets={fetchAll} />}
          {tab === 'trade' && <Trade markets={markets} address={address} onTrade={() => { fetchAll(); fetchUser() }} />}
          {tab === 'predict' && <Predict markets={markets} address={address} balance={balance}
            predictions={predictions} scoring={scoring}
            onAction={() => { fetchAll(); fetchUser() }} />}
          {tab === 'portfolio' && <Portfolio positions={positions} stakes={stakes} portfolio={portfolio} balance={balance} address={address} onAction={() => { fetchAll(); fetchUser() }} />}
          {tab === 'leaderboard' && <Leaderboard leaders={leaders} />}
        </div>
      </div>
    </main>
  )
}

/* ─── Dashboard ───────────────────────────────────────────────── */

function Dashboard({ status, markets, treasury, poolPrices, onMarkets }: any) {
  const s = status || {}
  const t = treasury || {}
  const [addOpen, setAddOpen] = useState(false)
  return (
    <div className="space-y-5">
      {/* Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Stat label="Treasury" value={fmtUsd(t.balance || 0)} sub="accumulated profit" />
        <Stat label="Volume" value={fmtUsd(s.total_volume || 0)} sub={`${s.positions_total || 0} trades`} />
        <Stat label="PREFI Supply" value={fmt(t.prefi_supply || 0, 0)}
          sub={`${fmt(s.total_prefi_minted || 0, 0)} minted · ${fmt(s.total_prefi_burned || 0, 0)} burned`} />
        <Stat label="Predictions" value={String(s.predictions_total || 0)}
          sub={`${s.predictions_open || 0} open · ${s.forecasters || 0} forecasters`} />
      </div>

      {/* Markets */}
      <Section title="Markets" count={markets?.length} action={
        <button onClick={() => setAddOpen(o => !o)} className="btn btn-ghost text-[10px] px-2 py-1">
          {addOpen ? 'Close' : '+ Hyperliquid'}
        </button>
      }>
        {addOpen && <HyperliquidAdd onAdded={() => { onMarkets?.(); }} />}
        {markets?.length > 0 ? (
          <div className="space-y-0">
            <div className="grid grid-cols-[1fr_100px_80px_80px_80px] gap-2 px-4 py-2 text-xs text-zinc-500 font-medium">
              <span>Asset</span><span className="text-right">Price</span>
              <span className="text-right">Volume</span><span className="text-right">Trades</span>
              <span className="text-right">Win Rate</span>
            </div>
            {markets.map((m: any, i: number) => (
              <div key={i} className="table-row grid-cols-[1fr_100px_80px_80px_80px] gap-2">
                <div className="flex items-center gap-2.5">
                  <div className="w-7 h-7 rounded-full bg-blue-500/10 border border-blue-500/20 flex items-center justify-center text-[10px] font-bold text-blue-400">
                    {m.symbol?.[0]}
                  </div>
                  <div>
                    <div className="text-sm text-white font-medium">
                      {m.symbol}{m.source === 'hyperliquid' ? '-PERP' : '/USDC'}
                    </div>
                    <div className="text-[10px] text-zinc-500">
                      {m.source === 'hyperliquid'
                        ? 'Hyperliquid perp'
                        : `Uniswap V3 · fee ${(m.fee_tier / 10000).toFixed(1)}%`}
                    </div>
                  </div>
                </div>
                <span className="text-right text-sm text-white tabular-nums font-medium">
                  {m.price_usd ? fmtUsd(m.price_usd) : '—'}
                </span>
                <span className="text-right text-xs text-zinc-400 tabular-nums">{fmtUsd(m.total_volume || 0, 0)}</span>
                <span className="text-right text-xs text-zinc-400 tabular-nums">{m.total_positions || 0}</span>
                <span className={`text-right text-xs tabular-nums ${(m.win_rate || 0) >= 50 ? 'up' : 'text-zinc-400'}`}>
                  {m.win_rate || 0}%
                </span>
              </div>
            ))}
          </div>
        ) : (
          <Empty msg="No markets added yet" />
        )}
      </Section>

      {/* Uniswap Pool Prices */}
      <Section title="Uniswap Base Pools" count={poolPrices?.length} sub="live on-chain">
        {poolPrices?.length > 0 ? (
          <div className="space-y-0">
            <div className="grid grid-cols-[1fr_100px_100px_80px] gap-2 px-4 py-2 text-xs text-zinc-500 font-medium">
              <span>Token</span><span className="text-right">Price</span>
              <span className="text-right">Volume (24h)</span><span className="text-right">Liquidity</span>
            </div>
            {poolPrices.slice(0, 15).map((t: any, i: number) => (
              <div key={i} className="table-row grid-cols-[1fr_100px_100px_80px] gap-2">
                <div className="flex items-center gap-2">
                  <div className="w-6 h-6 rounded-full bg-zinc-800 border border-zinc-700 flex items-center justify-center text-[9px] font-bold text-zinc-300">
                    {(t.symbol || '?')[0]}
                  </div>
                  <div>
                    <span className="text-sm text-white font-medium">{t.symbol || t.id?.slice(0,8)}</span>
                    {t.name && <span className="text-[10px] text-zinc-500 ml-1.5">{t.name}</span>}
                  </div>
                </div>
                <span className="text-right text-sm text-white tabular-nums">
                  {t.derivedETH ? fmtUsd(parseFloat(t.derivedETH) * 2500, t.derivedETH > 0.001 ? 2 : 6) : '—'}
                </span>
                <span className="text-right text-xs text-zinc-400 tabular-nums">
                  {t.volumeUSD ? fmtUsd(parseFloat(t.volumeUSD), 0) : '—'}
                </span>
                <span className="text-right text-xs text-zinc-400 tabular-nums">
                  {t.totalValueLockedUSD ? fmtUsd(parseFloat(t.totalValueLockedUSD), 0) : '—'}
                </span>
              </div>
            ))}
          </div>
        ) : (
          <div className="px-4 py-8 text-center text-xs text-zinc-500">
            Pool data off — point NEXT_PUBLIC_UNISWAP_API at a running uniswap module
          </div>
        )}
      </Section>

      {/* Treasury */}
      <Section title="Treasury">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 p-4">
          <MiniStat label="Balance" value={fmtUsd(t.balance || 0)} />
          <MiniStat label="Total Captured" value={fmtUsd(t.total_captured || 0)} />
          <MiniStat label="Distributed" value={fmtUsd(t.total_distributed || 0)} />
          <MiniStat label="Epoch" value={String(t.current_epoch || 0)} />
        </div>
      </Section>
    </div>
  )
}

/* ─── Trade ───────────────────────────────────────────────────── */

function Trade({ markets, address, onTrade }: any) {
  const [asset, setAsset] = useState('')
  const [amount, setAmount] = useState('')
  const [loading, setLoading] = useState(false)

  const activeMarkets = (markets || []).filter((m: any) => m.active)

  const handleOpen = async () => {
    if (!address || !asset || !amount) return
    setLoading(true)
    try {
      const r = await fetch(`${API}/position/open?asset=${asset}&amount=${amount}&address=${address}`, { method: 'POST' })
      const d = await r.json()
      if (r.ok) {
        toast.success(`Opened ${asset} position — ${fmt(d.asset_amount, 6)} tokens at ${fmtUsd(d.entry_price)}`)
        setAmount('')
        onTrade?.()
      } else toast.error(d.detail || 'Failed')
    } catch (e: any) { toast.error(e.message) }
    setLoading(false)
  }

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
      {/* Open Position */}
      <Section title="Open Position">
        <div className="p-4 space-y-4">
          <div>
            <label className="text-xs text-zinc-500 mb-1.5 block">Asset</label>
            <select value={asset} onChange={e => setAsset(e.target.value)}
              className="input text-sm">
              <option value="">Select market...</option>
              {activeMarkets.map((m: any) => (
                <option key={m.symbol} value={m.symbol}>
                  {m.symbol} {m.price_usd ? `— ${fmtUsd(m.price_usd)}` : ''}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="text-xs text-zinc-500 mb-1.5 block">Amount (USDC)</label>
            <input type="number" value={amount} onChange={e => setAmount(e.target.value)}
              placeholder="0.00" className="input text-lg font-medium tabular-nums" />
          </div>
          {asset && amount && (
            <div className="card px-3 py-2.5 text-xs text-zinc-400 space-y-1">
              <div className="flex justify-between">
                <span>Entry Price</span>
                <span className="text-white">{activeMarkets.find((m:any) => m.symbol === asset)?.price_usd ? fmtUsd(activeMarkets.find((m:any) => m.symbol === asset).price_usd) : '—'}</span>
              </div>
              <div className="flex justify-between">
                <span>If profitable</span>
                <span className="up">profit → treasury, you get PREFI</span>
              </div>
              <div className="flex justify-between">
                <span>If loss</span>
                <span className="down">you absorb the loss, no PREFI</span>
              </div>
            </div>
          )}
          <button onClick={handleOpen} disabled={!address || !asset || !amount || loading}
            className="btn btn-blue w-full py-3 text-sm font-semibold">
            {loading ? <div className="spinner" /> : 'Open Position'}
          </button>
          {!address && <p className="text-xs text-zinc-500 text-center">Connect wallet to trade</p>}
        </div>
      </Section>

      {/* Market Prices */}
      <Section title="Market Prices">
        <div className="divide-y divide-white/[0.04]">
          {activeMarkets.length > 0 ? activeMarkets.map((m: any, i: number) => (
            <div key={i} className="px-4 py-3.5 flex items-center justify-between">
              <div className="flex items-center gap-2.5">
                <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-blue-500/15 to-blue-600/5 border border-blue-500/15 flex items-center justify-center text-xs font-bold text-blue-400">
                  {m.symbol?.[0]}
                </div>
                <div>
                  <div className="text-sm font-medium text-white">{m.symbol}</div>
                  <div className="text-[10px] text-zinc-500">Uniswap V3 · {(m.fee_tier/10000).toFixed(1)}% fee</div>
                </div>
              </div>
              <div className="text-right">
                <div className="text-sm font-semibold text-white tabular-nums">{m.price_usd ? fmtUsd(m.price_usd) : '—'}</div>
                <div className="text-[10px] text-zinc-500">{m.total_positions || 0} trades</div>
              </div>
            </div>
          )) : <Empty msg="No markets — add one via CLI" />}
        </div>
      </Section>
    </div>
  )
}

/* ─── Hyperliquid asset add ───────────────────────────────────── */

function HyperliquidAdd({ onAdded }: { onAdded: () => void }) {
  const [search, setSearch] = useState('')
  const [assets, setAssets] = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  const [adding, setAdding] = useState('')

  useEffect(() => {
    let live = true
    setLoading(true)
    const t = setTimeout(async () => {
      const d = await get(`${API}/hyperliquid/assets?search=${encodeURIComponent(search)}&limit=24`)
      if (live) { setAssets(d || []); setLoading(false) }
    }, 250)
    return () => { live = false; clearTimeout(t) }
  }, [search])

  const add = async (coin: string) => {
    setAdding(coin)
    try {
      const r = await fetch(`${API}/hyperliquid/add?coin=${coin}`, { method: 'POST' })
      const d = await r.json()
      if (r.ok) {
        toast.success(`${coin} listed — priced from Hyperliquid`)
        setAssets(a => a.map(x => x.coin === coin ? { ...x, listed: true } : x))
        onAdded()
      } else toast.error(d.detail || 'Failed')
    } catch (e: any) { toast.error(e.message) }
    setAdding('')
  }

  return (
    <div className="px-4 py-3 border-b border-white/[0.04] space-y-3">
      <div className="flex items-center gap-2">
        <input value={search} onChange={e => setSearch(e.target.value)}
          placeholder="Search the Hyperliquid perp universe — BTC, SOL, HYPE…"
          className="input text-sm py-2" />
      </div>
      {loading ? (
        <div className="text-xs text-zinc-500 py-2">Loading assets…</div>
      ) : assets.length === 0 ? (
        <div className="text-xs text-zinc-500 py-2">
          No match. If this stays empty the Hyperliquid feed is unreachable — prefi
          reads it through the local <span className="text-zinc-400">hyperliquid</span> module.
        </div>
      ) : (
        <div className="flex flex-wrap gap-1.5">
          {assets.map(a => (
            <button key={a.coin} disabled={a.listed || adding === a.coin}
              onClick={() => add(a.coin)}
              className={`btn text-[11px] px-2.5 py-1.5 ${a.listed ? 'btn-ghost opacity-50' : 'btn-ghost'}`}>
              <span className="text-white font-medium">{a.coin}</span>
              <span className="text-zinc-500 tabular-nums">{fmtUsd(a.price, a.price < 1 ? 4 : 2)}</span>
              {a.listed && <span className="text-emerald-400">✓</span>}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

/* ─── Predict ─────────────────────────────────────────────────── */

const HORIZONS = [
  { label: '1 day', value: 86400 },
  { label: '12 hours', value: 43200 },
  { label: '1 hour', value: 3600 },
  { label: '3 days', value: 259200 },
  { label: '1 week', value: 604800 },
]

function Predict({ markets, address, balance, predictions, scoring, onAction }: any) {
  const active = (markets || []).filter((m: any) => m.active)
  const params = scoring?.active
  const [asset, setAsset] = useState('')
  const [price, setPrice] = useState('')
  const [burn, setBurn] = useState('')
  const [horizon, setHorizon] = useState(String(params?.horizon || 86400))
  const [loading, setLoading] = useState(false)
  const [board, setBoard] = useState<any[]>([])

  const market = active.find((m: any) => m.symbol === asset)
  const spot = market?.price_usd
  const available = balance?.available ?? 0

  useEffect(() => { get(`${API}/predictions/board`).then(d => setBoard(d || [])) }, [predictions])

  // Seed the price box with spot so the input is an edit, not a blank guess.
  useEffect(() => { if (spot) setPrice(String(spot)) }, [spot])

  const move = spot && price ? (parseFloat(price) - spot) / spot * 100 : null
  const maxPayout = burn && params ? parseFloat(burn) * params.multiplier : 0

  const submit = async () => {
    if (!address || !asset || !price || !burn) return
    setLoading(true)
    try {
      const r = await fetch(`${API}/predict?asset=${asset}&predicted_price=${price}` +
        `&burn=${burn}&address=${address}&horizon=${horizon}`, { method: 'POST' })
      const d = await r.json()
      if (r.ok) {
        toast.success(`Burned ${burn} PREFI on ${asset} @ ${fmtUsd(parseFloat(price))} — ` +
          `resolves ${new Date(d.resolves_at).toLocaleString()}`)
        setBurn('')
        onAction?.()
      } else toast.error(d.detail || 'Failed')
    } catch (e: any) { toast.error(e.message) }
    setLoading(false)
  }

  // Everything here except placing a call is public — the scoring params and
  // the forecaster board are the protocol's rules and results, not your data.
  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Stat label="PREFI Available" value={fmt(available, 2)} sub="earned by trading" />
        <Stat label="Burned" value={fmt(balance?.burned || 0, 2)} sub="on predictions" />
        <Stat label="Won Back" value={fmt(balance?.from_predictions || 0, 2)} sub="scored payouts" />
        <Stat label="Model" value={params?.model || '—'}
          sub={params ? `tolerance ${(params.tolerance * 100).toFixed(2)}% · ${params.multiplier}× max` : ''} />
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
        {/* Place a call */}
        <Section title="Call the price" sub="burn PREFI, get scored on the miss">
          <div className="p-4 space-y-4">
            <div>
              <label className="text-xs text-zinc-500 mb-1.5 block">Asset</label>
              <select value={asset} onChange={e => setAsset(e.target.value)} className="input text-sm">
                <option value="">Select market...</option>
                {active.map((m: any) => (
                  <option key={m.symbol} value={m.symbol}>
                    {m.symbol} {m.price_usd ? `— ${fmtUsd(m.price_usd, m.price_usd < 1 ? 4 : 2)}` : ''}
                    {m.source === 'hyperliquid' ? ' (HL)' : ''}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label className="text-xs text-zinc-500 mb-1.5 block">
                Price at resolution {spot ? <span className="text-zinc-600">· now {fmtUsd(spot, spot < 1 ? 4 : 2)}</span> : null}
              </label>
              <input type="number" value={price} onChange={e => setPrice(e.target.value)}
                placeholder="0.00" className="input text-lg font-medium tabular-nums" />
              {spot && (
                <div className="flex gap-1.5 mt-2">
                  {[-5, -2, -1, 1, 2, 5].map(pct => (
                    <button key={pct} onClick={() => setPrice((spot * (1 + pct / 100)).toPrecision(8))}
                      className="btn btn-ghost text-[10px] px-2 py-1">
                      {pct > 0 ? '+' : ''}{pct}%
                    </button>
                  ))}
                </div>
              )}
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs text-zinc-500 mb-1.5 block">Burn (PREFI)</label>
                <input type="number" value={burn} onChange={e => setBurn(e.target.value)}
                  placeholder={String(params?.min_burn ?? 1)} className="input text-sm tabular-nums" />
              </div>
              <div>
                <label className="text-xs text-zinc-500 mb-1.5 block">Horizon</label>
                <select value={horizon} onChange={e => setHorizon(e.target.value)} className="input text-sm">
                  {HORIZONS.map(h => <option key={h.value} value={h.value}>{h.label}</option>)}
                </select>
              </div>
            </div>

            {asset && price && burn && (
              <div className="card px-3 py-2.5 text-xs text-zinc-400 space-y-1">
                <div className="flex justify-between">
                  <span>Implied move</span>
                  <span className={move != null ? pctClass(move) : ''}>
                    {move != null ? `${pctSign(move)}${move.toFixed(2)}%` : '—'}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span>Burned now</span>
                  <span className="down">−{fmt(parseFloat(burn), 2)} PREFI</span>
                </div>
                <div className="flex justify-between">
                  <span>Max payout (exact call)</span>
                  <span className="up">+{fmt(maxPayout, 2)} PREFI</span>
                </div>
              </div>
            )}

            <button onClick={submit}
              disabled={!address || !asset || !price || !burn || loading || available <= 0}
              className="btn btn-blue w-full py-3 text-sm font-semibold">
              {loading ? <div className="spinner" /> : 'Burn & Predict'}
            </button>
            {!address ? (
              <p className="text-xs text-zinc-500 text-center">Connect wallet to predict</p>
            ) : available <= 0 && (
              <p className="text-xs text-zinc-500 text-center">
                No PREFI yet — close a profitable trade to mint some.
              </p>
            )}
          </div>
        </Section>

        {/* Scoring */}
        <ScoringPanel scoring={scoring} onSaved={onAction} />
      </div>

      {/* Your predictions */}
      <Section title="Your predictions" count={predictions?.length}>
        <div className="divide-y divide-white/[0.04] max-h-[420px] overflow-y-auto">
          {predictions?.length > 0
            ? predictions.map((p: any) => <PredictionRow key={p.id} p={p} />)
            : <Empty msg="No predictions yet" />}
        </div>
      </Section>

      {/* Forecasters */}
      <Section title="Forecasters" count={board?.length} sub="ranked by average score">
        {board?.length > 0 ? (
          <div>
            <div className="grid grid-cols-[40px_1fr_80px_80px_90px_90px] gap-2 px-4 py-2 text-xs text-zinc-500 font-medium">
              <span>#</span><span>Forecaster</span><span className="text-right">Calls</span>
              <span className="text-right">Avg score</span><span className="text-right">Burned</span>
              <span className="text-right">Net PREFI</span>
            </div>
            {board.map((f: any) => (
              <div key={f.rank} className="table-row grid-cols-[40px_1fr_80px_80px_90px_90px] gap-2">
                <span className={`text-sm font-bold ${f.rank <= 3 ? 'text-amber-400' : 'text-zinc-500'}`}>{f.rank}</span>
                <span className="text-sm text-white font-mono truncate">{f.address}</span>
                <span className="text-right text-xs text-zinc-400 tabular-nums">{f.resolved}/{f.predictions}</span>
                <span className="text-right text-sm text-white tabular-nums">{(f.avg_score * 100).toFixed(1)}%</span>
                <span className="text-right text-xs text-zinc-400 tabular-nums">{fmt(f.total_burned, 2)}</span>
                <span className={`text-right text-sm tabular-nums ${f.net_prefi >= 0 ? 'up' : 'down'}`}>
                  {f.net_prefi >= 0 ? '+' : ''}{fmt(f.net_prefi, 2)}
                </span>
              </div>
            ))}
          </div>
        ) : <Empty msg="No predictions yet" />}
      </Section>
    </div>
  )
}

function PredictionRow({ p }: { p: any }) {
  const open = !p.resolved
  const projected = p.projected?.score
  const countdown = (s: number) =>
    s >= 86400 ? `${Math.ceil(s / 86400)}d` : s >= 3600 ? `${Math.ceil(s / 3600)}h` : `${Math.ceil(s / 60)}m`

  return (
    <div className="px-4 py-3 flex items-center justify-between gap-3">
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium text-white">{p.asset}</span>
          <span className={`tag text-[9px] ${open ? 'bg-blue-500/10 text-blue-400'
            : p.net >= 0 ? 'bg-emerald-500/10 text-emerald-400' : 'bg-red-500/10 text-red-400'}`}>
            {open ? `OPEN · ${countdown(p.seconds_remaining)}` : `SCORE ${(p.score * 100).toFixed(1)}%`}
          </span>
          {p.price_mode === 'spot' && (
            <span className="tag text-[9px] bg-amber-500/10 text-amber-400" title="No historical price was available — settled at the spot price when it resolved">
              SPOT
            </span>
          )}
        </div>
        <div className="text-[10px] text-zinc-500 mt-0.5">
          called {fmtUsd(p.predicted_price, p.predicted_price < 1 ? 4 : 2)}
          {' · '}from {fmtUsd(p.entry_price, p.entry_price < 1 ? 4 : 2)}
          {p.resolved && <> · landed {fmtUsd(p.actual_price, p.actual_price < 1 ? 4 : 2)}</>}
          {' · '}{p.params?.model} @ {(p.params?.tolerance * 100).toFixed(2)}%
        </div>
      </div>
      <div className="text-right shrink-0">
        {p.resolved ? (
          <>
            <div className={`text-sm font-medium tabular-nums ${p.net >= 0 ? 'up' : 'down'}`}>
              {p.net >= 0 ? '+' : ''}{fmt(p.net, 2)} PREFI
            </div>
            <div className="text-[10px] text-zinc-500 tabular-nums">
              off by {fmtUsd(p.abs_error, p.abs_error < 1 ? 4 : 2)} ({(p.normalized_error * 100).toFixed(2)}%)
            </div>
          </>
        ) : (
          <>
            <div className="text-sm text-zinc-300 tabular-nums">−{fmt(p.burn, 2)} burned</div>
            <div className="text-[10px] text-zinc-500 tabular-nums">
              {projected != null ? `would score ${(projected * 100).toFixed(1)}%` : 'awaiting price'}
            </div>
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
  }, [p?.model, p?.tolerance])

  if (!p) return <Section title="Scoring"><Empty msg="Loading scoring params" /></Section>

  const set = (k: string, v: any) => setDraft({ ...p, [k]: v })

  const save = async () => {
    const q = new URLSearchParams({
      model: p.model, tolerance: String(p.tolerance), multiplier: String(p.multiplier),
      horizon: String(p.horizon), min_burn: String(p.min_burn),
    })
    try {
      const r = await fetch(`${API}/scoring?${q}`, { method: 'POST' })
      const d = await r.json()
      if (r.ok) { toast.success('Scoring updated — applies to new predictions'); setDraft(null); onSaved?.() }
      else toast.error(d.detail || 'Failed')
    } catch (e: any) { toast.error(e.message) }
  }

  return (
    <Section title="Scoring" sub="score = f(|called − actual| / actual)">
      <div className="p-4 space-y-4">
        <div>
          <label className="text-xs text-zinc-500 mb-1.5 block">Model</label>
          <select value={p.model} onChange={e => set('model', e.target.value)} className="input text-sm">
            {Object.keys(models).map(m => <option key={m} value={m}>{m}</option>)}
          </select>
          <p className="text-[10px] text-zinc-500 mt-1.5 leading-relaxed">{models[p.model]}</p>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="text-xs text-zinc-500 mb-1.5 block">Tolerance (%)</label>
            <input type="number" step="0.1" value={(p.tolerance * 100).toFixed(2)}
              onChange={e => set('tolerance', Math.max(1e-6, parseFloat(e.target.value || '0') / 100))}
              className="input text-sm tabular-nums" />
          </div>
          <div>
            <label className="text-xs text-zinc-500 mb-1.5 block">Payout multiplier</label>
            <input type="number" step="0.5" value={p.multiplier}
              onChange={e => set('multiplier', parseFloat(e.target.value || '0'))}
              className="input text-sm tabular-nums" />
          </div>
          <div>
            <label className="text-xs text-zinc-500 mb-1.5 block">Default horizon</label>
            <select value={p.horizon} onChange={e => set('horizon', parseInt(e.target.value))}
              className="input text-sm">
              {HORIZONS.map(h => <option key={h.value} value={h.value}>{h.label}</option>)}
            </select>
          </div>
          <div>
            <label className="text-xs text-zinc-500 mb-1.5 block">Min burn</label>
            <input type="number" step="1" value={p.min_burn}
              onChange={e => set('min_burn', parseFloat(e.target.value || '0'))}
              className="input text-sm tabular-nums" />
          </div>
        </div>

        {/* What the curve pays, straight from the API scorer */}
        <div>
          <div className="text-[10px] text-zinc-500 uppercase tracking-wider mb-2">
            Score by miss size
          </div>
          <div className="space-y-1">
            {curve.map(row => (
              <div key={row.miss} className="flex items-center gap-2">
                <span className="text-[10px] text-zinc-500 w-10 text-right tabular-nums">
                  {(row.miss * 100).toFixed(1)}%
                </span>
                <div className="flex-1 h-1.5 rounded-full bg-white/[0.04] overflow-hidden">
                  <div className="h-full rounded-full bg-blue-500/70"
                    style={{ width: `${Math.max(0, Math.min(1, row.score)) * 100}%` }} />
                </div>
                <span className="text-[10px] text-zinc-400 w-10 tabular-nums">
                  {(row.score * 100).toFixed(0)}%
                </span>
              </div>
            ))}
          </div>
        </div>

        <button onClick={save} disabled={!draft} className="btn btn-green w-full py-2.5 text-sm">
          {draft ? 'Save scoring params' : 'Saved'}
        </button>
        <p className="text-[10px] text-zinc-500 text-center">
          Open predictions keep the params they were placed under.
        </p>
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

  if (!address) return <div className="card-glow p-12 text-center text-zinc-500">Connect wallet to view portfolio</div>

  return (
    <div className="space-y-5">
      {/* Summary */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <Stat label="Net P&L" value={fmtUsd(trading.net_pnl || 0)} color={trading.net_pnl >= 0 ? 'up' : 'down'} />
        <Stat label="Volume" value={fmtUsd(trading.total_volume || 0)} />
        <Stat label="Win Rate" value={`${trading.win_rate || 0}%`} />
        <Stat label="PREFI Available" value={fmt(balance?.available || 0, 2)}
          sub={`${fmt(balance?.minted || 0, 2)} minted`} />
        <Stat label="PREFI Locked" value={fmt(prefi.total_locked || 0, 2)}
          sub={`${fmt(balance?.burned || 0, 2)} burned`} />
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
        {/* Positions */}
        <Section title="Positions" count={positions?.length}>
          <div className="divide-y divide-white/[0.04] max-h-[400px] overflow-y-auto">
            {positions?.length > 0 ? positions.map((p: any) => (
              <div key={p.id} className="px-4 py-3 flex items-center justify-between">
                <div>
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-white">{p.asset}</span>
                    <span className={`tag text-[9px] ${p.closed ? (p.profit > 0 ? 'bg-emerald-500/10 text-emerald-400' : 'bg-red-500/10 text-red-400') : 'bg-blue-500/10 text-blue-400'}`}>
                      {p.closed ? (p.profit > 0 ? 'WIN' : 'LOSS') : 'OPEN'}
                    </span>
                  </div>
                  <div className="text-[10px] text-zinc-500 mt-0.5">
                    {fmtUsd(p.usdc_in)} in · {p.closed ? `${fmtUsd(p.usdc_out)} out` : `${fmt(p.asset_amount, 6)} tokens`}
                  </div>
                </div>
                <div className="text-right">
                  {p.closed ? (
                    <div className={`text-sm font-medium tabular-nums ${p.profit > 0 ? 'up' : 'down'}`}>
                      {p.profit > 0 ? '+' : ''}{fmtUsd(p.profit)}
                    </div>
                  ) : (
                    <button onClick={() => handleClose(p.id)} className="btn btn-ghost text-[10px] px-2 py-1">Close</button>
                  )}
                  {p.prefi_earned > 0 && (
                    <div className="text-[10px] text-emerald-400">+{fmt(p.prefi_earned, 2)} PREFI</div>
                  )}
                </div>
              </div>
            )) : <Empty msg="No positions yet" />}
          </div>
        </Section>

        {/* Staking */}
        <Section title="Stake PREFI">
          <div className="p-4 space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs text-zinc-500 mb-1 block">Amount</label>
                <input type="number" value={lockAmt} onChange={e => setLockAmt(e.target.value)}
                  placeholder="0" className="input text-sm tabular-nums" />
              </div>
              <div>
                <label className="text-xs text-zinc-500 mb-1 block">Lock (weeks)</label>
                <select value={lockWeeks} onChange={e => setLockWeeks(e.target.value)} className="input text-sm">
                  {[1,2,4,8,13,26,52].map(w => (
                    <option key={w} value={w}>{w}w — {w}x weight</option>
                  ))}
                </select>
              </div>
            </div>
            {lockAmt && (
              <div className="text-xs text-zinc-500">
                Staketime: <span className="text-white">{fmt(parseFloat(lockAmt || '0') * parseInt(lockWeeks) * 604800, 0)}</span>
              </div>
            )}
            <button onClick={handleLock} disabled={!lockAmt} className="btn btn-green w-full py-2.5 text-sm">
              Lock PREFI
            </button>
          </div>

          {/* Active Stakes */}
          {stakes?.stakes?.length > 0 && (
            <div className="border-t border-white/[0.04] divide-y divide-white/[0.04]">
              {stakes.stakes.filter((s: any) => !s.withdrawn).map((s: any) => (
                <div key={s.id} className="px-4 py-2.5 flex items-center justify-between">
                  <div>
                    <div className="text-sm text-white tabular-nums">{fmt(s.amount, 2)} PREFI</div>
                    <div className="text-[10px] text-zinc-500">
                      {s.is_unlockable ? 'Ready to unlock' : `${Math.ceil(s.time_remaining / 86400)}d remaining`}
                    </div>
                  </div>
                  {s.is_unlockable ? (
                    <button onClick={() => handleUnlock(s.id)} className="btn btn-ghost text-[10px] px-2 py-1">Unlock</button>
                  ) : (
                    <span className="tag bg-zinc-800 text-zinc-400 text-[9px]">Locked</span>
                  )}
                </div>
              ))}
            </div>
          )}

          {/* Claims summary */}
          {claims.total_claimed > 0 && (
            <div className="border-t border-white/[0.04] px-4 py-2.5 text-xs text-zinc-500">
              Total claimed: <span className="text-emerald-400">{fmtUsd(claims.total_claimed)}</span>
              {' '}from {claims.epochs_claimed} epochs
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
    <Section title="Top Traders">
      {leaders?.length > 0 ? (
        <div>
          <div className="grid grid-cols-[40px_1fr_90px_90px_70px_90px] gap-2 px-4 py-2 text-xs text-zinc-500 font-medium">
            <span>#</span><span>Trader</span><span className="text-right">Profit</span>
            <span className="text-right">Volume</span><span className="text-right">Win%</span>
            <span className="text-right">PREFI</span>
          </div>
          {leaders.map((t: any) => (
            <div key={t.rank} className="table-row grid-cols-[40px_1fr_90px_90px_70px_90px] gap-2">
              <span className={`text-sm font-bold ${t.rank <= 3 ? 'text-amber-400' : 'text-zinc-500'}`}>
                {t.rank}
              </span>
              <span className="text-sm text-white font-mono truncate">{t.address}</span>
              <span className={`text-right text-sm tabular-nums font-medium ${t.net_pnl >= 0 ? 'up' : 'down'}`}>
                {t.net_pnl >= 0 ? '+' : ''}{fmtUsd(t.net_pnl)}
              </span>
              <span className="text-right text-xs text-zinc-400 tabular-nums">{fmtUsd(t.total_volume, 0)}</span>
              <span className={`text-right text-xs tabular-nums ${t.win_rate >= 50 ? 'up' : 'text-zinc-400'}`}>
                {t.win_rate}%
              </span>
              <span className="text-right text-xs text-emerald-400 tabular-nums">{fmt(t.prefi_earned, 2)}</span>
            </div>
          ))}
        </div>
      ) : <Empty msg="No trades yet" />}
    </Section>
  )
}

/* ─── Shared Components ───────────────────────────────────────── */

function Section({ title, count, sub, action, children }: { title: string; count?: number; sub?: string; action?: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="card-glow overflow-hidden">
      <div className="px-4 py-3 border-b border-white/[0.04] flex items-center justify-between">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold text-white">{title}</h2>
          {count != null && <span className="tag bg-zinc-800 text-zinc-400 text-[9px]">{count}</span>}
        </div>
        {action || (sub && <span className="text-[10px] text-zinc-500">{sub}</span>)}
      </div>
      {children}
    </div>
  )
}

function Stat({ label, value, sub, color }: { label: string; value: string; sub?: string; color?: string }) {
  return (
    <div className="stat-card">
      <div className="text-[10px] text-zinc-500 uppercase tracking-wider mb-1.5">{label}</div>
      <div className={`text-lg font-bold tabular-nums ${color || 'text-white'}`}>{value}</div>
      {sub && <div className="text-[10px] text-zinc-500 mt-0.5">{sub}</div>}
    </div>
  )
}

function MiniStat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-[10px] text-zinc-500 mb-0.5">{label}</div>
      <div className="text-sm font-semibold text-white tabular-nums">{value}</div>
    </div>
  )
}

function Empty({ msg }: { msg: string }) {
  return <div className="px-4 py-10 text-center text-xs text-zinc-500">{msg}</div>
}
