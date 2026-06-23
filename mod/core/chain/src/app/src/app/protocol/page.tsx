"use client";

import { useState, useEffect, useCallback, useMemo } from 'react'
import dynamic from 'next/dynamic'
import Link from 'next/link'
import { toast } from 'react-toastify'
import {
  WalletIcon,
  ArrowPathIcon,
  LockClosedIcon,
  LockOpenIcon,
  BanknotesIcon,
  PaperAirplaneIcon,
  CircleStackIcon,
  CubeTransparentIcon,
  ArrowUpRightIcon,
  PlusCircleIcon,
  ShieldCheckIcon,
} from '@heroicons/react/24/outline'

// ── Constants ────────────────────────────────────────────────────────────────

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8800'

const CHAIN_NAMES: Record<string, string> = {
  testnet: 'Base Sepolia',
  mainnet: 'Base',
  ganache: 'Ganache',
}

const EXPLORER_URLS: Record<string, string> = {
  testnet: 'https://sepolia.basescan.org',
  mainnet: 'https://basescan.org',
  ganache: '',
}

const TABS = [
  { id: 'wallet', label: 'Wallet', icon: WalletIcon },
  { id: 'stake', label: 'Stake', icon: LockClosedIcon },
  { id: 'market', label: 'Market', icon: BanknotesIcon },
  { id: 'registry', label: 'Registry', icon: CircleStackIcon },
] as const

type TabId = typeof TABS[number]['id']

const BAL_TOKENS = ['ETH', 'NativeToken', 'USDC', 'USDT', 'MARKET']

// ── Types ──────────────────────────────────────────────────────────────────

interface StakePosition {
  stake_id: number
  amount: number
  start_block: number
  lock_blocks: number
  bloctime_balance: number
  blocks_remaining: number
}

interface RegMod {
  id: number
  name: string
  data: string
}

// ── API helper ───────────────────────────────────────────────────────────────

async function api(path: string, params: Record<string, any> = {}, method: 'GET' | 'POST' = 'GET') {
  let url = `${API_URL}/${path}`
  let opts: RequestInit = { method }
  if (method === 'GET') {
    const qs = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== '')
        .map(([k, v]) => [k, String(v)])
    ).toString()
    if (qs) url += `?${qs}`
  } else {
    opts.headers = { 'Content-Type': 'application/json' }
    opts.body = JSON.stringify(params)
  }
  const res = await fetch(url, opts)
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || 'Request failed')
  }
  return res.json()
}

const fmtAddr = (s: string, chars = 5) =>
  s && s.length > 14 ? `${s.slice(0, chars + 2)}...${s.slice(-chars)}` : (s || '--')

const fmtNum = (n: number | null | undefined, dp = 4) =>
  n === null || n === undefined || isNaN(Number(n)) ? '--' : Number(n).toLocaleString(undefined, { maximumFractionDigits: dp })

// ── Reusable bits ────────────────────────────────────────────────────────────

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="block text-[9px] font-bold uppercase tracking-wider text-white/30 mb-1.5">{label}</span>
      {children}
    </label>
  )
}

const inputCls =
  'w-full px-3 py-2 text-xs rounded-lg border border-white/10 bg-white/[0.03] text-white/80 placeholder:text-white/20 focus:outline-none focus:border-cyan-500/40 font-mono'

function ActionBtn({ onClick, busy, children, icon: Icon }: {
  onClick: () => void; busy: boolean; children: React.ReactNode; icon: any
}) {
  return (
    <button
      onClick={onClick}
      disabled={busy}
      className="w-full px-4 py-2.5 rounded-lg border border-cyan-500/30 bg-cyan-500/10 text-cyan-300 text-[10px] font-bold uppercase tracking-wider hover:bg-cyan-500/20 disabled:opacity-30 transition-all flex items-center justify-center gap-2"
    >
      {busy ? <ArrowPathIcon className="w-3.5 h-3.5 animate-spin" /> : <Icon className="w-3.5 h-3.5" />}
      {children}
    </button>
  )
}

