'use client'

// Credits sidebar — top up USDT/USDC to the module's deposit address and
// spend the credits to run the agent on the module's public API key.
//
// A deposit is the guest pre-funding the OpenRouter/Venice credits their
// own runs burn: a run is billed at what it cost the module's key plus the
// margin (fee_rate). The owner's TREASURY panel is the other half of that
// loop — it says how much of the deposit float has to go to the providers.
//
// Deposits are verified trustlessly by tx hash: the API reads the receipt
// from a public RPC and credits the ON-CHAIN SENDER, once per hash.

import { useCallback, useEffect, useMemo, useState } from 'react'
import { API_URL } from '../config'
import { qrSvg } from '../lib/qr'

export type CreditsAuth = { address: string; token: string; isOwner: boolean }

export type CreditsHistoryEntry = {
  time: number; type: string; amount: number; note?: string; tx?: string
  cost?: number; fee?: number
}

export type CreditsInfo = {
  enabled: boolean
  price_per_step: number
  fee_rate?: number
  deposit: { address: string | null; networks: Record<string, { tokens: string[] }> }
  account?: { address: string; balance: number; history: CreditsHistoryEntry[] }
  accounts?: { address: string; balance: number }[]
}

type ProviderView = {
  balance: number | null; topups: number; error?: string
  metered?: { actual: number; billed: number; ratio: number | null; since: number }
}

