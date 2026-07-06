"use client";

import { useState, useEffect, useCallback, useMemo } from 'react'
import dynamic from 'next/dynamic'
import { toast } from 'react-toastify'
import { ethers } from 'ethers'
import {
  ClockIcon,
  ArrowPathIcon,
  LockClosedIcon,
  LockOpenIcon,
  ChevronUpIcon,
  ChevronDownIcon,
  ChevronUpDownIcon,
  GiftIcon,
  UserGroupIcon,
  FireIcon,
  ChartBarIcon,
} from '@heroicons/react/24/outline'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8851'

// ── Types ───────────────────────────────────────────────────────────────

interface StakePosition {
  stakeId: number
  amount: string
  startBlock: number
  lockBlocks: number
  blocTimeBalance: string
  blocksRemaining: number
}

interface Overview {
  address: string
  stakeCount: number
  totalStaked: string
  totalBlocTime: string
  delegate: string
  pendingRewards: string
  votingPower: string
  positions: StakePosition[]
}

interface Stats {
  totalBlocTime: string
  totalSupply: string
  totalStakes: number
  address: string
  nativeToken: string
  network: string
  explorer: string
  currentEpoch: number
  epochReward: string
  totalDistributed: string
  lastDistributionEpoch: number
  inflationParams: {
    initialRewardPerEpoch: string
    halvingInterval: number
    minRewardPerEpoch: string
    epochLength: number
    startBlock: number
  }
}

interface MultiplierPoint {
  blocks: number
  multiplier: number
  multiplierX: number
}

interface InflationCurvePoint {
  epoch: number
  reward: string
}

type Tab = 'stake' | 'rewards'

// ── API helper ──────────────────────────────────────────────────────────

async function api(fn: string, params: Record<string, any> = {}, method = 'POST') {
  const opts: RequestInit = method === 'GET'
    ? { method: 'GET' }
    : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(params) }

  const res = await fetch(`${API_URL}/${fn}`, opts)
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || err.error || 'Request failed')
  }
  const data = await res.json()
  return data.result !== undefined ? data.result : data
}

const fmtEth = (wei: string) => {
  if (!wei || wei === '0') return '0'
  try {
    const val = Number(ethers.formatEther(wei))
    if (val === 0) return '0'
    if (val < 0.0001) return val.toExponential(2)
    if (val < 1) return val.toFixed(4)
    if (val < 1000) return val.toLocaleString(undefined, { maximumFractionDigits: 2 })
    if (val < 1_000_000) return (val / 1000).toFixed(1) + 'K'
    return (val / 1_000_000).toFixed(2) + 'M'
  } catch { return '0' }
}

const fmtAddr = (s: string) => s.length > 16 ? `${s.slice(0, 8)}...${s.slice(-6)}` : s

// ── Inflation Curve Chart ───────────────────────────────────────────────

