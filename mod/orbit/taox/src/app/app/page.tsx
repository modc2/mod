"use client"

import { useEffect, useMemo, useState } from 'react'
import BridgeBoard from './BridgeBoard'

function getApiBase(): string {
  if (process.env.NEXT_PUBLIC_API_URL) return process.env.NEXT_PUBLIC_API_URL
  // The container's inner Caddy serves /taox/api whether accessed directly
  // on the container port or via the gateway, so one path works for both.
  return '/taox/api'
}

type SourceCfg = {
  chain: string
  wallet: string
  decimals: number
  deposit_env: string
  coingecko_id: string
  token_contract: string
  asset_type: string
}

type Quote = {
  from: string
  amount_in: number
  rate: number
  gross_tao: number
  fee_bps: number
  fee_tao: number
  tao_out: number
  rates_ts: number
  rates_stale?: boolean
}

type Order = {
  id: string
  state: string
  from: string
  amount_in: number
  source_address: string
  destination_ss58: string
  deposit_address: string
  quoted_rate: number
  quoted_tao_out: number
  source_tx?: string | null
  delivery_tx?: string | null
}

async function api<T = any>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${getApiBase()}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!res.ok) {
    const j = await res.json().catch(() => ({} as any))
    const detail = j?.detail || res.statusText || `HTTP ${res.status}`
    throw new Error(`HTTP ${res.status}: ${detail}`)
  }
  return res.json()
}

declare global {
  interface Window {
    ethereum?: any
    SubWallet?: any
    injectedWeb3?: Record<string, any>
  }
}

function isEvm(a: string) { return /^0x[a-fA-F0-9]{40}$/.test(a.trim()) }
function isSs58(a: string) { return /^[1-9A-HJ-NP-Za-km-z]{45,50}$/.test(a.trim()) }

// EVM networks the user can pick from. Only `ethereum` mainnet is wired up
// to the swap backend today — selecting any other chain triggers a MetaMask
// switch but the swap button stays disabled until they're on a supported chain.
type EvmNetwork = {
  key: string
  label: string
  chainId: string
  supported: boolean
  rpcUrls?: string[]
  blockExplorerUrls?: string[]
  nativeCurrency?: { name: string; symbol: string; decimals: number }
}
const EVM_NETWORKS: EvmNetwork[] = [
  { key: 'ethereum', label: 'Ethereum Mainnet', chainId: '0x1', supported: true },
  { key: 'base',     label: 'Base',             chainId: '0x2105', supported: false,
    rpcUrls: ['https://mainnet.base.org'], blockExplorerUrls: ['https://basescan.org'],
    nativeCurrency: { name: 'Ether', symbol: 'ETH', decimals: 18 } },
  { key: 'arbitrum', label: 'Arbitrum One',     chainId: '0xa4b1', supported: false,
    rpcUrls: ['https://arb1.arbitrum.io/rpc'], blockExplorerUrls: ['https://arbiscan.io'],
    nativeCurrency: { name: 'Ether', symbol: 'ETH', decimals: 18 } },
  { key: 'optimism', label: 'Optimism',         chainId: '0xa', supported: false,
    rpcUrls: ['https://mainnet.optimism.io'], blockExplorerUrls: ['https://optimistic.etherscan.io'],
    nativeCurrency: { name: 'Ether', symbol: 'ETH', decimals: 18 } },
  { key: 'polygon',  label: 'Polygon',          chainId: '0x89', supported: false,
    rpcUrls: ['https://polygon-rpc.com'], blockExplorerUrls: ['https://polygonscan.com'],
    nativeCurrency: { name: 'MATIC', symbol: 'MATIC', decimals: 18 } },
]
const REQUIRED_EVM_CHAIN_ID = '0x1' // Ethereum mainnet — the only chain the backend deposit watcher monitors today.
function networkByChainId(id: string | undefined | null): EvmNetwork | undefined {
  if (!id) return undefined
  const norm = id.toLowerCase()
  return EVM_NETWORKS.find(n => n.chainId.toLowerCase() === norm)
}

const TOKEN_LABEL: Record<string, string> = {
  eth: 'ETH (MetaMask)',
  sol: 'SOL (SubWallet)',
  usdc_eth: 'USDC on Ethereum (MetaMask)',
  usdt_eth: 'USDT on Ethereum (MetaMask)',
  usdc_sol: 'USDC on Solana (SubWallet)',
  usdt_sol: 'USDT on Solana (SubWallet)',
}