export type Treasury = {
  fee_rate: number; price_per_step: number; cost_multiplier: number
  deposits: number; grants: number; revenue: number
  provider_cost: number; fees: number; fees_withdrawn: number; fees_available: number
  topups: Record<string, number>; topups_total: number; float: number
  user_credits: number; funding_required: number; accounts: number
  provider_balance: number | null; topup_needed: number | null
  providers: Record<string, ProviderView>
  ledger: { time: number; type: string; provider?: string; amount: number; ref?: string; note?: string }[]
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
const fmtUsd = (n: number) => `$${n.toFixed(2)}`
// sub-cent charges are the normal case for a small run — show them
const fmtFine = (n: number) => (Math.abs(n) < 0.01 && n !== 0 ? `$${n.toFixed(4)}` : `$${n.toFixed(2)}`)
const pct = (n: number) => `${(n * 100).toFixed(n * 100 % 1 ? 1 : 0)}%`
const fmtTime = (t: number) => {
  const d = new Date(t * 1000)
  return `${d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })} ${d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })}`
}

export default function CreditsSidebar({ open, onClose, auth, info, onRefresh, spend, onSpendChange, onSignIn }: Props) {
  const [token, setToken] = useState<'USDT' | 'USDC'>('USDT')
  const [network, setNetwork] = useState('base')
  const [txHash, setTxHash] = useState('')
  const [verifying, setVerifying] = useState(false)
  const [verifyMsg, setVerifyMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [copied, setCopied] = useState(false)

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

  const depositAddr = info?.deposit?.address || null
  const networks = Object.keys(info?.deposit?.networks || { base: 1, ethereum: 1 })
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
      const res = await fetch(`${API_URL}/credits/deposit`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tx_hash: hash, network, key: auth?.token }),
        signal: AbortSignal.timeout(30000),
      })
      const data = await res.json()
      if (data.error) {
        setVerifyMsg({ ok: false, text: data.error })
      } else {
        setVerifyMsg({
          ok: true,
          text: `+${fmtUsd(data.credited)} ${data.token} credited to ${shortAddr(data.address)}`,
        })
        setTxHash('')
        onRefresh()
      }
    } catch (e: any) {
      setVerifyMsg({ ok: false, text: e?.message || 'verification failed' })
    }
    setVerifying(false)
  }

  const grant = async () => {
    const amount = parseFloat(grantAmount)
    if (!grantAddr.trim() || !isFinite(amount) || granting) return
    setGranting(true)
    setGrantMsg(null)
    try {
      const res = await fetch(`${API_URL}/credits/grant`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ address: grantAddr.trim(), amount, key: auth?.token }),
        signal: AbortSignal.timeout(10000),
      })
      const data = await res.json()
      if (data.error) setGrantMsg(data.error)
      else {
        setGrantMsg(`${shortAddr(data.address)} → ${fmtUsd(data.balance)}`)
        setGrantAddr(''); setGrantAmount('')
        onRefresh()
      }
    } catch (e: any) {
      setGrantMsg(e?.message || 'grant failed')
    }
    setGranting(false)
  }

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
              {/* balance */}
              <div className="rounded-lg border border-emerald-500/15 bg-emerald-500/[0.04] p-3">
                <div className="text-[9px] text-gray-500 uppercase tracking-wider">Balance · {shortAddr(auth.address)}</div>
                <div className="text-2xl font-semibold text-emerald-200 font-mono mt-0.5">{fmtUsd(balance)}</div>
                <div className="text-[10px] text-gray-500 mt-1">
                  Buys {pct(1 / (1 + feeRate))} of its value in model time — a run is billed at what it
                  costs on the module&apos;s provider key plus {pct(feeRate)}.
                </div>
              </div>

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
              {auth.isOwner && (
                <div className="text-[10px] text-gray-600 px-1">
                  You are the module owner — your runs use your own key and are never charged.
                </div>
              )}
            </>
          )}

          {/* top up */}
          <div className="rounded-lg border border-white/[0.08] bg-white/[0.02] p-3 space-y-2.5">
            <div className="text-[9px] text-gray-500 uppercase tracking-wider">Top up</div>
            {!info?.enabled || !depositAddr ? (
              <div className="text-[11px] text-gray-500">
                Deposits are disabled — the module has no deposit address configured.
              </div>
            ) : (
              <>
                {/* token + network pills */}
                <div className="flex items-center gap-1.5">
                  {(['USDT', 'USDC'] as const).map(t => (
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

                {/* deposit address + QR */}
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
                  Only USDT / USDC on {networks.join(' or ')} — other tokens or networks can&apos;t be credited.
                </div>

                {/* verify tx */}
                <div className="pt-1 space-y-1.5">
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
                  {verifyMsg && (
                    <div className={`text-[10px] rounded-md px-2 py-1.5 border ${
                      verifyMsg.ok ? 'text-emerald-300 border-emerald-500/25 bg-emerald-500/[0.06]' : 'text-red-400 border-red-500/25 bg-red-500/[0.06]'
                    }`}>
                      {verifyMsg.text}
                    </div>
                  )}
                  <div className="text-[9px] text-gray-600">
                    Credits go to the wallet that sent the transfer; each hash is credited once.
                  </div>
                </div>
              </>
            )}
          </div>

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
                  </div>

                  {/* per-provider: live balance, what we've sent, estimate drift */}
                  <div className="space-y-1">
                    {Object.entries(book.providers).map(([name, p]) => (
                      <div key={name} className="flex items-center gap-2 px-1.5 py-1 rounded bg-white/[0.02]">
                        <span className="text-[10px] text-gray-300 capitalize w-[70px] shrink-0">{name}</span>
                        <span className="text-[10px] font-mono text-emerald-300/90 w-[64px] shrink-0">
                          {p.error ? '—' : p.balance === null || p.balance === undefined ? '—' : fmtUsd(p.balance)}
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
                      </div>
                    ))}
                  </div>

                  {/* record a purchase of provider credits out of the float */}
                  <div className="space-y-1.5">
                    <div className="text-[9px] text-gray-500 uppercase tracking-wider">Record a top-up</div>
                    <div className="flex items-center gap-1.5">
                      <select value={topProvider} onChange={e => setTopProvider(e.target.value)}
                        className="bg-white/[0.04] border border-white/[0.08] rounded-md px-1.5 py-1.5 text-[10px] text-gray-200 outline-none">
                        {Object.keys(book.providers).map(n => <option key={n} value={n}>{n}</option>)}
                      </select>
                      <input value={topAmount} onChange={e => setTopAmount(e.target.value)} placeholder="$"
                        className="w-14 bg-white/[0.04] border border-white/[0.08] rounded-md px-2 py-1.5 text-[11px] font-mono text-gray-200 outline-none placeholder:text-gray-600 focus:border-amber-500/40 transition" />
                      <input value={topRef} onChange={e => setTopRef(e.target.value)} placeholder="receipt"
                        className="flex-1 min-w-0 bg-white/[0.04] border border-white/[0.08] rounded-md px-2 py-1.5 text-[11px] text-gray-200 outline-none placeholder:text-gray-600 focus:border-amber-500/40 transition" />
                      <button
                        onClick={async () => {
                          const amount = parseFloat(topAmount)
                          if (!isFinite(amount) || amount <= 0) return
                          const d = await post('/credits/topup',
                            { provider: topProvider, amount, ref: topRef }, 'top-up')
                          if (d && !d.error) { setTopAmount(''); setTopRef('') }
                        }}
                        disabled={busy === 'top-up' || !topAmount.trim()}
                        className="px-2.5 py-1.5 rounded-md text-[10px] font-medium border border-amber-500/30 text-amber-200 hover:bg-amber-500/10 disabled:opacity-40 transition shrink-0">
                        {busy === 'top-up' ? '…' : 'Log'}
                      </button>
                    </div>
                    <div className="text-[9px] text-gray-600">
                      Buy the credits at the provider, then log what you sent — the float is guests&apos; money.
                    </div>
                  </div>

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

          {/* owner tools */}
          {auth?.isOwner && (
            <div className="rounded-lg border border-violet-500/15 bg-violet-500/[0.03] p-3 space-y-2">
              <div className="text-[9px] text-violet-300/70 uppercase tracking-wider">Owner · grant credits</div>
              <div className="flex items-center gap-1.5">
                <input value={grantAddr} onChange={e => setGrantAddr(e.target.value)} placeholder="0x… address"
                  className="flex-1 min-w-0 bg-white/[0.04] border border-white/[0.08] rounded-md px-2.5 py-1.5 text-[11px] font-mono text-gray-200 outline-none placeholder:text-gray-600 focus:border-violet-500/40 transition" />
                <input value={grantAmount} onChange={e => setGrantAmount(e.target.value)} placeholder="± $"
                  className="w-16 bg-white/[0.04] border border-white/[0.08] rounded-md px-2 py-1.5 text-[11px] font-mono text-gray-200 outline-none placeholder:text-gray-600 focus:border-violet-500/40 transition" />
                <button onClick={grant} disabled={granting}
                  className="px-2.5 py-1.5 rounded-md text-[10px] font-medium border border-violet-500/30 text-violet-300 hover:bg-violet-500/10 disabled:opacity-40 transition shrink-0">
                  {granting ? '…' : 'Grant'}
                </button>
              </div>
              {grantMsg && <div className="text-[10px] text-gray-400">{grantMsg}</div>}
              {(info?.accounts?.length || 0) > 0 && (
                <div className="pt-1 space-y-0.5 max-h-40 overflow-y-auto">
                  {info!.accounts!.map(a => (
                    <div key={a.address} className="flex items-center gap-2 px-1.5 py-1 rounded hover:bg-white/[0.03]">
                      <button onClick={() => setGrantAddr(a.address)} title={a.address}
                        className="text-[10px] text-gray-400 font-mono hover:text-gray-200 transition">
                        {shortAddr(a.address)}
                      </button>
                      <span className="ml-auto text-[10px] font-mono text-emerald-300/80">{fmtUsd(a.balance)}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      </aside>
    </>
  )
}
