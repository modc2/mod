"use client"

import { useCallback, useEffect, useMemo, useState } from 'react'

// Every way into TAO from Solana, Base or Ethereum, priced side by side.
//
// The board's job is to not lie about what "best" means. Two rules do most of
// that work, and both are load-bearing:
//
//   * a route is only comparable to routes that deliver the same thing — a
//     Jupiter swap buying SPL TAO on Solana is not competing with a desk that
//     settles a stakeable ss58 balance, it is one hop short of it;
//   * a route that quoted a rate but will refuse your size is not a worse
//     option, it is not an option, and it shows its own minimum instead of
//     a number.

type SourceAsset = {
  key: string
  chain: string
  chain_label: string
  symbol: string
  decimals: number
  contract: string
  wallet: string
}

type BridgeRoute = {
  id: string
  name: string
  kind: string
  custody: string
  delivers: string
  delivers_label: string
  sources: string[]
  live_quote: boolean
  hops: number
  eta: string
  fees: string
  kyc: string
  url: string
  docs: string
  steps: string[]
  notes: string
}

type RouteQuote = {
  id: string
  name: string
  kind: string
  custody: string
  delivers: string
  delivers_label: string
  hops: number
  eta: string
  url: string
  status: 'ok' | 'unavailable' | 'error'
  tao_out?: number
  rate?: number
  vs_mid_pct?: number
  min_in?: number
  max_in?: number
  price_impact_pct?: number
  indicative?: boolean
  detail?: string
}

type BoardResult = {
  asset: string
  chain: string
  symbol: string
  amount: number
  mid_tao?: number
  mid_stale?: boolean
  routes: RouteQuote[]
  best_native?: string
  manual: BridgeRoute[]
  ts: number
}

type Catalog = {
  assets: SourceAsset[]
  routes: BridgeRoute[]
  coverage: Record<string, string[]>
  tao_solana_mint: string
  bittensor_evm: { chain_id: number; chain_id_hex: string; rpc: string; explorer: string }
}

const CHAIN_ORDER = ['solana', 'base', 'ethereum']
const CHAIN_LABEL: Record<string, string> = {
  solana: 'Solana',
  base: 'Base',
  ethereum: 'Ethereum',
}

const CUSTODY_LABEL: Record<string, string> = {
  non_custodial: 'non-custodial',
  swap_desk: 'swap desk holds funds in transit',
  trusted_operator: 'trusted operator',
  exchange: 'exchange account',
}

const KIND_LABEL: Record<string, string> = {
  instant_swap: 'instant swap',
  onchain_bridge: 'on-chain bridge',
  dex: 'DEX',
  cex: 'exchange',
  desk: 'desk',
}

// Sensible starting sizes — small enough to be a realistic first bridge,
// large enough to clear most desks' minimums.
const DEFAULT_AMOUNT: Record<string, string> = {
  SOL: '10', ETH: '1', USDC: '1000', USDT: '1000',
}

function fmtTao(n: number | undefined): string {
  if (n === undefined || n === null) return '—'
  if (n >= 1000) return n.toFixed(2)
  if (n >= 1) return n.toFixed(4)
  return n.toFixed(6)
}

function fmtPct(n: number | undefined): string {
  if (n === undefined || n === null) return '—'
  return `${n >= 0 ? '+' : ''}${n.toFixed(2)}%`
}