function InflationChart({ points, currentEpoch, halvingInterval }: {
  points: InflationCurvePoint[], currentEpoch: number, halvingInterval: number
}) {
  const W = 600, H = 180, PAD_L = 60, PAD_R = 20, PAD_T = 25, PAD_B = 35
  const cw = W - PAD_L - PAD_R, ch = H - PAD_T - PAD_B
  const maxEpoch = points.length > 0 ? points[points.length - 1].epoch : 1
  const maxReward = Math.max(...points.map(p => Number(ethers.formatEther(p.reward))))
  const mRange = maxReward || 1

  const toX = (e: number) => PAD_L + (e / maxEpoch) * cw
  const toY = (r: number) => PAD_T + ch - (r / mRange) * ch

  const pathD = points.map((p, i) => {
    const r = Number(ethers.formatEther(p.reward))
    return `${i === 0 ? 'M' : 'L'}${toX(p.epoch).toFixed(1)},${toY(r).toFixed(1)}`
  }).join(' ')

  const fillD = pathD
    + ` L${toX(points[points.length - 1].epoch).toFixed(1)},${(PAD_T + ch).toFixed(1)}`
    + ` L${toX(points[0].epoch).toFixed(1)},${(PAD_T + ch).toFixed(1)} Z`

  const yTicks = 4
  const yLabels = Array.from({ length: yTicks + 1 }, (_, i) => (mRange * i) / yTicks)
  const xTicks = 5
  const xLabels = Array.from({ length: xTicks + 1 }, (_, i) => Math.round((maxEpoch * i) / xTicks))

  const markerX = toX(Math.min(currentEpoch, maxEpoch))

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ maxHeight: 200 }}>
      <defs>
        <linearGradient id="inflGrad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="rgba(251,191,36,0.12)" />
          <stop offset="100%" stopColor="rgba(251,191,36,0)" />
        </linearGradient>
        <linearGradient id="inflLine" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%" stopColor="rgba(251,191,36,0.6)" />
          <stop offset="100%" stopColor="rgba(249,115,22,0.4)" />
        </linearGradient>
      </defs>

      {yLabels.map((v, i) => (
        <g key={`y${i}`}>
          <line x1={PAD_L} y1={toY(v)} x2={W - PAD_R} y2={toY(v)} stroke="rgba(255,255,255,0.04)" strokeWidth="1" />
          <text x={PAD_L - 8} y={toY(v) + 3} textAnchor="end" fill="rgba(255,255,255,0.2)" fontSize="9" fontFamily="monospace">{v.toFixed(1)}</text>
        </g>
      ))}
      {xLabels.map((v, i) => (
        <g key={`x${i}`}>
          <line x1={toX(v)} y1={PAD_T} x2={toX(v)} y2={PAD_T + ch} stroke="rgba(255,255,255,0.03)" strokeWidth="1" />
          <text x={toX(v)} y={H - 8} textAnchor="middle" fill="rgba(255,255,255,0.18)" fontSize="8" fontFamily="monospace">
            {v >= 1000 ? `${(v / 1000).toFixed(0)}k` : v}
          </text>
        </g>
      ))}

      {halvingInterval > 0 && Array.from({ length: 5 }, (_, i) => (i + 1) * halvingInterval).filter(e => e <= maxEpoch).map((e, i) => (
        <line key={`h${i}`} x1={toX(e)} y1={PAD_T} x2={toX(e)} y2={PAD_T + ch} stroke="rgba(251,191,36,0.15)" strokeWidth="1" strokeDasharray="4,4" />
      ))}

      <path d={fillD} fill="url(#inflGrad)" />
      <path d={pathD} fill="none" stroke="url(#inflLine)" strokeWidth="2.5" strokeLinejoin="round" strokeLinecap="round" />

      {currentEpoch > 0 && currentEpoch <= maxEpoch && (
        <g>
          <line x1={markerX} y1={PAD_T} x2={markerX} y2={PAD_T + ch} stroke="rgba(34,211,238,0.3)" strokeWidth="1.5" strokeDasharray="4,4" />
          <text x={markerX} y={PAD_T - 6} textAnchor="middle" fill="rgba(34,211,238,0.8)" fontSize="9" fontFamily="monospace">
            epoch {currentEpoch}
          </text>
        </g>
      )}
    </svg>
  )
}

// ── Main Page ───────────────────────────────────────────────────────────

