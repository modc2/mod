"use client";

import { useState, useEffect, useCallback, useMemo } from 'react'
import dynamic from 'next/dynamic'
import { toast } from 'react-toastify'
import {
  WalletIcon,
  ArrowPathIcon,
  LockClosedIcon,
  LockOpenIcon,
  BanknotesIcon,
  PaperAirplaneIcon,
  CircleStackIcon,
  ArrowUpRightIcon,
  PlusCircleIcon,
  ArrowTrendingUpIcon,
  SparklesIcon,
} from '@heroicons/react/24/outline'
import { Shell, NetworkSelect, RefreshBtn, BlockChip } from '../components/Shell'

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
  { id: 'defi', label: 'DeFi', icon: ArrowTrendingUpIcon },
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

interface YieldStrategy {
  id: number
  asset: string
  asset_symbol: string
  asset_decimals: number
  name: string
  enabled: boolean
  tvl: number
  pending_profit: number
}

interface YieldPosition {
  strategy_id: number
  shares: number
  pending_reward: number
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
  s && s.length > 14 ? `${s.slice(0, chars + 2)}…${s.slice(-chars)}` : (s || '--')

const fmtNum = (n: number | null | undefined, dp = 4) =>
  n === null || n === undefined || isNaN(Number(n)) ? '--' : Number(n).toLocaleString(undefined, { maximumFractionDigits: dp })

// ── Reusable bits ────────────────────────────────────────────────────────────

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="label block mb-1.5">{label}</span>
      {children}
    </label>
  )
}