export default function BridgeBoard({ apiBase }: { apiBase: string }) {
  const [catalog, setCatalog] = useState<Catalog | null>(null)
  const [chain, setChain] = useState<string>('solana')
  const [assetKey, setAssetKey] = useState<string>('sol:SOL')
  const [amount, setAmount] = useState<string>('10')
  const [board, setBoard] = useState<BoardResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string>('')
  const [openRoute, setOpenRoute] = useState<string>('')

  useEffect(() => {
    fetch(`${apiBase}/bridges`)
      .then(r => r.json())
      .then(setCatalog)
      .catch(e => setErr(`Failed to load the bridge catalog: ${e.message || e}`))
  }, [apiBase])

  const assets = catalog?.assets || []
  const chainAssets = useMemo(
    () => assets.filter(a => a.chain === chain),
    [assets, chain],
  )
  const asset = assets.find(a => a.key === assetKey)

  // Keep the asset selection inside the selected chain.
  useEffect(() => {
    if (!chainAssets.length) return
    if (!chainAssets.some(a => a.key === assetKey)) {
      const next = chainAssets[0]
      setAssetKey(next.key)
      setAmount(DEFAULT_AMOUNT[next.symbol] || '1')
      setBoard(null)
    }
  }, [chain, chainAssets, assetKey])

  const compare = useCallback(async () => {
    setErr(''); setBusy(true); setOpenRoute('')
    try {
      const res = await fetch(`${apiBase}/bridges/quote`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ asset: assetKey, amount: Number(amount) }),
      })
      if (!res.ok) {
        const j = await res.json().catch(() => ({} as any))
        throw new Error(j?.detail || `HTTP ${res.status}`)
      }
      setBoard(await res.json())
    } catch (e: any) {
      setErr(e.message || String(e))
    } finally {
      setBusy(false)
    }
  }, [apiBase, assetKey, amount])

  const catalogRoute = (id: string) => catalog?.routes.find(r => r.id === id)

  const native = (board?.routes || []).filter(r => r.delivers === 'native_ss58')
  const other = (board?.routes || []).filter(r => r.delivers !== 'native_ss58')
  const bestOut = native.find(r => r.id === board?.best_native)?.tao_out

  return (
    <div>
      <div className="muted" style={{ marginBottom: 14 }}>
        Every route from Solana, Base or Ethereum into TAO, priced live and ranked.
        Routes that settle native TAO to an ss58 coldkey are listed first — they
        are the only ones that finish the job in one step.
      </div>

      {/* ── picker ── */}
      <div className="h2" style={{ marginTop: 0 }}>What are you bridging from?</div>
      <div className="chiprow">
        {CHAIN_ORDER.map(c => (
          <button
            key={c}
            className={`chip ${chain === c ? 'chip-on' : ''}`}
            onClick={() => { setChain(c); setBoard(null) }}
          >
            {CHAIN_LABEL[c]}
          </button>
        ))}
      </div>
      <div className="chiprow">
        {chainAssets.map(a => (
          <button
            key={a.key}
            className={`chip ${assetKey === a.key ? 'chip-on' : ''}`}
            onClick={() => {
              setAssetKey(a.key)
              setAmount(DEFAULT_AMOUNT[a.symbol] || '1')
              setBoard(null)
            }}
          >
            {a.symbol}
          </button>
        ))}
      </div>
      <div className="row">
        <input
          type="number" step="any" min="0"
          value={amount}
          onChange={e => setAmount(e.target.value)}
          placeholder="amount"
        />
        <span className="unit">{asset?.symbol || ''}</span>
        <button onClick={compare} disabled={busy || !(Number(amount) > 0)}>
          {busy ? 'Pricing…' : 'Compare routes'}
        </button>
      </div>

      {err && <div className="err" style={{ marginTop: 8 }}>{err}</div>}

      {/* ── board ── */}
      {board && (
        <>
          <div className="h2">
            {board.amount} {board.symbol} on {CHAIN_LABEL[board.chain] || board.chain} → TAO
          </div>
          <div className="muted" style={{ marginBottom: 10 }}>
            {board.mid_tao !== undefined
              ? <>Mid-market for this trade is <b>{fmtTao(board.mid_tao)} TAO</b>. Every
                 route costs you something against that — the % column is what.
                 {board.mid_stale ? ' Price feed is stale.' : ''}</>
              : <>Price feed unavailable, so routes are compared to each other rather
                 than to a mid.</>}
          </div>

          {!board.best_native && (
            <div className="warn" style={{ marginBottom: 10 }}>
              No route will settle native TAO at this size. Each row below shows the
              minimum it needs — raise the amount past the smallest one.
            </div>
          )}

          <div className="secthead">Settles native TAO to your ss58 — one step</div>
          {native.map(r => (
            <QuoteRow
              key={r.id} q={r} best={bestOut} isBest={r.id === board.best_native}
              route={catalogRoute(r.id)}
              open={openRoute === r.id}
              onToggle={() => setOpenRoute(openRoute === r.id ? '' : r.id)}
            />
          ))}

          {other.length > 0 && (
            <>
              <div className="secthead">
                Leaves you one hop short — needs a further bridge to reach ss58
              </div>
              {other.map(r => (
                <QuoteRow
                  key={r.id} q={r} best={undefined} isBest={false}
                  route={catalogRoute(r.id)}
                  open={openRoute === r.id}
                  onToggle={() => setOpenRoute(openRoute === r.id ? '' : r.id)}
                />
              ))}
            </>
          )}

          {board.manual.length > 0 && (
            <>
              <div className="secthead">
                Also available for {board.symbol} — can&apos;t be priced from here
              </div>
              {board.manual.map(r => (
                <ManualRow
                  key={r.id} route={r}
                  open={openRoute === r.id}
                  onToggle={() => setOpenRoute(openRoute === r.id ? '' : r.id)}
                />
              ))}
            </>
          )}
        </>
      )}

      {/* ── reference ── */}
      {catalog && (
        <>
          <div className="secthead">Addresses worth checking against</div>
          <div className="kv"><span className="k">Canonical TAO on Solana</span></div>
          <div className="code">{catalog.tao_solana_mint}</div>
          <div className="kv" style={{ marginTop: 8 }}>
            <span className="k">Bittensor EVM</span>
            <span className="v">chain {catalog.bittensor_evm.chain_id} ({catalog.bittensor_evm.chain_id_hex})</span>
          </div>
          <div className="code">{catalog.bittensor_evm.rpc}</div>
        </>
      )}
    </div>
  )
}