function tokenSymbol(key: string): string {
  if (key.startsWith('usdc')) return 'USDC'
  if (key.startsWith('usdt')) return 'USDT'
  return key.toUpperCase()
}

// Encode `transfer(address,uint256)` for an ERC-20 contract call.
function encodeErc20Transfer(to: string, amount: bigint): string {
  const selector = '0xa9059cbb'
  const toPadded = to.toLowerCase().replace(/^0x/, '').padStart(64, '0')
  const amtPadded = amount.toString(16).padStart(64, '0')
  return selector + toPadded + amtPadded
}

// String → base-units BigInt. Avoids `Number * 10**18` floating-point loss,
// which silently mis-sizes ETH/USDC/USDT transfers past ~15 sig figs.
function toBaseUnits(amount: string, decimals: number): bigint {
  const s = String(amount).trim()
  if (!/^\d+(\.\d+)?$/.test(s)) throw new Error(`Invalid amount: ${amount}`)
  const [whole, fracRaw = ''] = s.split('.')
  if (fracRaw.length > decimals) {
    throw new Error(`Amount has ${fracRaw.length} decimal places but token only supports ${decimals}.`)
  }
  const frac = (fracRaw + '0'.repeat(decimals)).slice(0, decimals)
  return BigInt(whole || '0') * (BigInt(10) ** BigInt(decimals)) + BigInt(frac || '0')
}