function ActionBtn({ onClick, busy, children, icon: Icon }: {
  onClick: () => void; busy: boolean; children: React.ReactNode; icon: any
}) {
  return (
    <button
      onClick={onClick}
      disabled={busy}
      className="btn btn-primary w-full !py-2.5"
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

const DOT_GLOWS: Record<string, string> = {
  cyan: 'rgba(34, 211, 238, 0.55)',
  emerald: 'rgba(52, 211, 153, 0.55)',
  sky: 'rgba(56, 189, 248, 0.55)',
  violet: 'rgba(167, 139, 250, 0.55)',
  orange: 'rgba(251, 146, 60, 0.55)',
}

function Card({ title, children, accent = 'cyan' }: { title?: string; children: React.ReactNode; accent?: string }) {
  return (
    <div className="glass overflow-hidden">
      {title && (
        <div className="px-5 py-4 border-b hairline flex items-center gap-2">
          <div
            className={`w-1.5 h-1.5 rounded-full ${DOT_COLORS[accent] || 'bg-cyan-400'}`}
            style={{ boxShadow: `0 0 8px 1px ${DOT_GLOWS[accent] || DOT_GLOWS.cyan}` }}
          />
          <span className="text-[14px] font-semibold text-white/85 tracking-tight">{title}</span>
        </div>
      )}
      <div className="p-5 space-y-4">{children}</div>
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

  // defi state
  const [strategies, setStrategies] = useState<YieldStrategy[]>([])
  const [positions, setPositions] = useState<Record<number, YieldPosition>>({})
  const [yId, setYId] = useState('0')
  const [yAmt, setYAmt] = useState('')

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

      // DeFi strategies + per-strategy position (best effort)
      const st = await api('yield/strategies', { network }).catch(() => ({ strategies: [] }))
      const strat: YieldStrategy[] = st.strategies || []
      setStrategies(strat)
      if (strat.length && !strat.some(x => String(x.id) === yId)) setYId(String(strat[0].id))
      const pos: Record<number, YieldPosition> = {}
      await Promise.all(strat.map(s =>
        api('yield/position', { strategy_id: s.id, address: addr, network })
          .then((p: YieldPosition) => { pos[s.id] = p }).catch(() => {})
      ))
      setPositions(pos)
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

  const selStrat = strategies.find(s => String(s.id) === yId)
  const doYieldDeposit = () => run('y-deposit',
    () => api('yield/deposit', { strategy_id: Number(yId), amount: Number(yAmt), network, key: keyName || undefined }, 'POST'),
    `Deposited ${yAmt} into ${selStrat?.name || 'strategy'}`)
  const doYieldWithdraw = () => run('y-withdraw',
    () => api('yield/withdraw', { strategy_id: Number(yId), amount: Number(yAmt), network, key: keyName || undefined }, 'POST'),
    `Withdrew ${yAmt} from ${selStrat?.name || 'strategy'}`)
  const doYieldHarvest = (id: number) => run(`y-harvest-${id}`,
    () => api('yield/harvest', { strategy_id: id, network, key: keyName || undefined }, 'POST'),
    `Harvested strategy #${id}`)
  const doYieldClaim = (id: number) => run(`y-claim-${id}`,
    () => api('yield/claim', { strategy_id: id, network, key: keyName || undefined }, 'POST'),
    `Claimed rewards from strategy #${id}`)

  const marketBal = balances['MARKET']

  // ── Render ──
  return (
    <Shell
      active="protocol"
      right={
        <>
          <BlockChip block={block} />
          <NetworkSelect value={network} onChange={setNetwork} />
          <RefreshBtn onClick={refresh} loading={loading} />
        </>
      }
      footer={`${CHAIN_NAMES[network]} — protocol app`}
    >
      {/* ═══ Hero ═══ */}
      <div className="fade-up" style={{ '--i': 0 } as any}>
        <h1 className="text-[28px] font-semibold tracking-[-0.03em] text-white">Protocol</h1>
        <p className="mt-1 text-[13px] text-white/40">
          Your wallet, staking, market credits, yield and registry — on {CHAIN_NAMES[network]}.
        </p>
      </div>

      {/* ═══ Identity bar ═══ */}
      <div className="glass px-5 py-3.5 flex flex-col sm:flex-row items-stretch sm:items-center gap-3 fade-up" style={{ '--i': 1 } as any}>
        <div className="flex-1 flex items-center gap-2.5">
          <span className="label">Account</span>
          <span className="font-mono text-[12.5px] text-white/75 tabular-nums">{fmtAddr(address, 8)}</span>
          {explorer && address && (
            <a href={`${explorer}/address/${address}`} target="_blank" rel="noopener noreferrer"
              className="text-cyan-400/50 hover:text-cyan-300 transition-colors"><ArrowUpRightIcon className="w-3 h-3" /></a>
          )}
        </div>
        <div className="flex items-center gap-2.5">
          <span className="label">Key</span>
          <input value={keyName} onChange={e => setKeyName(e.target.value)} placeholder="default"
            className="input !w-36 !py-1.5 !text-[12px]" />
        </div>
      </div>

      {/* ═══ Tabs ═══ */}
      <div className="glass !rounded-full p-1 flex gap-1 fade-up" style={{ '--i': 2 } as any}>
        {TABS.map(t => {
          const Icon = t.icon
          const active = tab === t.id
          return (
            <button key={t.id} onClick={() => setTab(t.id)}
              className={`flex-1 flex items-center justify-center gap-1.5 py-2 rounded-full text-[12px] font-medium transition-all duration-200 ${
                active
                  ? 'text-white bg-gradient-to-b from-white/10 to-white/5 shadow-[inset_0_1px_0_rgba(255,255,255,0.1),0_0_18px_-8px_rgba(34,211,238,0.5)]'
                  : 'text-white/40 hover:text-white/75'
              }`}>
              <Icon className="w-4 h-4" /> {t.label}
            </button>
          )
        })}
      </div>

      {/* ── Wallet ── */}
      {tab === 'wallet' && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 fade-up" style={{ '--i': 3 } as any}>
          <Card title="Balances" accent="emerald">
            <div className="grid grid-cols-2 gap-2">
              {BAL_TOKENS.map(t => (
                <div key={t} className="rounded-xl border hairline bg-white/[0.02] px-4 py-3 row">
                  <p className="label">{t}</p>
                  <p className="text-[17px] font-semibold tabular-nums text-white/85 font-mono mt-0.5">{fmtNum(balances[t])}</p>
                </div>
              ))}
            </div>
          </Card>
          <Card title="Transfer" accent="sky">
            <Field label="Token">
              <select value={xferTok} onChange={e => setXferTok(e.target.value)} className="input">
                {BAL_TOKENS.map(t => <option key={t} value={t}>{t}</option>)}
              </select>
            </Field>
            <Field label="Recipient address">
              <input value={xferTo} onChange={e => setXferTo(e.target.value)} placeholder="0x..." className="input" />
            </Field>
            <Field label="Amount">
              <input value={xferAmt} onChange={e => setXferAmt(e.target.value)} placeholder="0.0" type="number" className="input" />
            </Field>
            <ActionBtn onClick={doTransfer} busy={busy === 'transfer'} icon={PaperAirplaneIcon}>Send</ActionBtn>
          </Card>
        </div>
      )}

      {/* ── Stake ── */}
      {tab === 'stake' && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 fade-up" style={{ '--i': 3 } as any}>
          <Card title="Stake NativeToken" accent="sky">
            <p className="text-[12px] text-white/35 leading-relaxed">
              Lock NativeToken to earn BlocTime. Longer locks accrue more BlocTime.
            </p>
            <Field label="Amount (NativeToken)">
              <input value={stakeAmt} onChange={e => setStakeAmt(e.target.value)} placeholder="0.0" type="number" className="input" />
            </Field>
            <Field label="Lock duration (blocks)">
              <input value={lockBlocks} onChange={e => setLockBlocks(e.target.value)} placeholder="100" type="number" className="input" />
            </Field>
            <ActionBtn onClick={doStake} busy={busy === 'stake'} icon={LockClosedIcon}>Stake</ActionBtn>
          </Card>
          <Card title={`Positions (${stakes.length})`} accent="violet">
            {stakes.length === 0 ? (
              <p className="text-[12px] text-white/25 py-6 text-center">No active stakes</p>
            ) : stakes.map(s => (
              <div key={s.stake_id} className="rounded-xl border hairline bg-white/[0.02] px-4 py-3 row space-y-2">
                <div className="flex items-center justify-between">
                  <span className="text-[13px] font-medium text-white/75">Stake #{s.stake_id}</span>
                  <button onClick={() => doUnstake(s.stake_id)} disabled={busy === `unstake-${s.stake_id}` || s.blocks_remaining > 0}
                    className="btn btn-danger !py-1 !px-2.5 !text-[10px]">
                    {busy === `unstake-${s.stake_id}` ? <ArrowPathIcon className="w-3 h-3 animate-spin" /> : <LockOpenIcon className="w-3 h-3" />}
                    Unstake
                  </button>
                </div>
                <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-[11px] text-white/35">
                  <span>Amount: <span className="text-white/70 font-mono tabular-nums">{fmtNum(s.amount)}</span></span>
                  <span>BlocTime: <span className="text-white/70 font-mono tabular-nums">{fmtNum(s.bloctime_balance)}</span></span>
                  <span>Lock: <span className="text-white/70 font-mono tabular-nums">{s.lock_blocks}</span></span>
                  <span>Remaining: <span className={`font-mono tabular-nums ${s.blocks_remaining > 0 ? 'text-amber-400' : 'text-emerald-400'}`}>{s.blocks_remaining}</span></span>
                </div>
              </div>
            ))}
          </Card>
        </div>
      )}

      {/* ── Market ── */}
      {tab === 'market' && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 fade-up" style={{ '--i': 3 } as any}>
          <Card title="Buy credits (MARKET)" accent="orange">
            <p className="text-[12px] text-white/35 leading-relaxed">
              Credit your account with MARKET stable tokens using a whitelisted payment token.
            </p>
            <Field label="Pay with">
              <select value={payToken} onChange={e => setPayToken(e.target.value)} className="input">
                <option value="usdt">USDT</option>
                <option value="usdc">USDC</option>
              </select>
            </Field>
            <Field label="Stable amount">
              <input value={creditAmt} onChange={e => setCreditAmt(e.target.value)} placeholder="0.0" type="number" className="input" />
            </Field>
            <ActionBtn onClick={doCredit} busy={busy === 'credit'} icon={BanknotesIcon}>Buy Credits</ActionBtn>
          </Card>
          <Card title="MARKET balance" accent="emerald">
            <div className="flex flex-col items-center justify-center py-8">
              <p className="stat-value !text-[40px] bg-gradient-to-r from-emerald-300 to-cyan-300 bg-clip-text text-transparent">{fmtNum(marketBal, 2)}</p>
              <p className="label mt-2">MARKET credits</p>
            </div>
          </Card>
        </div>
      )}

      {/* ── DeFi ── */}
      {tab === 'defi' && (
        <div className="space-y-4 fade-up" style={{ '--i': 3 } as any}>
          <Card title="Deposit / Withdraw" accent="emerald">
            <p className="text-[12px] text-white/35 leading-relaxed">
              Deposit a stablecoin into a yield strategy. Harvested profit is routed through
              the Market and minted to depositors as native tokens, distributed pro-rata.
            </p>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
              <Field label="Strategy">
                <select value={yId} onChange={e => setYId(e.target.value)} className="input">
                  {strategies.length === 0 && <option value="0">No strategies</option>}
                  {strategies.map(s => (
                    <option key={s.id} value={String(s.id)}>#{s.id} · {s.name}</option>
                  ))}
                </select>
              </Field>
              <Field label={`Amount${selStrat ? ` (${selStrat.asset_symbol})` : ''}`}>
                <input value={yAmt} onChange={e => setYAmt(e.target.value)} placeholder="0.0" type="number" className="input" />
              </Field>
              <div className="flex items-end gap-2">
                <ActionBtn onClick={doYieldDeposit} busy={busy === 'y-deposit'} icon={ArrowTrendingUpIcon}>Deposit</ActionBtn>
                <ActionBtn onClick={doYieldWithdraw} busy={busy === 'y-withdraw'} icon={ArrowUpRightIcon}>Withdraw</ActionBtn>
              </div>
            </div>
          </Card>

          <Card title={`Strategies (${strategies.length})`} accent="emerald">
            {strategies.length === 0 ? (
              <p className="text-[12px] text-white/25 py-6 text-center">No strategies deployed</p>
            ) : strategies.map(s => {
              const p = positions[s.id]
              return (
                <div key={s.id} className="rounded-xl border hairline bg-white/[0.02] px-4 py-3 row space-y-2">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <span className="text-[11px] text-white/25 font-mono tabular-nums">#{s.id}</span>
                      <span className="text-[13px] font-medium text-white/75">{s.name}</span>
                      {!s.enabled && <span className="chip chip-off !text-[9px]">paused</span>}
                    </div>
                    <div className="flex gap-1.5">
                      <button onClick={() => doYieldHarvest(s.id)} disabled={busy === `y-harvest-${s.id}`}
                        className="btn btn-teal !py-1 !px-2.5 !text-[10px]">
                        {busy === `y-harvest-${s.id}` ? <ArrowPathIcon className="w-3 h-3 animate-spin" /> : <SparklesIcon className="w-3 h-3" />}
                        Harvest
                      </button>
                      <button onClick={() => doYieldClaim(s.id)} disabled={busy === `y-claim-${s.id}` || !p?.pending_reward}
                        className="btn btn-primary !py-1 !px-2.5 !text-[10px]">
                        {busy === `y-claim-${s.id}` ? <ArrowPathIcon className="w-3 h-3 animate-spin" /> : <BanknotesIcon className="w-3 h-3" />}
                        Claim
                      </button>
                    </div>
                  </div>
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-x-3 gap-y-1 text-[11px] text-white/35">
                    <span>TVL: <span className="text-white/70 font-mono tabular-nums">{fmtNum(s.tvl, 2)}</span></span>
                    <span>Pending yield: <span className="text-amber-400 font-mono tabular-nums">{fmtNum(s.pending_profit, 2)}</span></span>
                    <span>Your shares: <span className="text-white/70 font-mono tabular-nums">{fmtNum(p?.shares, 2)}</span></span>
                    <span>Claimable: <span className="text-emerald-400 font-mono tabular-nums">{fmtNum(p?.pending_reward, 4)}</span></span>
                  </div>
                </div>
              )
            })}
          </Card>
        </div>
      )}

      {/* ── Registry ── */}
      {tab === 'registry' && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 fade-up" style={{ '--i': 3 } as any}>
          <Card title="Register a mod" accent="cyan">
            <Field label="Mod name">
              <input value={regName} onChange={e => setRegName(e.target.value)} placeholder="my-mod" className="input" />
            </Field>
            <Field label="Data / CID (optional)">
              <input value={regData} onChange={e => setRegData(e.target.value)} placeholder="Qm... (auto if blank)" className="input" />
            </Field>
            <ActionBtn onClick={doRegister} busy={busy === 'register'} icon={PlusCircleIcon}>Register</ActionBtn>
          </Card>
          <Card title={`My mods (${regMods.length})`} accent="cyan">
            {regMods.length === 0 ? (
              <p className="text-[12px] text-white/25 py-6 text-center">No registered mods</p>
            ) : (
              <div className="space-y-2">
                {regMods.map(mod => (
                  <div key={mod.id} className="rounded-xl border hairline bg-white/[0.02] px-4 py-3 row flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <span className="text-[11px] text-white/25 font-mono tabular-nums">#{mod.id}</span>
                      <span className="text-[13px] font-medium text-white/75">{mod.name}</span>
                    </div>
                    <span className="text-[11px] text-white/35 font-mono tabular-nums">{fmtAddr(mod.data, 4)}</span>
                  </div>
                ))}
              </div>
            )}
          </Card>
        </div>
      )}
    </Shell>
  )
}

export default dynamic(() => Promise.resolve(ProtocolInner), { ssr: false })