function QuoteRow({
  q, best, isBest, route, open, onToggle,
}: {
  q: RouteQuote
  best?: number
  isBest: boolean
  route?: BridgeRoute
  open: boolean
  onToggle: () => void
}) {
  const ok = q.status === 'ok'
  // How much less TAO than the winning native route. Only meaningful between
  // routes that deliver the same thing, so it is passed in rather than derived.
  const behind =
    ok && best !== undefined && q.tao_out !== undefined && best > 0 && !q.indicative
      ? (q.tao_out / best - 1) * 100
      : undefined

  return (
    <div className={`route ${ok ? '' : 'route-off'} ${isBest ? 'route-best' : ''}`}>
      <div className="route-head" onClick={onToggle}>
        <div className="route-name">
          {q.name}
          {isBest && <span className="tag tag-best">best net</span>}
          {q.indicative && <span className="tag tag-warn">indicative</span>}
          {!ok && <span className="tag">{q.status}</span>}
        </div>
        <div className="route-num">
          {ok ? <><b>{fmtTao(q.tao_out)}</b> TAO</> : <span className="muted">—</span>}
        </div>
      </div>

      <div className="route-sub">
        <span className="pill">{KIND_LABEL[q.kind] || q.kind}</span>
        <span className="pill">{q.hops} hop{q.hops === 1 ? '' : 's'}</span>
        <span className="pill">{q.eta}</span>
        {route && <span className="pill">{CUSTODY_LABEL[route.custody] || route.custody}</span>}
        {ok && q.vs_mid_pct !== undefined && (
          <span className="pill">{fmtPct(q.vs_mid_pct)} vs mid</span>
        )}
        {behind !== undefined && behind < -0.001 && (
          <span className="pill">{fmtPct(behind)} vs best</span>
        )}
        {q.price_impact_pct !== undefined && q.price_impact_pct > 0.01 && (
          <span className="pill warn">{q.price_impact_pct.toFixed(2)}% price impact</span>
        )}
      </div>

      {q.detail && <div className={ok ? 'muted' : 'warn'} style={{ marginTop: 4 }}>{q.detail}</div>}
      {!ok && q.min_in !== undefined && !q.detail?.includes('minimum') && (
        <div className="warn" style={{ marginTop: 4 }}>Minimum is {q.min_in}.</div>
      )}

      {open && route && <RouteDetail route={route} />}
      {route && (
        <div className="route-foot">
          <button className="linky" onClick={onToggle}>{open ? 'Hide steps' : 'How it works'}</button>
          {route.url && (
            <a className="linky" href={route.url} target="_blank" rel="noreferrer">Open {route.name} ↗</a>
          )}
        </div>
      )}
    </div>
  )
}

function ManualRow({ route, open, onToggle }: { route: BridgeRoute; open: boolean; onToggle: () => void }) {
  return (
    <div className="route route-manual">
      <div className="route-head" onClick={onToggle}>
        <div className="route-name">{route.name}</div>
        <div className="route-num"><span className="muted">{route.delivers_label}</span></div>
      </div>
      <div className="route-sub">
        <span className="pill">{KIND_LABEL[route.kind] || route.kind}</span>
        <span className="pill">{route.hops} hop{route.hops === 1 ? '' : 's'}</span>
        <span className="pill">{route.eta}</span>
        <span className="pill">{CUSTODY_LABEL[route.custody] || route.custody}</span>
        {route.kyc !== 'none' && <span className="pill warn">KYC</span>}
      </div>
      {open && <RouteDetail route={route} />}
      <div className="route-foot">
        <button className="linky" onClick={onToggle}>{open ? 'Hide steps' : 'How it works'}</button>
        {route.url && <a className="linky" href={route.url} target="_blank" rel="noreferrer">Open ↗</a>}
        {route.docs && <a className="linky" href={route.docs} target="_blank" rel="noreferrer">Docs ↗</a>}
      </div>
    </div>
  )
}

function RouteDetail({ route }: { route: BridgeRoute }) {
  return (
    <div className="route-detail">
      <ol>
        {route.steps.map((s, i) => <li key={i}>{s}</li>)}
      </ol>
      <div className="kv"><span className="k">Delivers</span><span className="v">{route.delivers_label}</span></div>
      <div className="kv"><span className="k">Fees</span><span className="v">{route.fees}</span></div>
      <div className="kv"><span className="k">KYC</span><span className="v">{route.kyc}</span></div>
      {route.notes && <div className="muted" style={{ marginTop: 6 }}>{route.notes}</div>}
    </div>
  )
}
