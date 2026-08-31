'use client'

// Credits sidebar — top up USDT/USDC/ETH to the module's deposit address
// (straight from MetaMask, or by pasting a tx hash) and spend the credits
// to run the agent on the module's public API key.
//
// A deposit is the guest pre-funding the OpenRouter/Venice credits their
// own runs burn: a run is billed at what it cost the module's key plus the
// margin (fee_rate). The owner's TREASURY panel is the other half of that
// loop — it says how much of the deposit float has to go to the providers.
//
// Deposits are verified trustlessly by tx hash: the API reads the receipt
// from a public RPC and credits the ON-CHAIN SENDER, once per hash. The
// MetaMask button is only a shortcut to producing that hash — the console
// switches the wallet to the chosen chain, sends the transfer, waits for the
// receipt and submits the hash itself. ETH is priced at Chainlink's ETH/USD
// feed on the chain it landed on.

import { useCallback, useEffect, useMemo, useState } from 'react'
import { API_URL } from '../config'
import { qrSvg } from '../lib/qr'

export type CreditsAuth = { address: string; token: string; isOwner: boolean }

export type CreditsHistoryEntry = {
  time: number; type: string; amount: number; note?: string; tx?: string
  cost?: number; fee?: number
}

export type DepositNetwork = {
  tokens: string[]
  chain_id?: number
  native?: string
  contracts?: Record<string, { address: string; decimals: number }>
  explorer?: string
}

export type CreditsInfo = {
  enabled: boolean
  price_per_step: number
  fee_rate?: number
  deposit: {
    address: string | null
    networks: Record<string, DepositNetwork>
    providers?: string[]
  }
  account?: { address: string; balance: number; history: CreditsHistoryEntry[] }
  accounts?: { address: string; balance: number }[]
}

type ProviderView = {
  balance: number | null; topups: number; error?: string
  metered?: { actual: number; billed: number; ratio: number | null; since: number }
  // where credits are bought, and what the key's own meter says has landed
  // since the last booked top-up (`pending`)
  topup?: {
    url: string | null; meter: string | null; exact: boolean
    mark: number | null; now: number | null; pending: number
  }
}

export type Treasury = {
  fee_rate: number; price_per_step: number; cost_multiplier: number
  deposits: number; grants: number; revenue: number
  provider_cost: number; fees: number; fees_withdrawn: number; fees_available: number
  topups: Record<string, number>; topups_total: number; float: number
  earmarked?: Record<string, number>   // deposits guests tagged for one provider key
  user_credits: number; funding_required: number; accounts: number
  provider_balance: number | null; topup_needed: number | null
  topup_pending: number | null       // bought on a key, not yet in the books
  providers: Record<string, ProviderView>
  ledger: { time: number; type: string; provider?: string; amount: number; ref?: string; note?: string; verified?: boolean }[]
}

type Props = {
  open: boolean
  onClose: () => void
  auth: CreditsAuth | null
  info: CreditsInfo | null
  onRefresh: () => void
  spend: boolean
  onSpendChange: (v: boolean) => void
  onSignIn: () => void
}

const shortAddr = (a: string) => `${a.slice(0, 6)}…${a.slice(-4)}`

// wallet plumbing — the injected EIP-1193 provider, if the browser has one
const eth = () => (typeof window !== 'undefined' ? (window as any).ethereum : undefined)
const CHAINS: Record<number, { chainName: string; rpcUrls: string[]; blockExplorerUrls: string[]; nativeCurrency: { name: string; symbol: string; decimals: number } }> = {
  8453: { chainName: 'Base', rpcUrls: ['https://mainnet.base.org'], blockExplorerUrls: ['https://basescan.org'],
    nativeCurrency: { name: 'Ether', symbol: 'ETH', decimals: 18 } },
  1: { chainName: 'Ethereum', rpcUrls: ['https://eth.llamarpc.com'], blockExplorerUrls: ['https://etherscan.io'],
    nativeCurrency: { name: 'Ether', symbol: 'ETH', decimals: 18 } },
}
// "12.5" with `decimals` → integer units as a bigint, no float rounding
const toUnits = (amount: string, decimals: number): bigint => {
  const [whole, frac = ''] = amount.trim().split('.')
  const digits = (frac + '0'.repeat(decimals)).slice(0, decimals)
  return BigInt(whole || '0') * BigInt(10) ** BigInt(decimals) + BigInt(digits || '0')
}
const hex = (n: bigint) => '0x' + n.toString(16)
const pad32 = (h: string) => h.replace(/^0x/, '').toLowerCase().padStart(64, '0')
// ERC-20 transfer(address,uint256)
const transferData = (to: string, units: bigint) => '0xa9059cbb' + pad32(to) + pad32(hex(units))
const sleep = (ms: number) => new Promise(r => setTimeout(r, ms))
const PENDING_KEY = 'agent_pending_deposit'
const fmtUsd = (n: number) => `$${n.toFixed(2)}`
// sub-cent charges are the normal case for a small run — show them
const fmtFine = (n: number) => (Math.abs(n) < 0.01 && n !== 0 ? `$${n.toFixed(4)}` : `$${n.toFixed(2)}`)
const pct = (n: number) => `${(n * 100).toFixed(n * 100 % 1 ? 1 : 0)}%`
const fmtTime = (t: number) => {
  const d = new Date(t * 1000)
  return `${d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })} ${d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })}`
}