const DOT_COLORS: Record<string, string> = {
  cyan: 'bg-cyan-400',
  emerald: 'bg-emerald-400',
  sky: 'bg-sky-400',
  violet: 'bg-violet-400',
  orange: 'bg-orange-400',
}

function Card({ title, children, accent = 'cyan' }: { title?: string; children: React.ReactNode; accent?: string }) {
  return (
    <div className={`border border-white/[0.08] rounded-xl bg-white/[0.02] overflow-hidden`}>
      {title && (
        <div className="px-4 py-3 border-b border-white/[0.05] flex items-center gap-2">
          <div className={`w-1.5 h-1.5 rounded-full ${DOT_COLORS[accent] || 'bg-cyan-400'}`} />
          <span className="text-[10px] font-bold uppercase tracking-wider text-white/40">{title}</span>
        </div>
      )}
      <div className="p-4 space-y-3">{children}</div>
    </div>
  )
}

// ── Main ───────────────────────────────────────────────────────────────────

function ProtocolInner() {
  const [tab, setTab] = useState<TabId>('wallet')
  const [network, setNetwork] = useState('testnet')
  const [keyName, setKeyName] = useState('')           // optional identity override
  const [address, setAddress] = useState<string>('')
  const [balances, setBalances] = useState<Record<string, number | null>>({})
  const [stakes, setStakes] = useState<StakePosition[]>([])
  const [regMods, setRegMods] = useState<RegMod[]>([])
  const [block, setBlock] = useState<number | null>(null)
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)

  // form state
  const [stakeAmt, setStakeAmt] = useState('')
  const [lockBlocks, setLockBlocks] = useState('100')
  const [creditAmt, setCreditAmt] = useState('')
  const [payToken, setPayToken] = useState('usdt')
  const [xferTo, setXferTo] = useState('')
  const [xferAmt, setXferAmt] = useState('')
  const [xferTok, setXferTok] = useState('MARKET')
  const [regName, setRegName] = useState('')
  const [regData, setRegData] = useState('')

  const explorer = EXPLORER_URLS[network]

  // ── Fetch ──
  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const w = await api('wallet', { network, key: keyName || undefined })
      setAddress(w.address || '')
      setBalances(w.balances || {})
      const addr = w.address
      const [s, r, b] = await Promise.all([
        api('stakes', { address: addr, network }).catch(() => ({ stakes: [] })),
        api('registry/mods', { address: addr, network }).catch(() => ({ mods: [] })),
        api('block').catch(() => ({ result: null })),
      ])
      setStakes(s.stakes || [])
      setRegMods(r.mods || [])
      setBlock(b.result ?? null)
    } catch (e: any) {
      toast.error(e?.message || 'Failed to load wallet')
    }
    setLoading(false)
  }, [network, keyName])

  useEffect(() => { refresh() }, [refresh])
  useEffect(() => {
    const iv = setInterval(() => api('block').then(b => setBlock(b.result ?? null)).catch(() => {}), 6000)
    return () => clearInterval(iv)
  }, [])

  // ── Actions ──
  const run = async (id: string, fn: () => Promise<any>, okMsg: string) => {
    setBusy(id)
    try {
      await fn()
      toast.success(okMsg)
      await refresh()
    } catch (e: any) {
      toast.error(e?.message || 'Action failed')
    }
    setBusy(null)
  }

  const doStake = () => run('stake',
    () => api('stake', { amount: Number(stakeAmt), lock_blocks: Number(lockBlocks), network, key: keyName || undefined }, 'POST'),
    `Staked ${stakeAmt} for ${lockBlocks} blocks`)

  const doUnstake = (id: number) => run(`unstake-${id}`,
    () => api('unstake', { stake_id: id, network, key: keyName || undefined }, 'POST'),
    `Unstaked position #${id}`)

  const doCredit = () => run('credit',
    () => api('credit', { stable_amount: Number(creditAmt), payment_token: payToken, network, key: keyName || undefined }, 'POST'),
    `Bought ${creditAmt} MARKET with ${payToken.toUpperCase()}`)

  const doTransfer = () => run('transfer',
    () => api('transfer', { to: xferTo, amount: Number(xferAmt), token: xferTok, network, key: keyName || undefined }, 'POST'),
    `Sent ${xferAmt} ${xferTok}`)

  const doRegister = () => run('register',
    () => api('registry/register', { name: regName, data: regData || undefined, network, key: keyName || undefined }, 'POST'),
    `Registered "${regName}"`)

  const marketBal = balances['MARKET']

  // ── Render ──
  return (
    <div className="min-h-screen bg-[#0a0a0f] text-[#e5e5e5] font-mono">
      <div className="p-4 md:p-6 max-w-5xl mx-auto space-y-4">

        {/* Header */}
        <div className="flex items-center justify-between border border-white/10 rounded-xl p-4 bg-white/[0.02]">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 flex items-center justify-center rounded-lg bg-gradient-to-br from-cyan-500/20 to-violet-500/20 border border-white/10">
              <WalletIcon className="w-5 h-5 text-white/80" />
            </div>
            <div>
              <h1 className="text-xl font-bold tracking-wider uppercase">Protocol</h1>
              <Link href="/" className="text-[10px] text-cyan-500/50 hover:text-cyan-400 uppercase tracking-widest flex items-center gap-1">
                <CubeTransparentIcon className="w-3 h-3" /> Back to Chain Hub
              </Link>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Link href="/admin"
              className="hidden sm:flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-teal-500/30 bg-teal-500/10 text-teal-300 text-[10px] font-bold uppercase tracking-wider hover:bg-teal-500/20 transition-all">
              <ShieldCheckIcon className="w-3.5 h-3.5" /> Owner
            </Link>
            {block !== null && (
              <div className="hidden sm:flex items-center gap-2 px-3 py-1.5 rounded-lg bg-white/[0.03] border border-white/[0.06]">
                <div className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
                <span className="text-[10px] text-white/30 uppercase">Blk</span>
                <span className="text-xs text-emerald-400 tabular-nums font-bold">{block.toLocaleString()}</span>
              </div>
            )}
            <select value={network} onChange={e => setNetwork(e.target.value)}
              className="text-xs px-3 py-2 rounded-lg border border-white/10 bg-white/[0.04] text-white/70 focus:outline-none focus:border-white/20 font-mono cursor-pointer">
              <option value="testnet">Base Sepolia</option>
              <option value="ganache">Ganache</option>
              <option value="mainnet">Base Mainnet</option>
            </select>
            <button onClick={refresh} disabled={loading}
              className="p-2 rounded-lg border border-white/10 bg-white/[0.04] text-white/40 hover:text-white/70 hover:bg-white/[0.08] disabled:opacity-30 transition-colors">
              <ArrowPathIcon className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
            </button>
          </div>
        </div>

        {/* Identity bar */}
        <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-3 border border-white/[0.06] rounded-xl p-3 bg-white/[0.015]">
          <div className="flex-1 flex items-center gap-2">
            <span className="text-[9px] uppercase tracking-wider text-white/25">Account</span>
            <span className="text-xs text-white/70 tabular-nums">{fmtAddr(address, 8)}</span>
            {explorer && address && (
              <a href={`${explorer}/address/${address}`} target="_blank" rel="noopener noreferrer"
                className="text-cyan-500/40 hover:text-cyan-400"><ArrowUpRightIcon className="w-3 h-3" /></a>
            )}
          </div>
          <div className="flex items-center gap-2">
            <span className="text-[9px] uppercase tracking-wider text-white/25">Key</span>
            <input value={keyName} onChange={e => setKeyName(e.target.value)} placeholder="default"
              className="px-2 py-1 text-xs rounded-md border border-white/10 bg-white/[0.03] text-white/70 placeholder:text-white/20 focus:outline-none focus:border-cyan-500/40 w-32" />
          </div>
        </div>

        {/* Tabs */}
        <div className="flex gap-1 border border-white/[0.06] rounded-xl p-1 bg-white/[0.015]">
          {TABS.map(t => {
            const Icon = t.icon
            const active = tab === t.id
            return (
              <button key={t.id} onClick={() => setTab(t.id)}
                className={`flex-1 flex items-center justify-center gap-1.5 py-2 rounded-lg text-[10px] font-bold uppercase tracking-wider transition-all ${
                  active ? 'bg-cyan-500/15 text-cyan-300 border border-cyan-500/30' : 'text-white/35 hover:text-white/60 border border-transparent'
                }`}>
                <Icon className="w-3.5 h-3.5" /> {t.label}
              </button>
            )
          })}
        </div>

        {/* ── Wallet ── */}
        {tab === 'wallet' && (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <Card title="Balances" accent="emerald">
              <div className="grid grid-cols-2 gap-2">
                {BAL_TOKENS.map(t => (
                  <div key={t} className="px-3 py-2.5 rounded-lg border border-white/[0.06] bg-white/[0.02]">
                    <p className="text-[9px] uppercase tracking-wider text-white/25">{t}</p>
                    <p className="text-sm font-bold text-white/70 tabular-nums">{fmtNum(balances[t])}</p>
                  </div>
                ))}
              </div>
            </Card>
            <Card title="Transfer" accent="sky">
              <Field label="Token">
                <select value={xferTok} onChange={e => setXferTok(e.target.value)} className={inputCls}>
                  {BAL_TOKENS.map(t => <option key={t} value={t}>{t}</option>)}
                </select>
              </Field>
              <Field label="Recipient address">
                <input value={xferTo} onChange={e => setXferTo(e.target.value)} placeholder="0x..." className={inputCls} />
              </Field>
              <Field label="Amount">
                <input value={xferAmt} onChange={e => setXferAmt(e.target.value)} placeholder="0.0" type="number" className={inputCls} />
              </Field>
              <ActionBtn onClick={doTransfer} busy={busy === 'transfer'} icon={PaperAirplaneIcon}>Send</ActionBtn>
            </Card>
          </div>
        )}

        {/* ── Stake ── */}
        {tab === 'stake' && (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <Card title="Stake NativeToken" accent="sky">
              <p className="text-[10px] text-white/30 leading-relaxed">
                Lock NativeToken to earn BlocTime. Longer locks accrue more BlocTime.
              </p>
              <Field label="Amount (NativeToken)">
                <input value={stakeAmt} onChange={e => setStakeAmt(e.target.value)} placeholder="0.0" type="number" className={inputCls} />
              </Field>
              <Field label="Lock duration (blocks)">
                <input value={lockBlocks} onChange={e => setLockBlocks(e.target.value)} placeholder="100" type="number" className={inputCls} />
              </Field>
              <ActionBtn onClick={doStake} busy={busy === 'stake'} icon={LockClosedIcon}>Stake</ActionBtn>
            </Card>
            <Card title={`Positions (${stakes.length})`} accent="violet">
              {stakes.length === 0 ? (
                <p className="text-[10px] text-white/25 py-4 text-center uppercase tracking-wider">No active stakes</p>
              ) : stakes.map(s => (
                <div key={s.stake_id} className="px-3 py-2.5 rounded-lg border border-white/[0.06] bg-white/[0.02] space-y-1.5">
                  <div className="flex items-center justify-between">
                    <span className="text-[10px] font-bold text-white/50 uppercase">Stake #{s.stake_id}</span>
                    <button onClick={() => doUnstake(s.stake_id)} disabled={busy === `unstake-${s.stake_id}` || s.blocks_remaining > 0}
                      className="flex items-center gap-1 px-2 py-1 rounded-md border border-rose-500/30 bg-rose-500/10 text-rose-300 text-[9px] font-bold uppercase tracking-wider hover:bg-rose-500/20 disabled:opacity-30 transition-all">
                      {busy === `unstake-${s.stake_id}` ? <ArrowPathIcon className="w-3 h-3 animate-spin" /> : <LockOpenIcon className="w-3 h-3" />}
                      Unstake
                    </button>
                  </div>
                  <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-[10px] text-white/30">
                    <span>Amount: <span className="text-white/60 tabular-nums">{fmtNum(s.amount)}</span></span>
                    <span>BlocTime: <span className="text-white/60 tabular-nums">{fmtNum(s.bloctime_balance)}</span></span>
                    <span>Lock: <span className="text-white/60 tabular-nums">{s.lock_blocks}</span></span>
                    <span>Remaining: <span className={`tabular-nums ${s.blocks_remaining > 0 ? 'text-amber-400' : 'text-emerald-400'}`}>{s.blocks_remaining}</span></span>
                  </div>
                </div>
              ))}
            </Card>
          </div>
        )}

        {/* ── Market ── */}
        {tab === 'market' && (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <Card title="Buy Credits (MARKET)" accent="orange">
              <p className="text-[10px] text-white/30 leading-relaxed">
                Credit your account with MARKET stable tokens using a whitelisted payment token.
              </p>
              <Field label="Pay with">
                <select value={payToken} onChange={e => setPayToken(e.target.value)} className={inputCls}>
                  <option value="usdt">USDT</option>
                  <option value="usdc">USDC</option>
                </select>
              </Field>
              <Field label="Stable amount">
                <input value={creditAmt} onChange={e => setCreditAmt(e.target.value)} placeholder="0.0" type="number" className={inputCls} />
              </Field>
              <ActionBtn onClick={doCredit} busy={busy === 'credit'} icon={BanknotesIcon}>Buy Credits</ActionBtn>
            </Card>
            <Card title="MARKET Balance" accent="emerald">
              <div className="flex flex-col items-center justify-center py-6">
                <p className="text-3xl font-bold text-emerald-400 tabular-nums">{fmtNum(marketBal, 2)}</p>
                <p className="text-[10px] uppercase tracking-widest text-white/25 mt-1">MARKET credits</p>
              </div>
            </Card>
          </div>
        )}

        {/* ── Registry ── */}
        {tab === 'registry' && (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <Card title="Register a Mod" accent="cyan">
              <Field label="Mod name">
                <input value={regName} onChange={e => setRegName(e.target.value)} placeholder="my-mod" className={inputCls} />
              </Field>
              <Field label="Data / CID (optional)">
                <input value={regData} onChange={e => setRegData(e.target.value)} placeholder="Qm... (auto if blank)" className={inputCls} />
              </Field>
              <ActionBtn onClick={doRegister} busy={busy === 'register'} icon={PlusCircleIcon}>Register</ActionBtn>
            </Card>
            <Card title={`My Mods (${regMods.length})`} accent="cyan">
              {regMods.length === 0 ? (
                <p className="text-[10px] text-white/25 py-4 text-center uppercase tracking-wider">No registered mods</p>
              ) : (
                <div className="divide-y divide-white/[0.04]">
                  {regMods.map(mod => (
                    <div key={mod.id} className="flex items-center justify-between py-2">
                      <div className="flex items-center gap-2">
                        <span className="text-[9px] text-white/20 tabular-nums">#{mod.id}</span>
                        <span className="text-xs font-bold text-white/55">{mod.name}</span>
                      </div>
                      <span className="text-[10px] text-white/25 tabular-nums">{fmtAddr(mod.data, 4)}</span>
                    </div>
                  ))}
                </div>
              )}
            </Card>
          </div>
        )}

        <div className="text-center text-[10px] text-white/10 uppercase tracking-widest py-6">
          {CHAIN_NAMES[network]} — Protocol App
        </div>
      </div>
    </div>
  )
}

export default dynamic(() => Promise.resolve(ProtocolInner), { ssr: false })