function BlocTimePageInner() {
  const [overview, setOverview] = useState<Overview | null>(null)
  const [stats, setStats] = useState<Stats | null>(null)
  const [points, setPoints] = useState<MultiplierPoint[]>([])
  const [loading, setLoading] = useState(false)
  const [connected, setConnected] = useState(false)
  const [account, setAccount] = useState('')
  const [tab, setTab] = useState<Tab>('stake')

  // Stake form
  const [stakeAmount, setStakeAmount] = useState('')
  const [lockBlocks, setLockBlocks] = useState('10000')
  const [staking, setStaking] = useState(false)

  // Sort
  type SortKey = 'amount' | 'bloctime' | 'remaining'
  type SortDir = 'asc' | 'desc'
  const [sortKey, setSortKey] = useState<SortKey>('remaining')
  const [sortDir, setSortDir] = useState<SortDir>('desc')

  // Delegation & Rewards
  const [delegateAddr, setDelegateAddr] = useState('')
  const [delegating, setDelegating] = useState(false)
  const [claiming, setClaiming] = useState(false)
  const [distributing, setDistributing] = useState(false)
  const [inflationCurve, setInflationCurve] = useState<InflationCurvePoint[]>([])

  // ── Wallet connect ──────────────────────────────────────────────────

  const connectWallet = useCallback(async () => {
    if (typeof window === 'undefined') return
    const w = window as any
    if (!w.ethereum) { toast.error('Install MetaMask'); return }
    try {
      const provider = new ethers.BrowserProvider(w.ethereum)
      const accounts = await provider.send('eth_requestAccounts', [])
      if (accounts.length > 0) {
        setAccount(accounts[0])
        setConnected(true)
        toast.success(`Connected: ${accounts[0].slice(0, 8)}...`)
      }
    } catch (err: any) {
      toast.error(err?.message || 'Connection failed')
    }
  }, [])

  // ── Data fetching ─────────────────────────────────────────────────

  const fetchAll = useCallback(async () => {
    setLoading(true)
    try {
      const [statsData, pointsData] = await Promise.all([
        api('stats', {}, 'GET').catch(() => null),
        api('points', {}, 'GET').catch(() => []),
      ])
      if (statsData) setStats(statsData)
      if (pointsData) setPoints(pointsData)

      if (account) {
        const ov = await api('overview', { address: account }).catch(() => null)
        if (ov) setOverview(ov)
      }
    } catch { /* ignore */ }
    setLoading(false)
  }, [account])

  const fetchInflationCurve = useCallback(async () => {
    try {
      const d = await api('get_inflation_curve', {}, 'GET')
      if (d?.points) setInflationCurve(d.points)
    } catch {}
  }, [])

  useEffect(() => { fetchAll(); fetchInflationCurve() }, [fetchAll, fetchInflationCurve])
  useEffect(() => {
    const iv = setInterval(fetchAll, 15000)
    return () => clearInterval(iv)
  }, [fetchAll])

  // ── Staking Actions ────────────────────────────────────────────────

  const handleStake = useCallback(async () => {
    if (!stakeAmount || Number(stakeAmount) <= 0) { toast.error('Enter amount'); return }
    setStaking(true)
    try {
      await api('stake', {
        amount: stakeAmount,
        lock_blocks: parseInt(lockBlocks),
        as_ether: true,
      })
      toast.success('Staked successfully')
      setStakeAmount('')
      fetchAll()
    } catch (err: any) {
      toast.error(err?.message || 'Stake failed')
    }
    setStaking(false)
  }, [stakeAmount, lockBlocks, fetchAll])

  const handleUnstake = useCallback(async (stakeId: number) => {
    try {
      await api('unstake', { stake_id: stakeId })
      toast.success('Unstaked successfully')
      fetchAll()
    } catch (err: any) {
      toast.error(err?.message || 'Unstake failed')
    }
  }, [fetchAll])

  // ── Delegation Actions ─────────────────────────────────────────────

  const handleDelegate = useCallback(async () => {
    if (!delegateAddr.trim()) { toast.error('Enter delegate address'); return }
    setDelegating(true)
    try {
      await api('delegate', { delegate_to: delegateAddr.trim() })
      toast.success('Delegated')
      setDelegateAddr('')
      fetchAll()
    } catch (err: any) {
      toast.error(err?.message || 'Delegation failed')
    }
    setDelegating(false)
  }, [delegateAddr, fetchAll])

  const handleUndelegate = useCallback(async () => {
    setDelegating(true)
    try {
      await api('undelegate', {})
      toast.success('Undelegated')
      fetchAll()
    } catch (err: any) {
      toast.error(err?.message || 'Undelegate failed')
    }
    setDelegating(false)
  }, [fetchAll])

  // ── Rewards Actions ────────────────────────────────────────────────

  const handleClaimRewards = useCallback(async () => {
    setClaiming(true)
    try {
      await api('claim_rewards', {})
      toast.success('Rewards claimed')
      fetchAll()
    } catch (err: any) {
      toast.error(err?.message || 'Claim failed')
    }
    setClaiming(false)
  }, [fetchAll])

  const handleDistribute = useCallback(async () => {
    setDistributing(true)
    try {
      await api('distribute_rewards', {})
      toast.success('Rewards distributed')
      fetchAll()
    } catch (err: any) {
      toast.error(err?.message || 'Distribution failed')
    }
    setDistributing(false)
  }, [fetchAll])

  // ── Sorting ────────────────────────────────────────────────────────

  const toggleSort = (key: SortKey) => {
    if (sortKey === key) setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    else { setSortKey(key); setSortDir('desc') }
  }

  const SortIcon = ({ col }: { col: SortKey }) => {
    if (sortKey !== col) return <ChevronUpDownIcon className="w-3 h-3 text-white/20" />
    return sortDir === 'asc'
      ? <ChevronUpIcon className="w-3 h-3 text-white/60" />
      : <ChevronDownIcon className="w-3 h-3 text-white/60" />
  }

  const sortedPositions = useMemo(() => {
    if (!overview) return []
    return [...overview.positions].sort((a, b) => {
      let cmp = 0
      if (sortKey === 'amount') cmp = Number(BigInt(a.amount) - BigInt(b.amount))
      else if (sortKey === 'bloctime') cmp = Number(BigInt(a.blocTimeBalance) - BigInt(b.blocTimeBalance))
      else if (sortKey === 'remaining') cmp = a.blocksRemaining - b.blocksRemaining
      return sortDir === 'asc' ? cmp : -cmp
    })
  }, [overview, sortKey, sortDir])

  // ── Multiplier preview ─────────────────────────────────────────────

  const currentMultiplier = useMemo(() => {
    const blocks = parseInt(lockBlocks) || 0
    if (points.length === 0) return 1.0
    if (blocks <= points[0].blocks) return points[0].multiplierX
    if (blocks >= points[points.length - 1].blocks) return points[points.length - 1].multiplierX
    for (let i = 0; i < points.length - 1; i++) {
      if (blocks >= points[i].blocks && blocks <= points[i + 1].blocks) {
        const range = points[i + 1].blocks - points[i].blocks
        const pos = blocks - points[i].blocks
        const yRange = points[i + 1].multiplierX - points[i].multiplierX
        return points[i].multiplierX + (yRange * pos) / range
      }
    }
    return points[points.length - 1].multiplierX
  }, [lockBlocks, points])

  return (
    <div className="min-h-screen bg-[#0a0a0f] text-[#e5e5e5] font-mono">
      <div className="relative z-10 p-4 md:p-6 max-w-5xl mx-auto space-y-5">

        {/* Header */}
        <div className="flex items-center justify-between border border-white/10 rounded-lg p-4 bg-white/[0.03]">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 flex items-center justify-center rounded-lg bg-white/5 border border-white/10">
              <ClockIcon className="w-5 h-5 text-white/70" />
            </div>
            <div>
              <h1 className="text-2xl font-bold tracking-wider uppercase">BlocTime</h1>
              <p className="text-xs text-white/40 uppercase tracking-wider">Time-Weighted Staking</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            {connected ? (
              <span className="text-xs text-emerald-400/70 font-mono">{fmtAddr(account)}</span>
            ) : (
              <button
                onClick={connectWallet}
                className="px-4 py-2 rounded-lg border border-white/10 bg-white/5 text-xs font-bold uppercase tracking-wider text-white/50 hover:bg-white/10 hover:text-white/70 transition-colors"
              >
                Connect
              </button>
            )}
            <button
              onClick={fetchAll}
              disabled={loading}
              className="p-2 rounded-lg border border-white/10 bg-white/5 text-white/40 hover:text-white/70 hover:bg-white/10 disabled:opacity-30 transition-colors"
            >
              <ArrowPathIcon className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
            </button>
          </div>
        </div>

        {/* Tab Bar */}
        <div className="flex gap-1 p-1 rounded-lg border border-white/10 bg-white/[0.02]">
          {([['stake', 'Stake', LockClosedIcon], ['rewards', 'Rewards', GiftIcon]] as [Tab, string, any][]).map(([t, label, Icon]) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`flex-1 flex items-center justify-center gap-2 py-2.5 rounded-md text-[10px] font-bold uppercase tracking-wider transition-all
                ${tab === t
                  ? 'bg-white/[0.06] text-white/90'
                  : 'text-white/30 hover:text-white/50 hover:bg-white/[0.02]'
                }`}
            >
              <Icon className="w-3.5 h-3.5" />
              {label}
            </button>
          ))}
        </div>

        {/* Stats */}
        {stats && (
          <div className="grid grid-cols-2 md:grid-cols-4 border border-white/10 rounded-lg bg-white/[0.02] overflow-hidden">
            <div className="p-4 text-center border-r border-b md:border-b-0 border-white/[0.06]">
              <p className="text-lg font-bold text-cyan-400 tabular-nums">{stats.totalStakes}</p>
              <p className="text-[10px] font-bold uppercase tracking-wider text-white/30 mt-1">Total Stakes</p>
            </div>
            <div className="p-4 text-center border-b md:border-b-0 md:border-r border-white/[0.06]">
              <p className="text-lg font-bold text-amber-400 tabular-nums">{fmtEth(stats.totalBlocTime)}</p>
              <p className="text-[10px] font-bold uppercase tracking-wider text-white/30 mt-1">Total BlocTime</p>
            </div>
            <div className="p-4 text-center border-r border-white/[0.06]">
              <p className="text-lg font-bold text-emerald-400 tabular-nums">{fmtEth(stats.totalSupply)}</p>
              <p className="text-[10px] font-bold uppercase tracking-wider text-white/30 mt-1">BT Supply</p>
            </div>
            <div className="p-4 text-center">
              <p className="text-lg font-bold text-violet-400 tabular-nums">{stats.network || '--'}</p>
              <p className="text-[10px] font-bold uppercase tracking-wider text-white/30 mt-1">Network</p>
            </div>
          </div>
        )}

        {/* Account overview with rewards info */}
        {connected && overview && (
          <div className="grid grid-cols-2 md:grid-cols-4 border border-white/10 rounded-lg bg-white/[0.02] overflow-hidden">
            <div className="p-4 text-center border-r border-b md:border-b-0 border-white/[0.06]">
              <p className="text-lg font-bold text-cyan-400 tabular-nums">{fmtEth(overview.totalBlocTime)}</p>
              <p className="text-[10px] font-bold uppercase tracking-wider text-white/30 mt-1">My BLOC</p>
            </div>
            <div className="p-4 text-center border-b md:border-b-0 md:border-r border-white/[0.06]">
              <p className="text-lg font-bold text-amber-400 tabular-nums">{fmtEth(overview.totalStaked)}</p>
              <p className="text-[10px] font-bold uppercase tracking-wider text-white/30 mt-1">Staked</p>
            </div>
            <div className="p-4 text-center border-r border-white/[0.06]">
              <p className="text-lg font-bold text-emerald-400 tabular-nums">{fmtEth(overview.pendingRewards)}</p>
              <p className="text-[10px] font-bold uppercase tracking-wider text-white/30 mt-1">Pending Rewards</p>
            </div>
            <div className="p-4 text-center">
              <p className="text-lg font-bold text-violet-400 tabular-nums">{fmtEth(overview.votingPower)}</p>
              <p className="text-[10px] font-bold uppercase tracking-wider text-white/30 mt-1">Voting Power</p>
            </div>
          </div>
        )}

        {/* ── Stake Tab ────────────────────────────────────────────────── */}
        {tab === 'stake' && (
          <>
            {/* Multiplier Curve */}
            {points.length > 0 && (
              <div className="border border-white/10 rounded-lg p-4 bg-white/[0.02]">
                <p className="text-[10px] font-bold uppercase tracking-wider text-white/40 mb-3">Multiplier Curve</p>
                <div className="flex items-end gap-1 h-16">
                  {points.map((pt, i) => (
                    <div key={i} className="flex flex-col items-center flex-1">
                      <div
                        className="w-full bg-gradient-to-t from-cyan-500/40 to-cyan-400/20 rounded-t"
                        style={{ height: `${(pt.multiplierX / (points[points.length - 1]?.multiplierX || 3)) * 100}%` }}
                      />
                      <span className="text-[9px] text-white/30 mt-1">{(pt.blocks / 1000).toFixed(0)}k</span>
                      <span className="text-[9px] text-cyan-400/60">{pt.multiplierX}x</span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Stake Form */}
            <div className="border border-white/10 rounded-lg p-4 bg-white/[0.02]">
              <div className="flex items-center gap-2 mb-3">
                <LockClosedIcon className="w-4 h-4 text-white/40" />
                <span className="text-[10px] font-bold uppercase tracking-wider text-white/40">Stake Tokens</span>
              </div>
              <div className="flex items-center gap-3 flex-wrap">
                <input
                  type="number"
                  placeholder="Amount (NTV)"
                  value={stakeAmount}
                  onChange={e => setStakeAmount(e.target.value)}
                  className="flex-1 min-w-[120px] text-sm px-4 py-2.5 rounded-lg border border-white/10 bg-white/5 text-white/90 focus:outline-none focus:border-white/30 font-mono transition-colors placeholder:text-white/20"
                />
                <input
                  type="number"
                  placeholder="Lock blocks"
                  value={lockBlocks}
                  onChange={e => setLockBlocks(e.target.value)}
                  className="w-32 text-sm px-4 py-2.5 rounded-lg border border-white/10 bg-white/5 text-white/90 focus:outline-none focus:border-white/30 font-mono transition-colors placeholder:text-white/20"
                />
                <div className="flex items-center gap-2">
                  <span className="text-xs text-cyan-400/70">{currentMultiplier.toFixed(2)}x</span>
                  {stakeAmount && (
                    <span className="text-xs text-emerald-400/60">
                      = {(Number(stakeAmount) * currentMultiplier).toFixed(2)} BT
                    </span>
                  )}
                </div>
                <button
                  onClick={handleStake}
                  disabled={staking || !stakeAmount}
                  className="px-4 py-2.5 rounded-lg border border-cyan-500/40 bg-cyan-500/15 text-cyan-300 text-[10px] font-bold uppercase tracking-wider hover:bg-cyan-500/25 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                >
                  {staking ? <ArrowPathIcon className="w-3.5 h-3.5 animate-spin" /> : 'Stake'}
                </button>
              </div>
            </div>

            {/* My Positions */}
            {overview && (
              <div className="border border-white/10 rounded-lg bg-white/[0.02] overflow-hidden">
                <div className="flex items-center justify-between p-4 border-b border-white/5">
                  <span className="text-[10px] font-bold uppercase tracking-wider text-white/40">
                    My Positions ({overview.stakeCount})
                  </span>
                  <div className="flex gap-3 text-xs">
                    <span className="text-white/40">Staked: <span className="text-amber-400">{fmtEth(overview.totalStaked)}</span></span>
                    <span className="text-white/40">BlocTime: <span className="text-cyan-400">{fmtEth(overview.totalBlocTime)}</span></span>
                  </div>
                </div>

                <div className="grid grid-cols-[60px_1fr_1fr_1fr_80px_60px] gap-2 px-4 py-2.5 border-b border-white/5 bg-white/[0.01]">
                  <div className="text-[10px] font-bold uppercase tracking-wider text-white/40">ID</div>
                  <button onClick={() => toggleSort('amount')} className="flex items-center gap-1 text-[10px] font-bold uppercase tracking-wider text-white/40 hover:text-white/60 transition-colors justify-end">
                    Staked <SortIcon col="amount" />
                  </button>
                  <button onClick={() => toggleSort('bloctime')} className="flex items-center gap-1 text-[10px] font-bold uppercase tracking-wider text-white/40 hover:text-white/60 transition-colors justify-end">
                    BlocTime <SortIcon col="bloctime" />
                  </button>
                  <div className="text-[10px] font-bold uppercase tracking-wider text-white/40 text-right">Lock</div>
                  <button onClick={() => toggleSort('remaining')} className="flex items-center gap-1 text-[10px] font-bold uppercase tracking-wider text-white/40 hover:text-white/60 transition-colors justify-end">
                    Left <SortIcon col="remaining" />
                  </button>
                  <div className="text-[10px] font-bold uppercase tracking-wider text-white/40 text-center">Act</div>
                </div>

                {sortedPositions.length === 0 ? (
                  <div className="text-center py-12">
                    <span className="text-xs text-white/30 uppercase tracking-wider">No active stakes</span>
                  </div>
                ) : (
                  <div>
                    {sortedPositions.map((pos) => {
                      const unlocked = pos.blocksRemaining === 0
                      return (
                        <div
                          key={pos.stakeId}
                          className="grid grid-cols-[60px_1fr_1fr_1fr_80px_60px] gap-2 px-4 py-2.5 border-b border-white/[0.03] hover:bg-white/[0.03] transition-colors items-center"
                        >
                          <span className="text-xs font-mono text-white/50">#{pos.stakeId}</span>
                          <span className="text-xs font-bold text-amber-400/80 text-right tabular-nums">
                            {fmtEth(pos.amount)}
                          </span>
                          <span className="text-xs font-bold text-cyan-400/80 text-right tabular-nums">
                            {fmtEth(pos.blocTimeBalance)}
                          </span>
                          <span className="text-xs text-white/40 text-right tabular-nums">
                            {pos.lockBlocks.toLocaleString()} blk
                          </span>
                          <span className={`text-xs text-right tabular-nums ${unlocked ? 'text-emerald-400' : 'text-white/40'}`}>
                            {unlocked ? 'Ready' : `${pos.blocksRemaining.toLocaleString()}`}
                          </span>
                          <div className="flex justify-center">
                            <button
                              onClick={() => handleUnstake(pos.stakeId)}
                              disabled={!unlocked}
                              className={`p-1.5 rounded-lg border transition-colors ${
                                unlocked
                                  ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300 hover:bg-emerald-500/20'
                                  : 'border-white/5 bg-white/[0.02] text-white/15 cursor-not-allowed'
                              }`}
                              title={unlocked ? 'Unstake' : 'Still locked'}
                            >
                              <LockOpenIcon className="w-3.5 h-3.5" />
                            </button>
                          </div>
                        </div>
                      )
                    })}
                  </div>
                )}
              </div>
            )}
          </>
        )}

        {/* ── Rewards Tab ──────────────────────────────────────────────── */}
        {tab === 'rewards' && (
          <>
            {/* Epoch Stats */}
            {stats && (
              <div className="grid grid-cols-2 md:grid-cols-4 border border-white/10 rounded-lg bg-white/[0.02] overflow-hidden">
                <div className="p-4 text-center border-r border-b md:border-b-0 border-white/[0.06]">
                  <p className="text-lg font-bold text-cyan-400 tabular-nums">{stats.currentEpoch}</p>
                  <p className="text-[10px] font-bold uppercase tracking-wider text-white/30 mt-1">Current Epoch</p>
                </div>
                <div className="p-4 text-center border-b md:border-b-0 md:border-r border-white/[0.06]">
                  <p className="text-lg font-bold text-amber-400 tabular-nums">{fmtEth(stats.epochReward)}</p>
                  <p className="text-[10px] font-bold uppercase tracking-wider text-white/30 mt-1">Epoch Reward</p>
                </div>
                <div className="p-4 text-center border-r border-white/[0.06]">
                  <p className="text-lg font-bold text-emerald-400 tabular-nums">{connected && overview ? fmtEth(overview.pendingRewards) : '--'}</p>
                  <p className="text-[10px] font-bold uppercase tracking-wider text-white/30 mt-1">Your Pending</p>
                </div>
                <div className="p-4 text-center">
                  <p className="text-lg font-bold text-violet-400 tabular-nums">{fmtEth(stats.totalDistributed)}</p>
                  <p className="text-[10px] font-bold uppercase tracking-wider text-white/30 mt-1">Total Distributed</p>
                </div>
              </div>
            )}

            {/* Inflation Curve */}
            {inflationCurve.length > 1 && stats && (
              <div className="border border-white/10 rounded-lg p-4 bg-white/[0.02]">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-[10px] font-bold uppercase tracking-wider text-white/40">Bitcoin-Style Inflation Curve</span>
                  {stats.inflationParams?.halvingInterval > 0 && (
                    <span className="text-[10px] text-amber-400/70 tabular-nums">
                      Halving every {stats.inflationParams.halvingInterval} epochs (~{(stats.inflationParams.halvingInterval / 365.25).toFixed(1)} years)
                    </span>
                  )}
                </div>
                <InflationChart
                  points={inflationCurve}
                  currentEpoch={stats.currentEpoch}
                  halvingInterval={stats.inflationParams?.halvingInterval || 0}
                />
              </div>
            )}

            {/* Delegation */}
            <div className="border border-white/10 rounded-lg p-4 bg-white/[0.02]">
              <div className="flex items-center gap-2 mb-4">
                <UserGroupIcon className="w-4 h-4 text-white/40" />
                <span className="text-[10px] font-bold uppercase tracking-wider text-white/40">Delegation</span>
              </div>

              {connected && overview?.delegate ? (
                <div className="mb-4 p-3 rounded-lg border border-emerald-500/15 bg-emerald-500/[0.03]">
                  <div className="flex items-center justify-between">
                    <div>
                      <p className="text-[10px] text-white/25 uppercase tracking-wider mb-0.5">Delegated To</p>
                      <p className="text-sm font-mono text-emerald-400/80 cursor-pointer hover:text-emerald-300 transition-colors"
                         onClick={() => { navigator.clipboard.writeText(overview.delegate); toast.success('Copied') }}>
                        {fmtAddr(overview.delegate)}
                      </p>
                    </div>
                    <button
                      onClick={handleUndelegate}
                      disabled={delegating}
                      className="px-3 py-1.5 rounded-lg text-[10px] font-bold uppercase tracking-wider border border-rose-500/30 bg-rose-500/10 text-rose-400 hover:bg-rose-500/20 disabled:opacity-30 transition-colors"
                    >
                      {delegating ? 'Removing...' : 'Undelegate'}
                    </button>
                  </div>
                </div>
              ) : connected ? (
                <p className="text-xs text-white/20 mb-4">Not delegated. Delegate voting power to another address.</p>
              ) : (
                <p className="text-xs text-white/20 mb-4">Connect wallet to manage delegation.</p>
              )}

              <div className="flex gap-2">
                <input
                  type="text"
                  placeholder="Delegate address (0x...)"
                  value={delegateAddr}
                  onChange={e => setDelegateAddr(e.target.value)}
                  className="flex-1 text-sm px-4 py-2.5 rounded-lg border border-white/10 bg-white/5 text-white/90 focus:outline-none focus:border-white/30 font-mono transition-colors placeholder:text-white/20"
                />
                <button
                  onClick={handleDelegate}
                  disabled={delegating || !connected || !delegateAddr.trim()}
                  className="px-4 py-2.5 rounded-lg border border-cyan-500/40 bg-cyan-500/15 text-cyan-300 text-[10px] font-bold uppercase tracking-wider hover:bg-cyan-500/25 disabled:opacity-30 disabled:cursor-not-allowed transition-colors flex items-center gap-2"
                >
                  {delegating ? <ArrowPathIcon className="w-3.5 h-3.5 animate-spin" /> : <UserGroupIcon className="w-3.5 h-3.5" />}
                  Delegate
                </button>
              </div>
            </div>

            {/* Claim Rewards */}
            <div className="border border-white/10 rounded-lg p-4 bg-white/[0.02]">
              <div className="flex items-center gap-2 mb-4">
                <GiftIcon className="w-4 h-4 text-white/40" />
                <span className="text-[10px] font-bold uppercase tracking-wider text-white/40">Rewards</span>
              </div>

              {connected && overview && (
                <div className="mb-4 p-3 rounded-lg border border-emerald-500/10 bg-emerald-500/[0.03]">
                  <p className="text-[10px] text-white/25 uppercase tracking-wider mb-0.5">Claimable</p>
                  <p className="text-2xl font-bold text-emerald-400 tabular-nums">{fmtEth(overview.pendingRewards)} BLOC</p>
                </div>
              )}

              <div className="flex gap-2">
                <button
                  onClick={handleClaimRewards}
                  disabled={claiming || !connected || !overview || overview.pendingRewards === '0'}
                  className="flex-1 py-3 rounded-lg border border-emerald-500/30 bg-emerald-500/10 text-emerald-300 text-[10px] font-bold uppercase tracking-wider hover:bg-emerald-500/20 disabled:opacity-30 disabled:cursor-not-allowed transition-colors flex items-center justify-center gap-2"
                >
                  {claiming ? <><ArrowPathIcon className="w-3.5 h-3.5 animate-spin" /> Claiming...</> : <><GiftIcon className="w-3.5 h-3.5" /> Claim Rewards</>}
                </button>
                <button
                  onClick={handleDistribute}
                  disabled={distributing || !connected}
                  className="flex-1 py-3 rounded-lg border border-amber-500/30 bg-amber-500/10 text-amber-300 text-[10px] font-bold uppercase tracking-wider hover:bg-amber-500/20 disabled:opacity-30 disabled:cursor-not-allowed transition-colors flex items-center justify-center gap-2"
                >
                  {distributing ? <><ArrowPathIcon className="w-3.5 h-3.5 animate-spin" /> Distributing...</> : <><FireIcon className="w-3.5 h-3.5" /> Distribute Epoch</>}
                </button>
              </div>

              {stats && stats.currentEpoch > stats.lastDistributionEpoch && (
                <p className="text-[10px] text-amber-400/50 mt-2 text-center">
                  {stats.currentEpoch - stats.lastDistributionEpoch} epoch{stats.currentEpoch - stats.lastDistributionEpoch !== 1 ? 's' : ''} ready to distribute
                </p>
              )}
            </div>
          </>
        )}

        {/* Footer */}
        <div className="text-center text-xs text-white/15 uppercase tracking-wider py-4">
          BlocTime Module
        </div>
      </div>
    </div>
  )
}

export default dynamic(() => Promise.resolve(BlocTimePageInner), { ssr: false })