export default function CreditsSidebar({ open, onClose, auth, info, onRefresh, spend, onSpendChange, onSignIn }: Props) {
  const [token, setToken] = useState<string>('USDC')
  const [network, setNetwork] = useState('base')
  const [txHash, setTxHash] = useState('')
  const [verifying, setVerifying] = useState(false)
  const [verifyMsg, setVerifyMsg] = useState<{ ok: boolean; text: string; link?: string } | null>(null)
  const [copied, setCopied] = useState(false)
  // pay from the wallet: amount typed in the token's own unit, which provider
  // key the deposit is meant for, and where the send is up to
  const [amount, setAmount] = useState('')
  const [fundFor, setFundFor] = useState<string>('')
  const [pay, setPay] = useState<{ stage: 'idle' | 'wallet' | 'sent' | 'verifying'; hash?: string }>({ stage: 'idle' })
  const [ethUsd, setEthUsd] = useState<{ usd: number; source: string } | null>(null)
  const [showManual, setShowManual] = useState(false)

  // owner grant form
  const [grantAddr, setGrantAddr] = useState('')
  const [grantAmount, setGrantAmount] = useState('')
  const [grantMsg, setGrantMsg] = useState<string | null>(null)
  const [granting, setGranting] = useState(false)

  // owner treasury: the deposits → provider credits → margin loop
  const [book, setBook] = useState<Treasury | null>(null)
  const [bookErr, setBookErr] = useState<string | null>(null)
  const [topProvider, setTopProvider] = useState('openrouter')
  const [topAmount, setTopAmount] = useState('')
  const [topRef, setTopRef] = useState('')
  // a purchase happens on the provider's own page — neither OpenRouter nor
  // Venice sells credits over an API — so the console opens that page and
  // then watches the key until the money lands, booking what actually arrived
  const [watch, setWatch] = useState<{ provider: string; until: number } | null>(null)
  const [topMsg, setTopMsg] = useState<string | null>(null)
  const [manualLog, setManualLog] = useState(false)
  const [feeInput, setFeeInput] = useState('')
  const [busy, setBusy] = useState<string | null>(null)
  const [bookMsg, setBookMsg] = useState<string | null>(null)

  const loadTreasury = useCallback(async () => {
    if (!auth?.isOwner) return
    setBookErr(null)
    try {
      const res = await fetch(`${API_URL}/credits/treasury?key=${encodeURIComponent(auth.token)}`,
        { signal: AbortSignal.timeout(30000) })
      const data = await res.json()
      if (data.error) setBookErr(data.error)
      else { setBook(data); setFeeInput(String(((data.fee_rate ?? 0) * 100).toFixed(2).replace(/\.?0+$/, ''))) }
    } catch (e: any) {
      setBookErr(e?.message || 'treasury unavailable')
    }
  }, [auth])

  useEffect(() => {
    if (open) { setVerifyMsg(null); onRefresh(); loadTreasury() }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  // owner POSTs that all end the same way: report, then re-read the books
  const post = async (path: string, body: any, label: string) => {
    setBusy(label); setBookMsg(null)
    try {
      const res = await fetch(`${API_URL}${path}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...body, key: auth?.token }),
        signal: AbortSignal.timeout(30000),
      })
      const data = await res.json()
      setBookMsg(data.error || `${label} ✓`)
      if (!data.error) { await loadTreasury(); onRefresh() }
      return data
    } catch (e: any) {
      setBookMsg(e?.message || `${label} failed`)
    } finally {
      setBusy(null)
    }
  }

  // Confirm a purchase by re-reading the provider key: the server compares
  // the key's own meter against the mark it stood at when we last booked one
  // and records the difference, so the books never rest on a typed amount.
  const verifyTopup = useCallback(async (provider: string, quiet = false) => {
    if (!auth?.isOwner) return null
    if (!quiet) { setBusy('check'); setTopMsg(null) }
    try {
      const res = await fetch(`${API_URL}/credits/topup/verify`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ provider, key: auth.token }),
        signal: AbortSignal.timeout(30000),
      })
      const data = await res.json()
      if (data.error) { setTopMsg(data.error); return null }
      if (data.booked > 0) {
        setTopMsg(`${fmtUsd(data.booked)} landed on ${provider} — booked`)
        setWatch(null)
        await loadTreasury(); onRefresh()
      } else if (!quiet) {
        setTopMsg(data.reason || 'nothing new on the key yet')
      }
      return data
    } catch (e: any) {
      if (!quiet) setTopMsg(e?.message || 'check failed')
      return null
    } finally {
      if (!quiet) setBusy(null)
    }
  }, [auth, loadTreasury, onRefresh])

  // while a purchase is in flight, poll the key instead of making the owner
  // press anything — a card payment can take a minute to clear
  useEffect(() => {
    if (!watch) return
    const id = setInterval(() => {
      if (Date.now() > watch.until) {
        setWatch(null)
        setTopMsg('nothing landed yet — press check once the purchase clears')
        return
      }
      verifyTopup(watch.provider, true)
    }, 8000)
    return () => clearInterval(id)
  }, [watch, verifyTopup])

  const depositAddr = info?.deposit?.address || null
  const networks = Object.keys(info?.deposit?.networks || { base: 1, ethereum: 1 })
  const netInfo: DepositNetwork | undefined = info?.deposit?.networks?.[network]
  const tokens = netInfo?.tokens?.length ? netInfo.tokens : ['USDC', 'USDT', 'ETH']
  const providers = info?.deposit?.providers || ['openrouter', 'venice']
  const isNative = token === (netInfo?.native || 'ETH')
  const explorer = netInfo?.explorer

  // an ETH deposit is credited at the chain's Chainlink price — show the
  // same number the server will use before the wallet opens
  useEffect(() => {
    if (!open || !isNative) return
    let live = true
    fetch(`${API_URL}/credits/price?network=${encodeURIComponent(network)}`, { signal: AbortSignal.timeout(15000) })
      .then(r => r.json())
      .then(d => { if (live && typeof d?.usd === 'number') setEthUsd({ usd: d.usd, source: d.source }) })
      .catch(() => {})
    return () => { live = false }
  }, [open, isNative, network])

  const typedAmount = parseFloat(amount)
  const usdEstimate = !isFinite(typedAmount) || typedAmount <= 0 ? null
    : isNative ? (ethUsd ? typedAmount * ethUsd.usd : null) : typedAmount

  // submit a hash for credit; retried because a public RPC can lag the
  // wallet's own node by a few seconds right after confirmation
  const submitHash = useCallback(async (hash: string, net: string, prov: string, tries = 6) => {
    let last = ''
    for (let i = 0; i < tries; i++) {
      try {
        const res = await fetch(`${API_URL}/credits/deposit`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ tx_hash: hash, network: net, provider: prov || null, key: auth?.token }),
          signal: AbortSignal.timeout(30000),
        })
        const data = await res.json()
        if (!data.error) return data
        last = String(data.error)
        if (!/not found|pending/i.test(last)) break
      } catch (e: any) {
        last = e?.message || 'verification failed'
      }
      await sleep(5000)
    }
    throw new Error(last || 'verification failed')
  }, [auth?.token])

  const finishDeposit = useCallback((data: any, hash: string) => {
    const mine = auth?.address && data.address && auth.address.toLowerCase() === data.address.toLowerCase()
    const extra = data.eth ? ` (${data.eth} ETH @ $${Number(data.eth_usd).toLocaleString()})` : ''
    const who = mine ? 'your balance' : `${shortAddr(data.address)} — sign in with that wallet to spend it`
    setVerifyMsg({
      ok: true,
      text: `+${fmtUsd(data.credited)} ${data.token}${extra} credited to ${who}${data.provider ? ` · for ${data.provider}` : ''}`,
      link: data.explorer || (explorer ? explorer + hash : undefined),
    })
    try { localStorage.removeItem(PENDING_KEY) } catch {}
    onRefresh()
  }, [auth?.address, explorer, onRefresh])

  // MetaMask: switch to the chain, send the transfer, wait for the receipt,
  // then hand the hash to the same verifier the paste box uses
  const payWithWallet = async () => {
    const provider = eth()
    if (!provider) { setVerifyMsg({ ok: false, text: 'No wallet found — install MetaMask, or send by hand and paste the hash below' }); return }
    if (!depositAddr || !isFinite(typedAmount) || typedAmount <= 0) { setVerifyMsg({ ok: false, text: 'enter an amount' }); return }
    const chainId = netInfo?.chain_id || (network === 'base' ? 8453 : 1)
    const contract = isNative ? null : netInfo?.contracts?.[token]
    if (!isNative && !contract) { setVerifyMsg({ ok: false, text: `${token} isn't configured on ${network}` }); return }
    setVerifyMsg(null)
    setPay({ stage: 'wallet' })
    try {
      const accounts: string[] = await provider.request({ method: 'eth_requestAccounts' })
      const from = String(accounts?.[0] || '').toLowerCase()
      if (!from) throw new Error('no account selected')
      // the chain the deposit address is watched on — add it if the wallet lacks it
      const want = '0x' + chainId.toString(16)
      const have = String(await provider.request({ method: 'eth_chainId' }))
      if (have.toLowerCase() !== want) {
        try {
          await provider.request({ method: 'wallet_switchEthereumChain', params: [{ chainId: want }] })
        } catch (e: any) {
          if (e?.code !== 4902 || !CHAINS[chainId]) throw e
          await provider.request({ method: 'wallet_addEthereumChain', params: [{ chainId: want, ...CHAINS[chainId] }] })
        }
      }
      const tx = isNative
        ? { from, to: depositAddr, value: hex(toUnits(amount, 18)) }
        : { from, to: contract!.address, data: transferData(depositAddr, toUnits(amount, contract!.decimals)), value: '0x0' }
      const hash: string = await provider.request({ method: 'eth_sendTransaction', params: [tx] })
      setPay({ stage: 'sent', hash })
      try { localStorage.setItem(PENDING_KEY, JSON.stringify({ hash, network, provider: fundFor })) } catch {}
      // wait for it to mine — the wallet's node sees the receipt first
      let mined = false
      for (let i = 0; i < 80 && !mined; i++) {
        await sleep(3000)
        try {
          const r = await provider.request({ method: 'eth_getTransactionReceipt', params: [hash] })
          if (r) {
            if (r.status !== '0x1') throw new Error('transaction failed on-chain')
            mined = true
          }
        } catch (e: any) {
          if (/failed on-chain/.test(e?.message || '')) throw e
        }
      }
      setPay({ stage: 'verifying', hash })
      const data = await submitHash(hash, network, fundFor)
      finishDeposit(data, hash)
      setAmount('')
    } catch (e: any) {
      if (e?.code === 4001) setVerifyMsg({ ok: false, text: 'cancelled in the wallet' })
      else setVerifyMsg({ ok: false, text: e?.message || 'payment failed', link: pay.hash && explorer ? explorer + pay.hash : undefined })
    }
    setPay({ stage: 'idle' })
  }

  // a reload mid-confirmation must not lose the hash — pick it back up
  useEffect(() => {
    if (!open || pay.stage !== 'idle') return
    let pending: { hash: string; network: string; provider: string } | null = null
    try { pending = JSON.parse(localStorage.getItem(PENDING_KEY) || 'null') } catch {}
    if (!pending?.hash) return
    let live = true
    setPay({ stage: 'verifying', hash: pending.hash })
    submitHash(pending.hash, pending.network, pending.provider, 3)
      .then(d => { if (live) finishDeposit(d, pending!.hash) })
      .catch(e => {
        if (!live) return
        if (/already credited/i.test(e?.message || '')) { try { localStorage.removeItem(PENDING_KEY) } catch {} }
        else setVerifyMsg({ ok: false, text: `${shortAddr(pending!.hash)}: ${e?.message}` })
      })
      .finally(() => { if (live) setPay({ stage: 'idle' }) })
    return () => { live = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])
  const balance = info?.account?.balance ?? 0
  const feeRate = info?.fee_rate ?? book?.fee_rate ?? 0.05

  const qr = useMemo(() => {
    if (!depositAddr) return null
    try { return qrSvg(depositAddr, 148, 3, '#0b0b0c', '#ffffff') } catch { return null }
  }, [depositAddr])

  const copyDeposit = () => {
    if (!depositAddr) return
    navigator.clipboard?.writeText(depositAddr).catch(() => {})
    setCopied(true)
    setTimeout(() => setCopied(false), 1200)
  }

  const verifyDeposit = async () => {
    const hash = txHash.trim()
    if (!hash || verifying) return
    setVerifying(true)
    setVerifyMsg(null)
    try {
      const data = await submitHash(hash, network, fundFor, 2)
      finishDeposit(data, hash)
      setTxHash('')
    } catch (e: any) {
      setVerifyMsg({ ok: false, text: e?.message || 'verification failed' })
    }
    setVerifying(false)
  }

  // The owner's ledger move: hand credit to an address, or take it back.
  // Same endpoint either way — the sign is the whole difference — and the
  // server clamps a deduction at zero, so `credited` is what really moved.
  const move = async (signed: number, address?: string) => {
    const addr = (address || grantAddr).trim()
    if (!addr || !isFinite(signed) || signed === 0 || granting) return
    setGranting(true)
    setGrantMsg(null)
    try {
      const res = await fetch(`${API_URL}/credits/grant`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ address: addr, amount: signed, key: auth?.token }),
        signal: AbortSignal.timeout(10000),
      })
      const data = await res.json()
      if (data.error) setGrantMsg(data.error)
      else {
        const moved = typeof data.credited === 'number' ? data.credited : signed
        const short = Math.abs(moved) < Math.abs(signed) ? ' (all it had)' : ''
        setGrantMsg(`${moved < 0 ? 'Took' : 'Gave'} ${fmtUsd(Math.abs(moved))}${short} · ` +
          `${shortAddr(data.address)} → ${fmtUsd(data.balance)}`)
        if (!address) setGrantAmount('')
        onRefresh()
        loadTreasury()
      }
    } catch (e: any) {
      setGrantMsg(e?.message || 'the ledger move failed')
    }
    setGranting(false)
  }

  const grantAmountNum = Math.abs(parseFloat(grantAmount))
  const grantReady = !!grantAddr.trim() && isFinite(grantAmountNum) && grantAmountNum > 0

  // the owner's own desk, in place of the guest top-up form: the owner funds
  // the provider keys directly, so buying credits from themselves would just
  // move their money in a circle. What they need is the other two buttons.
  const ownerDesk = (
    <div className="rounded-lg border border-violet-500/15 bg-violet-500/[0.03] p-3 space-y-2">
      <div className="text-[9px] text-violet-300/70 uppercase tracking-wider">Owner · credit desk</div>
      <div className="text-[10px] text-gray-500 leading-relaxed">
        Give credit to any address, or take it back. Nothing here is a payment —
        you already pay {providers.join(' / ')} for every run this module makes.
      </div>
      <input value={grantAddr} onChange={e => setGrantAddr(e.target.value)} placeholder="0x… address"
        className="w-full bg-white/[0.04] border border-white/[0.08] rounded-md px-2.5 py-1.5 text-[11px] font-mono text-gray-200 outline-none placeholder:text-gray-600 focus:border-violet-500/40 transition" />
      <div className="flex items-center gap-1.5">
        <div className="flex-1 min-w-0 flex items-center bg-white/[0.04] border border-white/[0.08] rounded-md focus-within:border-violet-500/40 transition">
          <span className="text-[10px] font-mono text-gray-500 pl-2">$</span>
          <input value={grantAmount} inputMode="decimal"
            onChange={e => setGrantAmount(e.target.value.replace(/[^0-9.]/g, ''))}
            onKeyDown={e => { if (e.key === 'Enter' && grantReady) move(grantAmountNum) }}
            placeholder="10"
            className="flex-1 min-w-0 bg-transparent px-1.5 py-1.5 text-[12px] font-mono text-gray-200 outline-none placeholder:text-gray-600" />
        </div>
        <button onClick={() => move(grantAmountNum)} disabled={!grantReady || granting}
          className="px-2.5 py-1.5 rounded-md text-[10px] font-medium border border-emerald-500/30 text-emerald-300 hover:bg-emerald-500/10 disabled:opacity-40 transition shrink-0">
          {granting ? '…' : 'Top up'}
        </button>
        <button onClick={() => move(-grantAmountNum)} disabled={!grantReady || granting}
          className="px-2.5 py-1.5 rounded-md text-[10px] font-medium border border-amber-500/30 text-amber-300 hover:bg-amber-500/10 disabled:opacity-40 transition shrink-0">
          Deduct
        </button>
      </div>
      <div className="flex items-center gap-1.5">
        {[5, 10, 25, 100].map(v => (
          <button key={v} onClick={() => setGrantAmount(String(v))}
            className="px-2 py-0.5 rounded-full text-[10px] font-mono border border-white/10 text-gray-500 hover:text-gray-200 hover:border-white/25 transition">
            ${v}
          </button>
        ))}
      </div>
      {grantMsg && <div className="text-[10px] text-gray-400">{grantMsg}</div>}
      {(info?.accounts?.length || 0) > 0 && (
        <div className="pt-1 space-y-0.5 max-h-44 overflow-y-auto">
          {info!.accounts!.map(a => (
            <div key={a.address} className="flex items-center gap-2 px-1.5 py-1 rounded hover:bg-white/[0.03] group">
              <button onClick={() => setGrantAddr(a.address)} title={`${a.address} — click to load into the desk`}
                className="text-[10px] text-gray-400 font-mono hover:text-gray-200 transition">
                {shortAddr(a.address)}
              </button>
              <span className="ml-auto text-[10px] font-mono text-emerald-300/80">{fmtUsd(a.balance)}</span>
              <button onClick={() => move(grantReady ? grantAmountNum : 10, a.address)} disabled={granting}
                title={`Give this address ${fmtUsd(grantReady ? grantAmountNum : 10)}`}
                className="text-[11px] leading-none w-5 h-5 rounded border border-white/10 text-emerald-300/70 opacity-0 group-hover:opacity-100 hover:bg-emerald-500/10 transition disabled:opacity-30">
                +
              </button>
              <button onClick={() => move(-(grantReady ? grantAmountNum : a.balance), a.address)}
                disabled={granting || a.balance <= 0}
                title={grantReady ? `Take ${fmtUsd(grantAmountNum)} back` : 'Zero this account out'}
                className="text-[11px] leading-none w-5 h-5 rounded border border-white/10 text-amber-300/70 opacity-0 group-hover:opacity-100 hover:bg-amber-500/10 transition disabled:opacity-30">
                −
              </button>
            </div>
          ))}
        </div>
      )}
      {depositAddr && (
        <div className="text-[9px] text-gray-600 leading-relaxed pt-0.5">
          Guests pay in at <span className="font-mono text-gray-500">{shortAddr(depositAddr)}</span>{' '}
          <button onClick={copyDeposit} className="text-gray-500 hover:text-gray-300 transition">
            {copied ? 'copied' : 'copy'}
          </button>
        </div>
      )}
    </div>
  )

  if (!open) return null

  return (
    <>
      <div className="fixed inset-0 bg-black/50 z-40" onClick={onClose} />
      <aside className="fixed inset-y-0 right-0 w-[380px] max-w-[92vw] z-50 bg-surface-1 border-l border-white/10 shadow-2xl flex flex-col">
        {/* header */}
        <div className="px-4 py-3 border-b border-white/[0.06] flex items-center gap-2 shrink-0">
          <span className="text-emerald-300">◈</span>
          <span className="text-[11px] text-gray-300 uppercase tracking-wider font-medium">Credits</span>
          <span className="text-[9px] text-gray-600">1 credit = $1 · model cost + {pct(feeRate)}</span>
          <button onClick={onClose}
            className="ml-auto w-6 h-6 flex items-center justify-center rounded text-gray-500 hover:text-gray-200 hover:bg-white/[0.06] transition">
            ✕
          </button>
        </div>

        <div className="flex-1 overflow-y-auto min-h-0 p-3 space-y-3">
          {!auth ? (
            <div className="rounded-lg border border-white/[0.08] bg-white/[0.02] p-4 text-center space-y-3">
              <div className="text-xs text-gray-400">
                Sign in with your wallet to see your balance, top up, and spend credits on the module&apos;s public API key.
              </div>
              <button onClick={onSignIn}
                className="px-4 py-1.5 rounded-full text-xs font-medium border border-emerald-500/30 text-emerald-300 hover:bg-emerald-500/10 transition">
                Sign in
              </button>
            </div>
          ) : (
            <>
              {/* balance — the owner has no balance to hold: they pay the
                  providers directly, so the number that means anything to
                  them is what the guests are holding */}
              {auth.isOwner ? (
                <div className="rounded-lg border border-violet-500/15 bg-violet-500/[0.04] p-3">
                  <div className="text-[9px] text-gray-500 uppercase tracking-wider">
                    Owner · {shortAddr(auth.address)}
                  </div>
                  <div className="text-lg font-semibold text-violet-200 mt-0.5">Your runs are free</div>
                  <div className="text-[10px] text-gray-500 mt-1 leading-relaxed">
                    You pay the providers directly, so the module never bills your address and
                    you never buy credits from yourself. Credits are for everyone else —
                    hand them out below.
                  </div>
                  {book && (
                    <div className="text-[10px] text-gray-500 mt-1.5 font-mono">
                      {book.accounts} account{book.accounts === 1 ? '' : 's'} holding{' '}
                      <span className="text-emerald-300/80">{fmtUsd(book.user_credits)}</span>
                    </div>
                  )}
                </div>
              ) : (
                <div className="rounded-lg border border-emerald-500/15 bg-emerald-500/[0.04] p-3">
                  <div className="text-[9px] text-gray-500 uppercase tracking-wider">Balance · {shortAddr(auth.address)}</div>
                  <div className="text-2xl font-semibold text-emerald-200 font-mono mt-0.5">{fmtUsd(balance)}</div>
                  <div className="text-[10px] text-gray-500 mt-1">
                    Buys {pct(1 / (1 + feeRate))} of its value in model time — a run is billed at what it
                    costs on the module&apos;s provider key plus {pct(feeRate)}.
                  </div>
                </div>
              )}

              {/* spend toggle — how guest runs are powered */}
              {!auth.isOwner && (
                <button onClick={() => onSpendChange(!spend)}
                  className={`w-full flex items-center gap-3 rounded-lg border p-3 text-left transition ${
                    spend ? 'border-emerald-500/25 bg-emerald-500/[0.05]' : 'border-white/[0.08] bg-white/[0.02] hover:border-white/15'
                  }`}>
                  <span className={`w-8 h-[18px] rounded-full relative shrink-0 transition ${spend ? 'bg-emerald-500/60' : 'bg-white/10'}`}>
                    <span className={`absolute top-[2px] w-[14px] h-[14px] rounded-full bg-white transition-all ${spend ? 'left-[16px]' : 'left-[2px]'}`} />
                  </span>
                  <span className="min-w-0">
                    <span className="block text-xs text-gray-200">Spend credits · public key</span>
                    <span className="block text-[10px] text-gray-500 mt-0.5">
                      {spend
                        ? `Runs use the module’s API key — billed at the model’s own price plus ${pct(feeRate)}`
                        : 'Off — runs stick to free models, nothing is charged'}
                    </span>
                  </span>
                </button>
              )}
            </>
          )}

          {/* the owner gets the desk where a guest gets the payment form:
              buying credits from themselves would move their own money in a
              circle, and what they actually need is to fund other people */}
          {auth?.isOwner && ownerDesk}

          {/* top up */}
          {!auth?.isOwner && (
          <div className="rounded-lg border border-white/[0.08] bg-white/[0.02] p-3 space-y-2.5">
            <div className="text-[9px] text-gray-500 uppercase tracking-wider">Top up</div>
            {!info?.enabled || !depositAddr ? (
              <div className="text-[11px] text-gray-500">
                Deposits are disabled — the module has no deposit address configured.
              </div>
            ) : (
              <>
                {/* token + network pills */}
                <div className="flex items-center gap-1.5 flex-wrap">
                  {tokens.map(t => (
                    <button key={t} onClick={() => setToken(t)}
                      className={`px-2.5 py-1 rounded-full text-[10px] font-mono border transition ${
                        token === t ? 'border-emerald-500/40 text-emerald-200 bg-emerald-500/10' : 'border-white/10 text-gray-500 hover:text-gray-300'
                      }`}>
                      {t}
                    </button>
                  ))}
                  <span className="w-px h-4 bg-white/10 mx-0.5" />
                  {networks.map(n => (
                    <button key={n} onClick={() => setNetwork(n)}
                      className={`px-2.5 py-1 rounded-full text-[10px] border capitalize transition ${
                        network === n ? 'border-sky-500/40 text-sky-200 bg-sky-500/10' : 'border-white/10 text-gray-500 hover:text-gray-300'
                      }`}>
                      {n}
                    </button>
                  ))}
                </div>

                {/* which provider key the money is for — one balance either
                    way, the tag tells the owner where to send it */}
                <div className="flex items-center gap-1.5">
                  <span className="text-[9px] text-gray-600 uppercase tracking-wider">for</span>
                  {['', ...providers].map(p => (
                    <button key={p || 'any'} onClick={() => setFundFor(p)}
                      className={`px-2 py-0.5 rounded-full text-[10px] border capitalize transition ${
                        fundFor === p ? 'border-violet-500/40 text-violet-200 bg-violet-500/10' : 'border-white/10 text-gray-500 hover:text-gray-300'
                      }`}>
                      {p || 'any model'}
                    </button>
                  ))}
                </div>

                {/* pay from the wallet */}
                <div className="flex items-center gap-1.5">
                  <div className="flex-1 min-w-0 flex items-center bg-white/[0.04] border border-white/[0.08] rounded-md focus-within:border-emerald-500/40 transition">
                    <input
                      value={amount}
                      onChange={e => setAmount(e.target.value.replace(/[^0-9.]/g, ''))}
                      onKeyDown={e => { if (e.key === 'Enter') payWithWallet() }}
                      placeholder={isNative ? '0.01' : '10'} inputMode="decimal"
                      disabled={pay.stage !== 'idle'}
                      className="flex-1 min-w-0 bg-transparent px-2.5 py-1.5 text-[12px] font-mono text-gray-200 outline-none placeholder:text-gray-600"
                    />
                    <span className="text-[10px] font-mono text-gray-500 pr-2">{token}</span>
                  </div>
                  <button onClick={payWithWallet} disabled={pay.stage !== 'idle' || !amount.trim()}
                    className="px-3 py-1.5 rounded-md text-[10px] font-medium border border-emerald-500/30 text-emerald-200 bg-emerald-500/[0.06] hover:bg-emerald-500/15 disabled:opacity-40 transition shrink-0">
                    {pay.stage === 'wallet' ? 'Confirm in wallet…'
                      : pay.stage === 'sent' ? 'Confirming…'
                      : pay.stage === 'verifying' ? 'Crediting…'
                      : 'Pay with MetaMask'}
                  </button>
                </div>
                <div className="flex items-center gap-2 text-[10px] text-gray-500 min-h-[14px]">
                  {usdEstimate !== null ? (
                    <span>≈ <span className="text-gray-300 font-mono">{fmtUsd(usdEstimate)}</span> of credit
                      {isNative && ethUsd && <span className="text-gray-600"> · ETH ${ethUsd.usd.toLocaleString()} via {ethUsd.source}</span>}
                    </span>
                  ) : isNative ? (
                    <span className="text-gray-600">{ethUsd ? `ETH $${ethUsd.usd.toLocaleString()} via ${ethUsd.source} — credited at the chain’s own price` : 'reading ETH/USD…'}</span>
                  ) : (
                    <span className="text-gray-600">1 {token} = $1 of credit</span>
                  )}
                  {pay.hash && explorer && (
                    <a href={explorer + pay.hash} target="_blank" rel="noopener noreferrer"
                      className="ml-auto text-sky-300/80 hover:text-sky-200 font-mono">
                      {shortAddr(pay.hash)} ↗
                    </a>
                  )}
                </div>
                <div className="text-[9px] text-gray-600 leading-relaxed">
                  The wallet is switched to <span className="capitalize text-gray-400">{network}</span>, sends{' '}
                  {isNative ? 'ETH' : token} to the deposit address, and the hash is verified here the
                  moment it confirms. Credits go to the wallet that pays.
                </div>

                {verifyMsg && (
                  <div className={`text-[10px] rounded-md px-2 py-1.5 border ${
                    verifyMsg.ok ? 'text-emerald-300 border-emerald-500/25 bg-emerald-500/[0.06]' : 'text-red-400 border-red-500/25 bg-red-500/[0.06]'
                  }`}>
                    {verifyMsg.text}
                    {verifyMsg.link && (
                      <a href={verifyMsg.link} target="_blank" rel="noopener noreferrer" className="ml-1.5 underline underline-offset-2 opacity-80 hover:opacity-100">tx ↗</a>
                    )}
                  </div>
                )}

                {/* send by hand: address + QR + paste the hash */}
                <button onClick={() => setShowManual(v => !v)}
                  className="text-[9px] text-gray-600 hover:text-gray-400 transition">
                  {showManual ? '▾ hide' : '▸ send from another wallet instead'}
                </button>
                {showManual && (
                  <div className="space-y-2.5 pt-0.5">
                    <div className="flex items-start gap-2.5">
                      {qr && (
                        <div className="rounded-md overflow-hidden border border-white/10 shrink-0 bg-white"
                          dangerouslySetInnerHTML={{ __html: qr }} />
                      )}
                      <div className="min-w-0 flex-1 space-y-1.5">
                        <div className="text-[10px] text-gray-500">
                          Send <span className="text-gray-300 font-mono">{token}</span> on{' '}
                          <span className="text-gray-300 capitalize">{network}</span> to:
                        </div>
                        <div className="text-[10px] text-gray-200 font-mono break-all leading-relaxed bg-black/30 border border-white/[0.06] rounded-md px-2 py-1.5">
                          {depositAddr}
                        </div>
                        <button onClick={copyDeposit}
                          className="px-2 py-1 rounded text-[10px] border border-white/10 text-gray-400 hover:text-gray-200 hover:border-white/20 transition">
                          {copied ? '✓ copied' : 'Copy address'}
                        </button>
                      </div>
                    </div>
                    <div className="text-[9px] text-amber-300/70">
                      Only USDT / USDC / ETH on {networks.join(' or ')} — other tokens or networks can&apos;t be credited.
                    </div>
                    <div className="text-[10px] text-gray-500">Sent it? Paste the transaction hash to credit your balance:</div>
                    <div className="flex items-center gap-1.5">
                      <input
                        value={txHash}
                        onChange={e => setTxHash(e.target.value)}
                        onKeyDown={e => { if (e.key === 'Enter') verifyDeposit() }}
                        placeholder="0x… tx hash"
                        className="flex-1 min-w-0 bg-white/[0.04] border border-white/[0.08] rounded-md px-2.5 py-1.5 text-[11px] font-mono text-gray-200 outline-none placeholder:text-gray-600 focus:border-emerald-500/40 transition"
                      />
                      <button onClick={verifyDeposit} disabled={verifying || !txHash.trim()}
                        className="px-2.5 py-1.5 rounded-md text-[10px] font-medium border border-emerald-500/30 text-emerald-300 hover:bg-emerald-500/10 disabled:opacity-40 transition shrink-0">
                        {verifying ? 'Verifying…' : 'Verify'}
                      </button>
                    </div>
                    <div className="text-[9px] text-gray-600">
                      Credits go to the wallet that sent the transfer; each hash is credited once.
                    </div>
                  </div>
                )}
              </>
            )}
          </div>
          )}

          {/* history */}
          {auth && (info?.account?.history?.length || 0) > 0 && (
            <div className="rounded-lg border border-white/[0.08] bg-white/[0.02] p-3">
              <div className="text-[9px] text-gray-500 uppercase tracking-wider mb-1.5">Activity</div>
              <div className="space-y-0.5 max-h-52 overflow-y-auto">
                {info!.account!.history.map((h, i) => (
                  <div key={i} className="flex items-center gap-2 px-1.5 py-1 rounded hover:bg-white/[0.03]">
                    <span className={`text-[10px] w-12 shrink-0 uppercase ${
                      h.type === 'spend' ? 'text-gray-500' : h.type === 'grant' ? 'text-violet-300/80' : 'text-emerald-300/80'
                    }`}>
                      {h.type}
                    </span>
                    <span className="text-[10px] text-gray-500 truncate flex-1"
                      title={h.cost !== undefined
                        ? `model ${fmtFine(h.cost)} + margin ${fmtFine(h.fee || 0)} — ${h.note || ''}`
                        : (h.tx || h.note)}>
                      {h.note || (h.tx ? shortAddr(h.tx) : '')}
                    </span>
                    <span className={`text-[10px] font-mono shrink-0 ${h.amount < 0 ? 'text-gray-400' : 'text-emerald-300'}`}>
                      {h.amount < 0 ? '−' : '+'}{fmtFine(Math.abs(h.amount))}
                    </span>
                    <span className="text-[9px] text-gray-700 font-mono shrink-0 w-[76px] text-right">{fmtTime(h.time)}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* owner treasury — deposits in, provider credits out, margin kept */}
          {auth?.isOwner && (
            <div className="rounded-lg border border-amber-500/15 bg-amber-500/[0.03] p-3 space-y-2.5">
              <div className="flex items-center gap-2">
                <span className="text-[9px] text-amber-300/80 uppercase tracking-wider">Owner · treasury</span>
                <button onClick={loadTreasury}
                  className="ml-auto text-[9px] text-gray-500 hover:text-gray-300 transition">refresh</button>
              </div>
              {bookErr && <div className="text-[10px] text-red-400">{bookErr}</div>}
              {book && (
                <>
                  {/* the operative number: what has to go to the providers now */}
                  <div className="rounded-md border border-white/[0.08] bg-black/20 p-2.5">
                    <div className="text-[9px] text-gray-500 uppercase tracking-wider">Top up the providers</div>
                    <div className={`text-xl font-mono mt-0.5 ${
                      (book.topup_needed ?? 0) > 0 ? 'text-amber-200' : 'text-emerald-300'}`}>
                      {book.topup_needed === null ? '—' : fmtUsd(book.topup_needed)}
                    </div>
                    <div className="text-[10px] text-gray-500 mt-1 leading-relaxed">
                      {fmtUsd(book.user_credits)} of unspent guest credits = {fmtUsd(book.funding_required)} of
                      model time owed; the keys hold{' '}
                      {book.provider_balance === null ? '—' : fmtUsd(book.provider_balance)}.
                    </div>
                    {(book.topup_pending ?? 0) > 0 && (
                      <div className="mt-1.5 text-[10px] text-emerald-300/90">
                        {fmtUsd(book.topup_pending || 0)} bought on a key isn&apos;t in the books yet —{' '}
                        {Object.entries(book.providers)
                          .filter(([, p]) => (p.topup?.pending || 0) > 0)
                          .map(([name]) => (
                            <button key={name} onClick={() => verifyTopup(name)}
                              className="underline underline-offset-2 hover:text-emerald-200 transition">
                              book {name}
                            </button>
                          ))}
                      </div>
                    )}
                  </div>

                  {/* per-provider: live balance, what we've sent, estimate drift */}
                  <div className="space-y-1">
                    {Object.entries(book.providers).map(([name, p]) => (
                      <div key={name} className="flex items-center gap-2 px-1.5 py-1 rounded bg-white/[0.02]">
                        <span className="text-[10px] text-gray-300 capitalize w-[70px] shrink-0">{name}</span>
                        {/* an exhausted key is the reason a run fails — say so
                            in the colour, not just the number */}
                        <span className={`text-[10px] font-mono w-[64px] shrink-0 ${
                          typeof p.balance === 'number' && p.balance <= 0 ? 'text-amber-300' : 'text-emerald-300/90'}`}>
                          {p.error ? '—' : p.balance === null || p.balance === undefined ? '—'
                            : p.balance < 0 ? `-${fmtUsd(Math.abs(p.balance))}` : fmtUsd(p.balance)}
                        </span>
                        <span className="text-[9px] text-gray-600 truncate flex-1"
                          title={p.error || (p.metered
                            ? `metered since baseline — actual ${fmtFine(p.metered.actual)} vs billed ${fmtFine(p.metered.billed)}`
                            : '')}>
                          {p.error ? p.error
                            : p.metered?.ratio
                              ? `drift ×${p.metered.ratio}`
                              : `sent ${fmtUsd(p.topups)}`}
                        </span>
                        {(book.earmarked?.[name] || 0) > 0 && (
                          <span className="text-[9px] text-violet-300/80 shrink-0"
                            title={`guests deposited ${fmtUsd(book.earmarked![name])} tagged for this key`}>
                            {fmtUsd(book.earmarked![name])} tagged
                          </span>
                        )}
                      </div>
                    ))}
                  </div>

                  {/* buy provider credits: the purchase is made on the
                      provider's page (neither sells credits over an API), and
                      the amount is booked by reading it back off the key */}
                  {(() => {
                    const prov = book.providers[topProvider]
                    const url = prov?.topup?.url
                    const pending = prov?.topup?.pending || 0
                    const typed = parseFloat(topAmount)
                    const suggested = Math.max(5, Math.ceil(book.topup_needed || 0))
                    const buy = isFinite(typed) && typed > 0 ? typed : suggested
                    return (
                      <div className="space-y-1.5">
                        <div className="text-[9px] text-gray-500 uppercase tracking-wider">Top up a provider key</div>
                        <div className="flex items-center gap-1.5">
                          <select value={topProvider}
                            onChange={e => { setTopProvider(e.target.value); setTopMsg(null); setWatch(null) }}
                            className="bg-white/[0.04] border border-white/[0.08] rounded-md px-1.5 py-1.5 text-[10px] text-gray-200 outline-none">
                            {Object.keys(book.providers).map(n => <option key={n} value={n}>{n}</option>)}
                          </select>
                          <input value={topAmount} onChange={e => setTopAmount(e.target.value)}
                            placeholder={`${suggested}`} inputMode="decimal"
                            className="w-14 bg-white/[0.04] border border-white/[0.08] rounded-md px-2 py-1.5 text-[11px] font-mono text-gray-200 outline-none placeholder:text-gray-600 focus:border-amber-500/40 transition" />
                          <button
                            onClick={() => {
                              if (url) window.open(url, '_blank', 'noopener,noreferrer')
                              setWatch({ provider: topProvider, until: Date.now() + 5 * 60_000 })
                              setTopMsg(`waiting for ${fmtUsd(buy)} to land on the ${topProvider} key…`)
                            }}
                            disabled={!url}
                            className="flex-1 px-2.5 py-1.5 rounded-md text-[10px] font-medium border border-emerald-500/30 text-emerald-200 hover:bg-emerald-500/10 disabled:opacity-40 transition">
                            Buy {fmtUsd(buy)} at {topProvider} ↗
                          </button>
                        </div>

                        {/* the money is sent over there; this end just has to
                            notice it arrived */}
                        <div className="flex items-center gap-1.5">
                          <button onClick={() => verifyTopup(topProvider)}
                            disabled={busy === 'check'}
                            className="px-2 py-1 rounded-md text-[10px] border border-white/10 text-gray-300 hover:border-emerald-500/30 hover:text-emerald-200 disabled:opacity-40 transition">
                            {busy === 'check' ? 'checking…' : watch ? 'check now' : "I've paid — check"}
                          </button>
                          {watch && (
                            <span className="text-[9px] text-emerald-300/70 animate-pulse">
                              watching the {watch.provider} key
                            </span>
                          )}
                          {!watch && pending > 0 && (
                            <span className="text-[9px] text-amber-300/80">
                              {fmtUsd(pending)} on the key, unbooked
                            </span>
                          )}
                          <button onClick={() => setManualLog(v => !v)}
                            className="ml-auto text-[9px] text-gray-600 hover:text-gray-400 transition">
                            {manualLog ? 'hide' : 'log by hand'}
                          </button>
                        </div>
                        {topMsg && (
                          <div className={`text-[10px] ${topMsg.includes('landed') ? 'text-emerald-300' : 'text-gray-500'}`}>
                            {topMsg}
                          </div>
                        )}
                        <div className="text-[9px] text-gray-600 leading-relaxed">
                          {url
                            ? <>Credits are bought on {topProvider}&apos;s own page — there is no API to buy
                                them with. The books record what the key says arrived
                                {prov?.topup?.exact ? '' : ', read off its remaining balance'}.</>
                            : <>No purchase page known for {topProvider} — log the top-up by hand.</>}
                        </div>

                        {/* fallback: a purchase the key's meter can't show us */}
                        {manualLog && (
                          <div className="flex items-center gap-1.5 pt-0.5">
                            <input value={topRef} onChange={e => setTopRef(e.target.value)} placeholder="receipt"
                              className="flex-1 min-w-0 bg-white/[0.04] border border-white/[0.08] rounded-md px-2 py-1.5 text-[11px] text-gray-200 outline-none placeholder:text-gray-600 focus:border-amber-500/40 transition" />
                            <button
                              onClick={async () => {
                                if (!isFinite(typed) || typed <= 0) { setTopMsg('enter the amount you sent'); return }
                                const d = await post('/credits/topup',
                                  { provider: topProvider, amount: typed, ref: topRef }, 'top-up')
                                if (d && !d.error) { setTopAmount(''); setTopRef(''); setTopMsg(null) }
                              }}
                              disabled={busy === 'top-up' || !topAmount.trim()}
                              className="px-2.5 py-1.5 rounded-md text-[10px] font-medium border border-amber-500/30 text-amber-200 hover:bg-amber-500/10 disabled:opacity-40 transition shrink-0">
                              {busy === 'top-up' ? '…' : `Log ${topAmount.trim() ? fmtUsd(typed || 0) : ''}`}
                            </button>
                          </div>
                        )}
                      </div>
                    )
                  })()}

                  {/* the books */}
                  <div className="grid grid-cols-2 gap-x-3 gap-y-1 pt-1">
                    {([
                      ['Deposits in', fmtUsd(book.deposits)],
                      ['Float held', fmtUsd(book.float)],
                      ['Billed to guests', fmtFine(book.revenue)],
                      ['Model cost', fmtFine(book.provider_cost)],
                      ['Sent to providers', fmtUsd(book.topups_total)],
                      [`Margin (${pct(book.fee_rate)})`, fmtFine(book.fees_available)],
                    ] as const).map(([label, value]) => (
                      <div key={label} className="flex items-baseline gap-1.5">
                        <span className="text-[9px] text-gray-600 truncate">{label}</span>
                        <span className="ml-auto text-[10px] font-mono text-gray-300">{value}</span>
                      </div>
                    ))}
                  </div>

                  {/* the margin, and taking it */}
                  <div className="flex items-center gap-1.5">
                    <input value={feeInput} onChange={e => setFeeInput(e.target.value)} placeholder="5"
                      className="w-12 bg-white/[0.04] border border-white/[0.08] rounded-md px-2 py-1.5 text-[11px] font-mono text-gray-200 outline-none focus:border-amber-500/40 transition" />
                    <span className="text-[10px] text-gray-500">% margin</span>
                    <button
                      onClick={() => {
                        const rate = parseFloat(feeInput)
                        if (isFinite(rate)) post('/credits/config', { fee_rate: rate / 100 }, 'margin')
                      }}
                      disabled={busy === 'margin'}
                      className="px-2 py-1.5 rounded-md text-[10px] border border-white/10 text-gray-300 hover:border-amber-500/30 hover:text-amber-200 disabled:opacity-40 transition">
                      {busy === 'margin' ? '…' : 'Set'}
                    </button>
                    <button
                      onClick={() => {
                        if (book.fees_available > 0) post('/credits/withdraw', { amount: book.fees_available }, 'withdraw')
                      }}
                      disabled={busy === 'withdraw' || book.fees_available <= 0}
                      className="ml-auto px-2.5 py-1.5 rounded-md text-[10px] font-medium border border-emerald-500/25 text-emerald-300 hover:bg-emerald-500/10 disabled:opacity-40 transition shrink-0">
                      Take {fmtFine(book.fees_available)}
                    </button>
                  </div>
                  {bookMsg && <div className="text-[10px] text-gray-400">{bookMsg}</div>}
                </>
              )}
            </div>
          )}

        </div>
      </aside>
    </>
  )
}