export default function TaoxPage() {
  // The board comes first: for most people the answer to "get me into TAO" is
  // one of the outside routes, not this deployment's own custodial desk.
  const [tab, setTab] = useState<'bridge' | 'desk'>('bridge')
  const [sources, setSources] = useState<Record<string, SourceCfg>>({})
  const [from, setFrom] = useState<string>('eth')
  const [amount, setAmount] = useState<string>('0.1')

  const [evmAddress, setEvmAddress] = useState<string>('')
  const [evmChainId, setEvmChainId] = useState<string>('')
  const [solAddress, setSolAddress] = useState<string>('')
  const [taoSs58, setTaoSs58] = useState<string>('')

  const [quote, setQuote] = useState<Quote | null>(null)
  const [order, setOrder] = useState<Order | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string>('')
  const [notice, setNotice] = useState<string>('')

  const fromCfg = sources[from]
  const fromChain = fromCfg?.chain || (from === 'sol' ? 'solana' : 'ethereum')
  const sourceAddress = fromChain === 'ethereum' ? evmAddress : solAddress
  const evmNet = networkByChainId(evmChainId)
  const evmChainOk = fromChain !== 'ethereum' || evmChainId.toLowerCase() === REQUIRED_EVM_CHAIN_ID

  useEffect(() => {
    api<Record<string, SourceCfg>>('/sources')
      .then(s => setSources(s))
      .catch(e => setErr(`Failed to load sources: ${e.message || e}`))
  }, [])

  const canQuote = useMemo(() => {
    const n = Number(amount)
    return !!from && n > 0
  }, [from, amount])

  const canSwap = useMemo(() => {
    if (!quote) return false
    if (fromChain === 'ethereum' && !isEvm(evmAddress)) return false
    if (fromChain === 'ethereum' && !evmChainOk) return false
    if (fromChain === 'solana' && !solAddress) return false
    if (!isSs58(taoSs58)) return false
    return true
  }, [quote, fromChain, evmAddress, evmChainOk, solAddress, taoSs58])

  // ── Wallet connectors ─────────────────────────────────────────

  async function connectMetaMask() {
    setErr('')
    try {
      if (!window.ethereum) throw new Error('MetaMask not detected. Install MetaMask.')
      const accs: string[] = await window.ethereum.request({ method: 'eth_requestAccounts' })
      if (accs?.[0]) setEvmAddress(accs[0])
      const cid: string = await window.ethereum.request({ method: 'eth_chainId' })
      setEvmChainId(cid)
    } catch (e: any) { setErr(e.message || String(e)) }
  }

  async function switchEvmNetwork(chainId: string) {
    setErr('')
    if (!window.ethereum) { setErr('MetaMask not detected.'); return }
    const net = EVM_NETWORKS.find(n => n.chainId === chainId)
    try {
      await window.ethereum.request({
        method: 'wallet_switchEthereumChain',
        params: [{ chainId }],
      })
    } catch (e: any) {
      // Chain not in wallet (4902) — try to add it for non-mainnet networks.
      if (e?.code === 4902 && net && net.rpcUrls && net.nativeCurrency) {
        try {
          await window.ethereum.request({
            method: 'wallet_addEthereumChain',
            params: [{
              chainId: net.chainId,
              chainName: net.label,
              rpcUrls: net.rpcUrls,
              blockExplorerUrls: net.blockExplorerUrls,
              nativeCurrency: net.nativeCurrency,
            }],
          })
        } catch (e2: any) { setErr(e2.message || String(e2)); return }
      } else { setErr(e.message || String(e)); return }
    }
    try {
      const cid: string = await window.ethereum.request({ method: 'eth_chainId' })
      setEvmChainId(cid)
    } catch {}
  }

  // Subscribe to MetaMask chain/account changes so the UI reflects manual switches.
  useEffect(() => {
    const eth = (typeof window !== 'undefined') ? window.ethereum : undefined
    if (!eth?.on) return
    const onChain = (cid: string) => setEvmChainId(cid)
    const onAccts = (accs: string[]) => setEvmAddress(accs?.[0] || '')
    eth.on('chainChanged', onChain)
    eth.on('accountsChanged', onAccts)
    eth.request({ method: 'eth_chainId' }).then((cid: string) => setEvmChainId(cid)).catch(() => {})
    return () => {
      eth.removeListener?.('chainChanged', onChain)
      eth.removeListener?.('accountsChanged', onAccts)
    }
  }, [])

  async function connectSubWallet(target: 'tao' | 'sol') {
    setErr('')
    try {
      const inj = (window as any).injectedWeb3
      if (!inj || !inj['subwallet-js']) {
        throw new Error('SubWallet not detected. Install the SubWallet extension.')
      }
      const ext = await inj['subwallet-js'].enable('taox')
      const accs = await ext.accounts.get()
      if (target === 'tao') {
        const sub = accs.find((a: any) => a.type === 'sr25519' || a.type === 'ed25519' || !a.type)
        if (!sub) throw new Error('No Substrate (TAO) account found in SubWallet.')
        setTaoSs58(sub.address)
      } else {
        const sol = accs.find((a: any) => a.type === 'solana' || (a.address?.length || 0) >= 32 && (a.address?.length || 0) <= 44)
        if (!sol) throw new Error('No Solana account found in SubWallet.')
        setSolAddress(sol.address)
      }
    } catch (e: any) { setErr(e.message || String(e)) }
  }

  // ── Actions ───────────────────────────────────────────────────

  async function fetchQuote() {
    setErr(''); setNotice(''); setBusy(true)
    try {
      const q = await api<Quote>('/quote', {
        method: 'POST',
        body: JSON.stringify({ from_token: from, amount: Number(amount) }),
      })
      setQuote(q)
    } catch (e: any) { setErr(e.message || String(e)) }
    finally { setBusy(false) }
  }

  async function startSwap() {
    setErr(''); setNotice(''); setBusy(true)
    try {
      const o = await api<Order>('/swap', {
        method: 'POST',
        body: JSON.stringify({
          from_token: from,
          amount: Number(amount),
          source_address: sourceAddress,
          destination_ss58: taoSs58,
          slippage_bps: 100,
        }),
      })
      setOrder(o)
      setNotice(`Order ${o.id} opened. Send ${amount} ${tokenSymbol(from)} to the deposit address.`)
    } catch (e: any) { setErr(e.message || String(e)) }
    finally { setBusy(false) }
  }

  async function sendDeposit() {
    if (!order || !fromCfg) return
    setErr(''); setBusy(true)
    try {
      let txHash = ''
      const decimals = fromCfg.decimals || 18
      const baseUnits = toBaseUnits(amount, decimals)

      if (fromCfg.chain === 'ethereum') {
        if (!window.ethereum) throw new Error('MetaMask required.')
        const cid: string = await window.ethereum.request({ method: 'eth_chainId' })
        if (cid.toLowerCase() !== REQUIRED_EVM_CHAIN_ID) {
          throw new Error(`MetaMask is on ${networkByChainId(cid)?.label || cid}. Switch to Ethereum Mainnet before sending — the deposit watcher only sees mainnet.`)
        }
        if (fromCfg.asset_type === 'erc20' && fromCfg.token_contract) {
          const data = encodeErc20Transfer(order.deposit_address, baseUnits)
          txHash = await window.ethereum.request({
            method: 'eth_sendTransaction',
            params: [{
              from: evmAddress,
              to: fromCfg.token_contract,
              value: '0x0',
              data,
            }],
          })
        } else {
          // Native ETH
          const valueHex = '0x' + baseUnits.toString(16)
          txHash = await window.ethereum.request({
            method: 'eth_sendTransaction',
            params: [{
              from: evmAddress,
              to: order.deposit_address,
              value: valueHex,
            }],
          })
        }
      } else {
        // Solana (native SOL or SPL): SubWallet handling varies by token.
        // User signs in SubWallet and pastes the resulting tx signature.
        const sym = tokenSymbol(from)
        const t = window.prompt(`Send ${amount} ${sym} from SubWallet to ${order.deposit_address}, then paste the tx signature:`)
        if (!t) throw new Error('cancelled')
        txHash = t.trim()
      }
      const updated = await api<Order>(`/order/${order.id}/confirm`, {
        method: 'POST',
        body: JSON.stringify({ source_tx: txHash }),
      })
      setOrder(updated)
      setNotice(`Deposit recorded: ${txHash}`)
    } catch (e: any) { setErr(e.message || String(e)) }
    finally { setBusy(false) }
  }

  async function refreshOrder() {
    if (!order) return
    try {
      const o = await api<Order>(`/order/${order.id}`)
      setOrder(o)
    } catch (e: any) { setErr(e.message || String(e)) }
  }

  useEffect(() => {
    if (!order || order.state === 'completed' || order.state === 'cancelled') return
    const t = setInterval(refreshOrder, 8000)
    return () => clearInterval(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [order?.id, order?.state])

  const sourceKeys = Object.keys(sources).length ? Object.keys(sources) : ['eth', 'sol']

  return (
    <div className="wrap-wide">
      <div className="card">
        <div className="h1">Taox</div>
        <div className="muted">Get into TAO from Solana, Base or Ethereum.</div>

        <div className="tabs">
          <button className={tab === 'bridge' ? 'on' : ''} onClick={() => setTab('bridge')}>Bridge in</button>
          <button className={tab === 'desk' ? 'on' : ''} onClick={() => setTab('desk')}>This desk</button>
        </div>
        <hr />

        {tab === 'bridge' && <BridgeBoard apiBase={getApiBase()} />}

        {tab === 'desk' && (<>
        <div className="muted">
          This module&apos;s own custodial desk: deposit ETH, SOL, USDC or USDT and the
          operator delivers native TAO to your ss58. Compare it against the outside
          routes on the Bridge in tab before using it.
        </div>

        <div className="h2">1. Connect wallets</div>
        <div className="row">
          <button className="secondary" onClick={connectMetaMask}>
            {evmAddress ? `MetaMask: ${evmAddress.slice(0, 6)}…${evmAddress.slice(-4)}` : 'Connect MetaMask'}
          </button>
          <select
            value={evmChainId || ''}
            onChange={e => switchEvmNetwork(e.target.value)}
            disabled={!evmAddress}
            title="EVM network"
          >
            {!evmChainId && <option value="">Network…</option>}
            {evmChainId && !evmNet && <option value={evmChainId}>{`Unknown (${evmChainId})`}</option>}
            {EVM_NETWORKS.map(n => (
              <option key={n.chainId} value={n.chainId}>
                {n.label}{n.supported ? '' : ' (unsupported)'}
              </option>
            ))}
          </select>
        </div>
        {evmAddress && fromChain === 'ethereum' && !evmChainOk && (
          <div className="warn">
            MetaMask is on <b>{evmNet?.label || evmChainId}</b>. The deposit watcher only sees Ethereum Mainnet — switch before sending or your funds will be stranded on the wrong chain.{' '}
            <button className="secondary" onClick={() => switchEvmNetwork(REQUIRED_EVM_CHAIN_ID)}>Switch to Ethereum Mainnet</button>
          </div>
        )}
        <div className="row">
          <button className="secondary" onClick={() => connectSubWallet('tao')}>
            {taoSs58 ? `TAO: ${taoSs58.slice(0, 6)}…${taoSs58.slice(-4)}` : 'Connect SubWallet (TAO)'}
          </button>
          <button className="secondary" onClick={() => connectSubWallet('sol')}>
            {solAddress ? `SOL: ${solAddress.slice(0, 4)}…${solAddress.slice(-4)}` : 'Connect SubWallet (SOL)'}
          </button>
        </div>

        <div className="h2">2. Pick swap</div>
        <div className="row">
          <select value={from} onChange={e => { setFrom(e.target.value); setQuote(null) }}>
            {sourceKeys.map(k => (
              <option key={k} value={k}>{TOKEN_LABEL[k] || k.toUpperCase()}</option>
            ))}
          </select>
          <input
            type="number"
            step="any"
            min="0"
            value={amount}
            onChange={e => setAmount(e.target.value)}
            placeholder="amount"
          />
          <button onClick={fetchQuote} disabled={!canQuote || busy}>Quote</button>
        </div>

        {quote && (
          <div style={{ marginTop: 8 }}>
            <div className="kv"><span className="k">Rate</span><span className="v">1 {tokenSymbol(quote.from)} = {quote.rate.toFixed(4)} TAO</span></div>
            <div className="kv"><span className="k">Fee</span><span className="v">{(quote.fee_bps / 100).toFixed(2)}% ({quote.fee_tao.toFixed(4)} TAO)</span></div>
            <div className="kv"><span className="k">You receive</span><span className="v">{quote.tao_out.toFixed(4)} TAO</span></div>
            {quote.rates_stale && <div className="warn">Rates are stale (network issue). Refresh before swapping.</div>}
          </div>
        )}

        <div className="h2">3. Destination ss58 (TAO)</div>
        <div className="row">
          <input
            value={taoSs58}
            onChange={e => setTaoSs58(e.target.value)}
            placeholder="5Xxx... (Bittensor ss58)"
          />
        </div>
        {taoSs58 && !isSs58(taoSs58) && <div className="err">Invalid ss58 address.</div>}

        <hr />

        <button onClick={startSwap} disabled={!canSwap || busy}>
          {busy ? 'Working…' : 'Start swap'}
        </button>

        {err && <div className="err" style={{ marginTop: 8 }}>{err}</div>}
        {notice && !err && <div className="ok" style={{ marginTop: 8 }}>{notice}</div>}
        </>)}
      </div>

      {tab === 'desk' && order && (
        <div className="card" style={{ marginTop: 16 }}>
          <div className="h2" style={{ marginTop: 0 }}>Order <span className="pill">{order.state}</span></div>
          <div className="kv"><span className="k">ID</span><span className="v">{order.id}</span></div>
          <div className="kv"><span className="k">From</span><span className="v">{order.amount_in} {tokenSymbol(order.from)}</span></div>
          <div className="kv"><span className="k">Quoted out</span><span className="v">{order.quoted_tao_out.toFixed(4)} TAO</span></div>
          <div className="h2">Deposit address</div>
          <div className="code">{order.deposit_address}</div>
          {order.state === 'awaiting_deposit' && (
            <div style={{ marginTop: 12 }}>
              <button onClick={sendDeposit} disabled={busy}>
                {fromChain === 'ethereum' ? `Send ${tokenSymbol(from)} via MetaMask` : `I sent ${tokenSymbol(from)} — paste tx`}
              </button>
            </div>
          )}
          {order.source_tx && (
            <>
              <div className="h2">Deposit tx</div>
              <div className="code">{order.source_tx}</div>
            </>
          )}
          {order.delivery_tx && (
            <>
              <div className="h2">TAO delivery tx</div>
              <div className="code">{order.delivery_tx}</div>
            </>
          )}
          <div className="muted" style={{ marginTop: 8 }}>
            Status auto-refreshes every 8s.
          </div>
        </div>
      )}

      <div className="muted" style={{ textAlign: 'center', marginTop: 16 }}>
        Powered by mod • api: <span className="code" style={{ display: 'inline', padding: '1px 6px' }}>{getApiBase()}</span>
      </div>
    </div>
  )
}
