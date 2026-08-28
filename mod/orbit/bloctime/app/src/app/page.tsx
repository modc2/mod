"use client";

import { useState, useEffect, useCallback, useMemo, useRef } from 'react'
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
  CodeBracketIcon,
  DocumentDuplicateIcon,
  ArrowTopRightOnSquareIcon,
  PlayIcon,
  BoltIcon,
  BuildingStorefrontIcon,
  RocketLaunchIcon,
  ArrowsRightLeftIcon,
  CheckCircleIcon,
  XMarkIcon,
} from '@heroicons/react/24/outline'
import { useThemeColors } from './theme'
import ThemePicker from './ThemePicker'

// Remote browsers can't reach localhost:8851 — go through the gateway's
// /api/bloctime route unless we're actually running on localhost.
const isLocal = typeof window !== 'undefined' &&
  ['localhost', '127.0.0.1'].includes(window.location.hostname)
const API_URL = isLocal
  ? (process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8851')
  : '/api/bloctime'

// ── Networks ────────────────────────────────────────────────────────────

interface NetworkDef {
  chainId: string
  label: string
  rpc: string
  symbol: string
}

// Chains you can deploy to / point the wallet at. The header picker drives
// the wallet's chain; everything else (deploy, instance writes) follows it.
const NETWORKS: NetworkDef[] = [
  { chainId: '84532', label: 'Base Sepolia', rpc: 'https://sepolia.base.org', symbol: 'ETH' },
  { chainId: '8453', label: 'Base', rpc: 'https://mainnet.base.org', symbol: 'ETH' },
  { chainId: '11155111', label: 'Sepolia', rpc: 'https://ethereum-sepolia-rpc.publicnode.com', symbol: 'ETH' },
  { chainId: '1', label: 'Ethereum', rpc: 'https://ethereum-rpc.publicnode.com', symbol: 'ETH' },
  { chainId: '10', label: 'Optimism', rpc: 'https://mainnet.optimism.io', symbol: 'ETH' },
  { chainId: '42161', label: 'Arbitrum One', rpc: 'https://arb1.arbitrum.io/rpc', symbol: 'ETH' },
  { chainId: '137', label: 'Polygon', rpc: 'https://polygon-rpc.com', symbol: 'POL' },
  { chainId: '1337', label: 'Localhost', rpc: 'http://localhost:8545', symbol: 'ETH' },
]

const DEFAULT_CHAIN = '84532'

const netFor = (chainId: string): NetworkDef | null =>
  NETWORKS.find(n => n.chainId === chainId) || null

const netLabel = (chainId: string) => netFor(chainId)?.label || (chainId ? `Chain ${chainId}` : 'Unknown')

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
  blocBalance: string   // BLOC held — what the weekly pot is split by
  positions: StakePosition[]
}

interface PotInfo {
  pot: string
  pendingInflation: string
  projected: string
  eligibleSupply: string
  nextDistribution: number   // unix seconds
  lastDistribution: number
  due: boolean
  secondsRemaining: number
  schedule: string
}

interface Stats {
  pot: PotInfo | null
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

interface AbiParam {
  name: string
  type: string
  components?: AbiParam[]
}

interface AbiEntry {
  type: string
  name?: string
  inputs?: AbiParam[]
  outputs?: AbiParam[]
  stateMutability?: string
  anonymous?: boolean
}

interface ContractInfo {
  id: string
  name: string
  address: string
  chainId: string
  rpc: string
  explorer: string
  abi: AbiEntry[]
  source: string
  deployed?: boolean      // deployed through this console, not shipped with the module
  deployer?: string
}

interface CompiledContract {
  name: string
  abi: AbiEntry[]
  bytecode: string
  constructor: AbiParam[]
  deployable: boolean     // false for interfaces and abstract contracts
}

interface Compiled {
  solc: string
  filename: string
  contracts: CompiledContract[]
  warnings: string[]
}

interface Deployment {
  id: string
  name: string
  address: string
  chainId: string
  rpc: string
  deployer: string
  txHash: string
  explorer: string
  createdAt: string
}

interface ContractsMeta {
  network: string
  chainId: string
  rpc: string
  signer: string
  contracts: ContractInfo[]
}

interface InstanceStats {
  totalBlocTime: string
  totalSupply: string
  totalStakes: number
}

interface Instance {
  id: string
  name: string
  description: string
  chainId: string
  rpc: string
  bloctime: string
  nativeToken: string
  owner: string
  official: boolean
  explorer: string
  createdAt: string
  stats?: InstanceStats | null
}

interface FactoryContract {
  abi: AbiEntry[]
  bytecode: string
}

interface FactoryKit {
  contracts: { bloctime: FactoryContract; nativeToken: FactoryContract }
  defaults: {
    initialSupply: string
    maxLockBlocks: number
    distributionPercentage: number
    points: { blocks: number; multiplier: number }[]
    inflation: {
      initialRewardPerEpoch: string
      halvingInterval: number
      minRewardPerEpoch: string
      epochLength: number
    }
  }
  fork: string
}

interface BridgeInfo {
  online: boolean
  health: Record<string, any>
  totals: Record<string, any>
  app: string
  api: string
}

type Tab = 'stake' | 'rewards' | 'market' | 'deploy' | 'bridge' | 'contracts'

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

// Contracts deployed before getPoints() answer /points with an empty list.
// getMultiplier() still works one lock length at a time, so sample it — a
// curve you can read beats a panel that says "unavailable".
const SAMPLE_LOCKS = [0, 10_000, 50_000, 100_000, 200_000]

// Sequential on purpose: each call is an RPC round-trip on the API side, and
// firing all five at once gets the batch rate-limited — a half-sampled curve
// is worse than a slightly slower one.
async function sampleCurve(): Promise<MultiplierPoint[]> {
  const pts: MultiplierPoint[] = []
  for (const blocks of SAMPLE_LOCKS) {
    try {
      const r = await api('get_multiplier', { block_count: blocks })
      pts.push({ blocks, multiplier: r.multiplier, multiplierX: r.multiplierX })
    } catch { return [] }   // partial curves lie about the shape — drop it
  }
  return pts
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

// The pot opens once a week, Friday 12:00 EST — 17:00 UTC year round.
const WEEKLY_SCHEDULE = 'Weekly, Friday 12:00 EST (17:00 UTC)'

const fmtCountdown = (secs: number) => {
  if (secs <= 0) return 'now'
  const d = Math.floor(secs / 86400), h = Math.floor((secs % 86400) / 3600)
  const m = Math.floor((secs % 3600) / 60), s = secs % 60
  return d > 0 ? `${d}d ${h}h ${m}m` : h > 0 ? `${h}h ${m}m ${s}s` : `${m}m ${s}s`
}

const fmtWhen = (unix: number) =>
  unix ? new Date(unix * 1000).toLocaleString(undefined, {
    weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
  }) : '--'

const BRIDGE_APP_URL = isLocal ? 'http://localhost:8841/bridge' : '/bridge'

// ── Stat tiles ──────────────────────────────────────────────────────────
// A tone is one of the five voices, never a hue: the tile paints its number
// and its top hairline from `--tone`, so every skin carries the meaning.

type Tone = 'accent' | 'up' | 'gold' | 'iris' | 'down' | 'ink'

const TONE: Record<Tone, string> = {
  accent: 'var(--accent)',
  up: 'var(--up)',
  gold: 'var(--gold)',
  iris: 'var(--iris)',
  down: 'var(--down)',
  ink: 'rgb(var(--ink-rgb))',
}

function Stat({ label, value, tone = 'accent', hint }: {
  label: string
  value: React.ReactNode
  tone?: Tone
  hint?: React.ReactNode
}) {
  return (
    <div className="stat bg-panel" style={{ ['--tone' as any]: TONE[tone] }}>
      <p className="stat-val">{value}</p>
      <span className="stat-lab">{label}</span>
      {hint && <span className="block text-[10px] text-faint mt-1 tabular-nums">{hint}</span>}
    </div>
  )
}

// gap-px over a hairline background draws every divider in the grid — no
// per-tile border juggling at the row/column breaks.
function StatGrid({ children }: { children: React.ReactNode }) {
  return (
    <div className="card overflow-hidden grid grid-cols-2 md:grid-cols-4 gap-px bg-hair">
      {children}
    </div>
  )
}

// Halving schedule is deterministic — no need to hit the RPC per point.
function computeInflationCurve(infl: Stats['inflationParams'] | null): InflationCurvePoint[] {
  if (!infl || infl.halvingInterval <= 0) return []
  const initial = BigInt(infl.initialRewardPerEpoch || '0')
  const minReward = BigInt(infl.minRewardPerEpoch || '0')
  const totalEpochs = infl.halvingInterval * 5
  const steps = Math.min(100, totalEpochs)
  const out: InflationCurvePoint[] = []
  for (let i = 0; i <= steps; i++) {
    const epoch = Math.floor((totalEpochs * i) / steps)
    const halvings = Math.min(Math.floor(epoch / infl.halvingInterval), 250)
    let reward = initial >> BigInt(halvings)
    if (reward < minReward) reward = minReward
    out.push({ epoch, reward: reward.toString() })
  }
  return out
}

// ── Instance-direct chain reads ─────────────────────────────────────────
// Non-official market instances aren't served by our API — read them
// straight from their RPC and write through the connected wallet.

async function readInstanceState(inst: Instance, kit: FactoryKit): Promise<{ stats: Stats; points: MultiplierPoint[] }> {
  const provider = new ethers.JsonRpcProvider(inst.rpc)
  const c = new ethers.Contract(inst.bloctime, kit.contracts.bloctime.abi as any, provider)

  const [totalBT, supply, nextId] = await Promise.all([
    c.totalBlocTime(), c.totalSupply(), c.nextStakeId(),
  ])

  let infl: Stats['inflationParams'] | null = null
  let epoch = 0n, epochReward = 0n, totalDist = 0n, lastDist = 0n
  try {
    const ip = await c.getInflationParams()
    epoch = await c.currentEpoch()
    epochReward = epoch > 0n ? await c.getEpochReward(epoch) : 0n
    totalDist = await c.totalDistributed()
    lastDist = await c.lastDistributionEpoch()
    infl = {
      initialRewardPerEpoch: ip[0].toString(),
      halvingInterval: Number(ip[1]),
      minRewardPerEpoch: ip[2].toString(),
      epochLength: Number(ip[3]),
      startBlock: Number(ip[4]),
    }
  } catch { /* older contract without inflation */ }

  let points: MultiplierPoint[] = []
  try {
    const raw = await c.getPoints()
    points = raw.map((p: any) => ({
      blocks: Number(p[0]), multiplier: Number(p[1]), multiplierX: Number(p[1]) / 10000,
    }))
  } catch { /* older contract without getPoints */ }

  let pot: PotInfo | null = null
  try {
    const pi = await c.getPotInfo()
    const next = Number(pi[3])
    pot = {
      pot: pi[0].toString(),
      pendingInflation: pi[1].toString(),
      projected: (pi[0] + pi[1]).toString(),
      eligibleSupply: pi[2].toString(),
      nextDistribution: next,
      lastDistribution: Number(pi[4]),
      due: Boolean(pi[5]),
      secondsRemaining: Math.max(0, next - Math.floor(Date.now() / 1000)),
      schedule: WEEKLY_SCHEDULE,
    }
  } catch { /* older contract without the weekly pot */ }

  const stats: Stats = {
    pot,
    totalBlocTime: totalBT.toString(),
    totalSupply: supply.toString(),
    totalStakes: Number(nextId),
    address: inst.bloctime,
    nativeToken: inst.nativeToken,
    network: inst.name,
    explorer: inst.explorer,
    currentEpoch: Number(epoch),
    epochReward: epochReward.toString(),
    totalDistributed: totalDist.toString(),
    lastDistributionEpoch: Number(lastDist),
    inflationParams: infl as Stats['inflationParams'],
  }
  return { stats, points }
}

async function readInstanceOverview(inst: Instance, kit: FactoryKit, addr: string): Promise<Overview> {
  const provider = new ethers.JsonRpcProvider(inst.rpc)
  const c = new ethers.Contract(inst.bloctime, kit.contracts.bloctime.abi as any, provider)
  const ids: bigint[] = await c.getUserStakeIds(addr)
  const positions: StakePosition[] = await Promise.all([...ids].map(async sid => {
    const p = await c.getStakePosition(addr, sid)
    return {
      stakeId: Number(sid), amount: p[0].toString(), startBlock: Number(p[1]),
      lockBlocks: Number(p[2]), blocTimeBalance: p[3].toString(), blocksRemaining: Number(p[4]),
    }
  }))
  let pending = 0n, vp = 0n, deleg = '', bloc = 0n
  try { pending = await c.earned(addr) } catch {}
  try { vp = await c.getVotingPower(addr) } catch {}
  try { bloc = await c.balanceOf(addr) } catch {}
  try {
    const d = await c.delegates(addr)
    deleg = d === ethers.ZeroAddress ? '' : d
  } catch {}
  return {
    address: addr,
    stakeCount: positions.length,
    totalStaked: positions.reduce((a, p) => a + BigInt(p.amount), 0n).toString(),
    totalBlocTime: positions.reduce((a, p) => a + BigInt(p.blocTimeBalance), 0n).toString(),
    delegate: deleg,
    pendingRewards: pending.toString(),
    votingPower: vp.toString(),
    blocBalance: bloc.toString(),
    positions,
  }
}

// ── Inflation Curve Chart ───────────────────────────────────────────────

function InflationChart({ points, currentEpoch, halvingInterval }: {
  points: InflationCurvePoint[], currentEpoch: number, halvingInterval: number
}) {
  const c = useThemeColors()
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
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ maxHeight: 200 }} role="img"
         aria-label="Reward per epoch, halving on a Bitcoin-style schedule">
      <defs>
        <linearGradient id="inflGrad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={c.gold} stopOpacity="0.28" />
          <stop offset="100%" stopColor={c.gold} stopOpacity="0" />
        </linearGradient>
        <linearGradient id="inflLine" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%" stopColor={c.gold} />
          <stop offset="100%" stopColor={c.down} stopOpacity="0.7" />
        </linearGradient>
      </defs>

      {yLabels.map((v, i) => (
        <g key={`y${i}`}>
          <line x1={PAD_L} y1={toY(v)} x2={W - PAD_R} y2={toY(v)} stroke={c.line} strokeOpacity="0.5" strokeWidth="1" />
          <text x={PAD_L - 8} y={toY(v) + 3} textAnchor="end" fill={c.faint} fontSize="9" fontFamily="var(--font-num)">{v.toFixed(1)}</text>
        </g>
      ))}
      {xLabels.map((v, i) => (
        <g key={`x${i}`}>
          <line x1={toX(v)} y1={PAD_T} x2={toX(v)} y2={PAD_T + ch} stroke={c.line} strokeOpacity="0.3" strokeWidth="1" />
          <text x={toX(v)} y={H - 8} textAnchor="middle" fill={c.faint} fontSize="8" fontFamily="var(--font-num)">
            {v >= 1000 ? `${(v / 1000).toFixed(0)}k` : v}
          </text>
        </g>
      ))}

      {halvingInterval > 0 && Array.from({ length: 5 }, (_, i) => (i + 1) * halvingInterval).filter(e => e <= maxEpoch).map((e, i) => (
        <line key={`h${i}`} x1={toX(e)} y1={PAD_T} x2={toX(e)} y2={PAD_T + ch} stroke={c.gold} strokeOpacity="0.3" strokeWidth="1" strokeDasharray="4,4" />
      ))}

      <path d={fillD} fill="url(#inflGrad)" />
      <path d={pathD} fill="none" stroke="url(#inflLine)" strokeWidth="2.5" strokeLinejoin="round" strokeLinecap="round" />

      {currentEpoch > 0 && currentEpoch <= maxEpoch && (
        <g>
          <line x1={markerX} y1={PAD_T} x2={markerX} y2={PAD_T + ch} stroke={c.accent} strokeOpacity="0.55" strokeWidth="1.5" strokeDasharray="4,4" />
          <text x={markerX} y={PAD_T - 6} textAnchor="middle" fill={c.accent} fontSize="9" fontFamily="var(--font-num)">
            epoch {currentEpoch}
          </text>
        </g>
      )}
    </svg>
  )
}

// ── Multiplier curve ────────────────────────────────────────────────────
// The lock-length → BlocTime-multiplier curve, with a marker on wherever the
// stake form currently sits. This is the one chart that answers the only
// question the page exists to ask: how much longer do I have to lock?

function MultiplierChart({ points, atBlocks, atMultiplier }: {
  points: MultiplierPoint[], atBlocks: number, atMultiplier: number
}) {
  const c = useThemeColors()
  const W = 600, H = 150, PAD_L = 34, PAD_R = 14, PAD_T = 16, PAD_B = 24
  const cw = W - PAD_L - PAD_R, ch = H - PAD_T - PAD_B

  const maxB = points[points.length - 1]?.blocks || 1
  const maxM = points[points.length - 1]?.multiplierX || 1
  const minM = points[0]?.multiplierX ?? 1
  const span = maxM - minM || 1

  const toX = (b: number) => PAD_L + (Math.min(b, maxB) / maxB) * cw
  const toY = (m: number) => PAD_T + ch - ((m - minM) / span) * ch

  const line = points.map((p, i) => `${i === 0 ? 'M' : 'L'}${toX(p.blocks).toFixed(1)},${toY(p.multiplierX).toFixed(1)}`).join(' ')
  const area = `${line} L${(PAD_L + cw).toFixed(1)},${(PAD_T + ch).toFixed(1)} L${PAD_L},${(PAD_T + ch).toFixed(1)} Z`

  const showMarker = atBlocks > 0
  const mx = toX(atBlocks)
  const my = toY(Math.min(Math.max(atMultiplier, minM), maxM))

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ maxHeight: 170 }} role="img"
         aria-label="BlocTime multiplier by lock length">
      <defs>
        <linearGradient id="multGrad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={c.accent} stopOpacity="0.3" />
          <stop offset="100%" stopColor={c.accent} stopOpacity="0" />
        </linearGradient>
      </defs>

      {[0, 0.5, 1].map(f => (
        <g key={f}>
          <line x1={PAD_L} y1={PAD_T + ch * f} x2={W - PAD_R} y2={PAD_T + ch * f}
                stroke={c.line} strokeOpacity="0.45" strokeWidth="1" />
          <text x={PAD_L - 6} y={PAD_T + ch * f + 3} textAnchor="end" fill={c.faint} fontSize="9"
                fontFamily="var(--font-num)">{(maxM - span * f).toFixed(1)}x</text>
        </g>
      ))}

      <path d={area} fill="url(#multGrad)" />
      <path d={line} fill="none" stroke={c.accent} strokeWidth="2.5" strokeLinejoin="round" strokeLinecap="round" />

      {points.map((p, i) => (
        <text key={i} x={toX(p.blocks)} y={H - 7} textAnchor={i === 0 ? 'start' : i === points.length - 1 ? 'end' : 'middle'}
              fill={c.faint} fontSize="8" fontFamily="var(--font-num)">
          {p.blocks >= 1000 ? `${(p.blocks / 1000).toFixed(0)}k` : p.blocks}
        </text>
      ))}

      {showMarker && (
        <g>
          <line x1={mx} y1={PAD_T} x2={mx} y2={PAD_T + ch} stroke={c.gold} strokeOpacity="0.5" strokeWidth="1" strokeDasharray="3,3" />
          <circle cx={mx} cy={my} r="4" fill={c.gold} stroke={c.panel} strokeWidth="2" />
          <text x={Math.min(mx + 8, W - PAD_R - 4)} y={Math.max(my - 8, PAD_T + 8)}
                textAnchor={mx > W * 0.75 ? 'end' : 'start'} fill={c.gold} fontSize="10" fontFamily="var(--font-num)">
            {atMultiplier.toFixed(2)}x
          </text>
        </g>
      )}
    </svg>
  )
}

// ── Contract Playground ─────────────────────────────────────────────────

// ethers v6 handles decimal strings for uints and 0x strings for
// address/bytes — only bools and arrays need explicit coercion.
function coerceForEthers(value: string, type: string): any {
  if (type.endsWith(']')) {
    const base = type.slice(0, type.lastIndexOf('['))
    return JSON.parse(value).map((v: any) => coerceForEthers(String(v), base))
  }
  if (type === 'bool') return ['1', 'true', 'yes'].includes(value.trim().toLowerCase())
  return value.trim()
}

// Explorer tx link for whatever chain the contract is on — derived from its
// own address link, so custom deployments don't all point at Basescan.
const txExplorer = (contract: ContractInfo, hash: string) =>
  contract.explorer ? `${contract.explorer.replace(/\/address\/.*$/, '')}/tx/${hash}` : ''

async function ensureChain(meta: { chainId: string; rpc: string }) {
  const w = window as any
  if (!w.ethereum || !meta.chainId) return
  const hexId = '0x' + Number(meta.chainId).toString(16)
  const current = await w.ethereum.request({ method: 'eth_chainId' })
  if (current === hexId) return
  try {
    await w.ethereum.request({ method: 'wallet_switchEthereumChain', params: [{ chainId: hexId }] })
  } catch (err: any) {
    if (err?.code === 4902) {
      const known = netFor(meta.chainId)
      await w.ethereum.request({
        method: 'wallet_addEthereumChain',
        params: [{
          chainId: hexId,
          chainName: known?.label || `Chain ${meta.chainId}`,
          nativeCurrency: {
            name: known?.symbol || 'ETH',
            symbol: known?.symbol || 'ETH',
            decimals: 18,
          },
          rpcUrls: [meta.rpc || known?.rpc].filter(Boolean),
        }],
      })
    } else throw err
  }
}

function AbiFunctionRow({ contract, fn, mode, writeVia, meta, connected }: {
  contract: ContractInfo
  fn: AbiEntry
  mode: 'read' | 'write'
  writeVia: 'wallet' | 'server'
  meta: ContractsMeta
  connected: boolean
}) {
  const [open, setOpen] = useState(false)
  const [args, setArgs] = useState<string[]>(() => (fn.inputs || []).map(() => ''))
  const [payValue, setPayValue] = useState('')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<any>(null)
  const [error, setError] = useState('')

  const inputs = fn.inputs || []
  const payable = fn.stateMutability === 'payable'

  const run = useCallback(async () => {
    setBusy(true); setError(''); setResult(null)
    try {
      if (mode === 'read') {
        const r = await api('contract/read', { contract: contract.id, fn: fn.name, args })
        setResult(r)
      } else if (writeVia === 'wallet') {
        const w = window as any
        if (!w.ethereum) throw new Error('Install MetaMask')
        // A contract you deployed may live on a different chain than the module's.
        await ensureChain({ chainId: contract.chainId || meta.chainId, rpc: contract.rpc || meta.rpc })
        const provider = new ethers.BrowserProvider(w.ethereum)
        const signer = await provider.getSigner()
        const c = new ethers.Contract(contract.address, contract.abi as any, signer)
        const coerced = inputs.map((inp, i) => coerceForEthers(args[i], inp.type))
        const overrides = payable && payValue ? [{ value: BigInt(payValue) }] : []
        const tx = await c[fn.name!](...coerced, ...overrides)
        const receipt = await tx.wait()
        setResult({
          success: receipt?.status === 1,
          tx_hash: tx.hash,
          block: receipt?.blockNumber,
          explorer: txExplorer(contract, tx.hash),
        })
        toast.success(`${fn.name} sent`)
      } else {
        const r = await api('contract/write', { contract: contract.id, fn: fn.name, args, value: payValue || '0' })
        setResult(r)
        toast.success(`${fn.name} sent`)
      }
    } catch (err: any) {
      const msg = err?.reason || err?.shortMessage || err?.message || 'Call failed'
      setError(msg)
    }
    setBusy(false)
  }, [mode, writeVia, contract, fn, args, payValue, meta, inputs, payable])

  const accent = mode === 'read'
    ? 'border-accent/40 bg-accent/15 text-accent hover:bg-accent/25'
    : 'border-gold/40 bg-gold/15 text-gold hover:bg-gold/25'

  return (
    <div className="border-b border-hair last:border-b-0">
      <button
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center justify-between px-4 py-2.5 hover:bg-panel transition-colors text-left"
      >
        <span className="text-xs font-mono text-ink2">
          {fn.name}
          <span className="text-faint">({inputs.map(i => i.type).join(', ')})</span>
        </span>
        <div className="flex items-center gap-2">
          {payable && <span className="text-[9px] uppercase tracking-wider text-down border border-down/20 rounded px-1.5 py-0.5">payable</span>}
          {(fn.outputs || []).length > 0 && mode === 'read' && (
            <span className="text-[9px] font-mono text-faint">→ {(fn.outputs || []).map(o => o.type).join(', ')}</span>
          )}
          {open ? <ChevronUpIcon className="w-3 h-3 text-mute" /> : <ChevronDownIcon className="w-3 h-3 text-mute" />}
        </div>
      </button>

      {open && (
        <div className="px-4 pb-4 space-y-2">
          {inputs.map((inp, i) => (
            <div key={i} className="flex items-center gap-2">
              <span className="text-[10px] font-mono text-mute w-36 shrink-0 truncate">
                {inp.name || `arg${i}`} <span className="text-faint">({inp.type})</span>
              </span>
              <input
                type="text"
                placeholder={inp.type.endsWith(']') ? '["a", "b"] (JSON array)' : inp.type}
                value={args[i]}
                onChange={e => setArgs(a => a.map((v, j) => j === i ? e.target.value : v))}
                className="input flex-1 text-xs px-3 py-2"
              />
            </div>
          ))}
          {mode === 'write' && payable && (
            <div className="flex items-center gap-2">
              <span className="text-[10px] font-mono text-down w-36 shrink-0">value <span className="text-faint">(wei)</span></span>
              <input
                type="text"
                placeholder="0"
                value={payValue}
                onChange={e => setPayValue(e.target.value)}
                className="input flex-1 text-xs px-3 py-2"
              />
            </div>
          )}

          <div className="flex items-center gap-3 pt-1">
            <button
              onClick={run}
              disabled={busy || (mode === 'write' && writeVia === 'wallet' && !connected)}
              className={`px-4 py-2 rounded-lg border text-[10px] font-bold uppercase tracking-wider disabled:opacity-30 disabled:cursor-not-allowed transition-colors flex items-center gap-1.5 ${accent}`}
            >
              {busy
                ? <ArrowPathIcon className="w-3 h-3 animate-spin" />
                : mode === 'read' ? <PlayIcon className="w-3 h-3" /> : <BoltIcon className="w-3 h-3" />}
              {mode === 'read' ? 'Query' : 'Write'}
            </button>
            {mode === 'write' && (
              <span className="lbl-dim">
                via {writeVia === 'wallet' ? (connected ? 'connected wallet' : 'wallet (connect first)') : `server signer${meta.signer ? ` ${fmtAddr(meta.signer)}` : ''}`}
              </span>
            )}
          </div>

          {error && (
            <p className="text-[11px] font-mono text-down break-all border border-down/20 bg-down/[0.05] rounded-lg p-3">{error}</p>
          )}
          {result !== null && (
            <div className="border border-up/15 bg-up/[0.03] rounded-lg p-3 space-y-1">
              <pre className="text-[11px] font-mono text-up whitespace-pre-wrap break-all">
                {JSON.stringify(result.output !== undefined ? result.output : result, null, 2)}
              </pre>
              {result.explorer && (
                <a href={result.explorer} target="_blank" rel="noreferrer"
                   className="inline-flex items-center gap-1 text-[10px] uppercase tracking-wider text-accent hover:underline">
                  <ArrowTopRightOnSquareIcon className="w-3 h-3" /> View tx
                </a>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function ContractsPlayground({ meta, connected }: { meta: ContractsMeta, connected: boolean }) {
  const [selected, setSelected] = useState(0)
  const [view, setView] = useState<'read' | 'write' | 'abi' | 'source'>('read')
  const [writeVia, setWriteVia] = useState<'wallet' | 'server'>(connected ? 'wallet' : 'server')

  const contract = meta.contracts[selected]
  if (!contract) return null

  const fns = contract.abi.filter(e => e.type === 'function')
  const readFns = fns.filter(e => e.stateMutability === 'view' || e.stateMutability === 'pure')
  const writeFns = fns.filter(e => e.stateMutability !== 'view' && e.stateMutability !== 'pure')
  const events = contract.abi.filter(e => e.type === 'event')

  const copy = (text: string, label: string) => {
    navigator.clipboard.writeText(text)
    toast.success(`${label} copied`)
  }

  return (
    <>
      {/* Contract selector + address */}
      <div className="border border-line rounded-lg bg-panel p-4 space-y-3">
        <div className="flex items-center gap-2 flex-wrap">
          {meta.contracts.map((c, i) => (
            <button
              key={c.id}
              onClick={() => setSelected(i)}
              className={`px-4 py-2 rounded-lg border text-[10px] font-bold uppercase tracking-wider transition-colors
                ${i === selected
                  ? 'border-accent/40 bg-accent/15 text-accent'
                  : 'border-line bg-field text-mute hover:text-ink2 hover:bg-fieldhi'}`}
            >
              {c.name}
            </button>
          ))}
          <span className="ml-auto text-[10px] uppercase tracking-wider text-iris">
            {contract.deployed ? 'deployed here' : meta.network} · chain {contract.chainId || meta.chainId}
          </span>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-xs font-mono text-ink2 break-all">{contract.address}</span>
          <button onClick={() => copy(contract.address, 'Address')} className="p-1 rounded text-mute hover:text-ink2 transition-colors" title="Copy address">
            <DocumentDuplicateIcon className="w-3.5 h-3.5" />
          </button>
          {contract.explorer && (
            <a href={contract.explorer} target="_blank" rel="noreferrer" className="p-1 rounded text-mute hover:text-accent transition-colors" title="View on Basescan">
              <ArrowTopRightOnSquareIcon className="w-3.5 h-3.5" />
            </a>
          )}
        </div>
      </div>

      {/* View switch */}
      <div className="flex gap-1 p-1 rounded-lg border border-line bg-panel">
        {([
          ['read', `Read (${readFns.length})`],
          ['write', `Write (${writeFns.length})`],
          ['abi', 'ABI'],
          ['source', 'Source'],
        ] as [typeof view, string][]).map(([v, label]) => (
          <button
            key={v}
            onClick={() => setView(v)}
            className={`flex-1 py-2 rounded-md text-[10px] font-bold uppercase tracking-wider transition-all
              ${view === v ? 'bg-panel2 text-ink' : 'text-mute hover:text-ink2 hover:bg-panel'}`}
          >
            {label}
          </button>
        ))}
      </div>

      {(view === 'read' || view === 'write') && (
        <div className="card overflow-hidden">
          <div className="flex items-center justify-between px-4 py-3 border-b border-hair">
            <span className="lbl">
              {view === 'read' ? 'Read Functions' : 'Write Functions'}
            </span>
            {view === 'write' && (
              <div className="flex gap-1 p-0.5 rounded-lg border border-line bg-panel">
                {(['wallet', 'server'] as const).map(v => (
                  <button
                    key={v}
                    onClick={() => setWriteVia(v)}
                    className={`px-3 py-1 rounded-md text-[9px] font-bold uppercase tracking-wider transition-all
                      ${writeVia === v ? 'bg-panel2 text-ink' : 'text-mute hover:text-ink2'}`}
                  >
                    {v}
                  </button>
                ))}
              </div>
            )}
          </div>
          {(view === 'read' ? readFns : writeFns).map((fn, i) => (
            <AbiFunctionRow
              key={`${contract.id}-${fn.name}-${i}`}
              contract={contract}
              fn={fn}
              mode={view}
              writeVia={writeVia}
              meta={meta}
              connected={connected}
            />
          ))}
        </div>
      )}

      {view === 'abi' && (
        <div className="card overflow-hidden">
          <div className="flex items-center justify-between px-4 py-3 border-b border-hair">
            <span className="lbl">
              ABI · {fns.length} functions · {events.length} events
            </span>
            <button
              onClick={() => copy(JSON.stringify(contract.abi, null, 2), 'ABI')}
              className="btn btn-sm"
            >
              <DocumentDuplicateIcon className="w-3 h-3" /> Copy ABI
            </button>
          </div>
          <pre className="p-4 text-[11px] font-mono text-ink2 overflow-x-auto max-h-[500px] overflow-y-auto whitespace-pre">
            {JSON.stringify(contract.abi, null, 2)}
          </pre>
        </div>
      )}

      {view === 'source' && (
        <div className="card overflow-hidden">
          <div className="flex items-center justify-between px-4 py-3 border-b border-hair">
            <span className="lbl">{contract.name}.sol</span>
            <button
              onClick={() => copy(contract.source, 'Source')}
              className="btn btn-sm"
            >
              <DocumentDuplicateIcon className="w-3 h-3" /> Copy
            </button>
          </div>
          <pre className="p-4 text-[11px] font-mono text-ink2 overflow-x-auto max-h-[600px] overflow-y-auto whitespace-pre">
            {contract.source || '// source not found'}
          </pre>
        </div>
      )}
    </>
  )
}

// ── Marketplace ─────────────────────────────────────────────────────────

function MarketPanel({ instances, activeId, account, loading, onUse, onRefresh }: {
  instances: Instance[]
  activeId: string
  account: string
  loading: boolean
  onUse: (inst: Instance) => void
  onRefresh: () => void
}) {
  const [removing, setRemoving] = useState('')

  const handleRemove = useCallback(async (inst: Instance) => {
    const w = window as any
    if (!w.ethereum) { toast.error('Install MetaMask'); return }
    setRemoving(inst.id)
    try {
      const provider = new ethers.BrowserProvider(w.ethereum)
      const signer = await provider.getSigner()
      const signature = await signer.signMessage(`bloctime:unregister:${inst.id}`)
      await api('registry/unregister', { id: inst.id, address: await signer.getAddress(), signature })
      toast.success(`${inst.name} removed from market`)
      onRefresh()
    } catch (err: any) {
      toast.error(err?.message || 'Remove failed')
    }
    setRemoving('')
  }, [onRefresh])

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between border border-line rounded-lg p-4 bg-panel">
        <div className="flex items-center gap-2">
          <BuildingStorefrontIcon className="w-4 h-4 text-mute" />
          <span className="lbl">
            BlocTime Market · {instances.length} deployed instance{instances.length !== 1 ? 's' : ''}
          </span>
        </div>
        <button
          onClick={onRefresh}
          disabled={loading}
          className="p-1.5 rounded-lg border border-line bg-field text-mute hover:text-ink2 hover:bg-fieldhi disabled:opacity-30 transition-colors"
        >
          <ArrowPathIcon className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
        </button>
      </div>

      {instances.length === 0 && (
        <div className="border border-line rounded-lg bg-panel py-16 text-center">
          <span className="text-xs text-mute uppercase tracking-wider">
            {loading ? 'Loading market...' : 'No instances registered yet — deploy one from the DEPLOY tab'}
          </span>
        </div>
      )}

      <div className="grid md:grid-cols-2 gap-4">
        {instances.map(inst => {
          const active = inst.id === activeId
          const mine = account && inst.owner && account.toLowerCase() === inst.owner.toLowerCase()
          return (
            <div key={inst.id} className={`border rounded-lg p-4 bg-panel space-y-3 transition-colors ${active ? 'border-accent/40' : 'border-line'}`}>
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <h3 className="text-sm font-bold text-ink truncate">{inst.name}</h3>
                    {inst.official && (
                      <span className="text-[9px] uppercase tracking-wider text-gold border border-gold/30 bg-gold/10 rounded px-1.5 py-0.5">official</span>
                    )}
                    {mine && (
                      <span className="text-[9px] uppercase tracking-wider text-up border border-up/30 bg-up/10 rounded px-1.5 py-0.5">yours</span>
                    )}
                  </div>
                  {inst.description && <p className="text-[11px] text-mute mt-1 line-clamp-2">{inst.description}</p>}
                </div>
                {mine && !inst.official && (
                  <button
                    onClick={() => handleRemove(inst)}
                    disabled={removing === inst.id}
                    className="p-1 rounded text-faint hover:text-down disabled:opacity-30 transition-colors shrink-0"
                    title="Remove from market (signs with your wallet)"
                  >
                    {removing === inst.id ? <ArrowPathIcon className="w-3.5 h-3.5 animate-spin" /> : <XMarkIcon className="w-3.5 h-3.5" />}
                  </button>
                )}
              </div>

              <div className="grid grid-cols-3 border border-hair rounded-lg overflow-hidden">
                <div className="p-2 text-center border-r border-hair">
                  <p className="text-sm font-bold text-accent tabular-nums">{inst.stats ? inst.stats.totalStakes : '--'}</p>
                  <p className="lbl-dim">Stakes</p>
                </div>
                <div className="p-2 text-center border-r border-hair">
                  <p className="text-sm font-bold text-gold tabular-nums">{inst.stats ? fmtEth(inst.stats.totalBlocTime) : '--'}</p>
                  <p className="lbl-dim">BlocTime</p>
                </div>
                <div className="p-2 text-center">
                  <p className="text-sm font-bold text-up tabular-nums">{inst.stats ? fmtEth(inst.stats.totalSupply) : '--'}</p>
                  <p className="lbl-dim">BT Supply</p>
                </div>
              </div>

              <div className="space-y-1 text-[10px] font-mono text-mute">
                <p>chain <span className="text-iris">{inst.chainId || '?'}</span> · contract <span className="text-ink2">{fmtAddr(inst.bloctime)}</span></p>
                {inst.owner && <p>owner <span className="text-ink2">{fmtAddr(inst.owner)}</span></p>}
              </div>

              <div className="flex items-center gap-2">
                <button
                  onClick={() => onUse(inst)}
                  disabled={active}
                  // The active instance is disabled but not greyed — it's the
                  // current state, not an unavailable action.
                  className={`btn flex-1 ${active ? 'btn-accent cursor-default disabled:opacity-100' : ''}`}
                >
                  {active ? <><CheckCircleIcon className="w-3.5 h-3.5" /> Active</> : 'Use'}
                </button>
                {inst.explorer && (
                  <a href={inst.explorer} target="_blank" rel="noreferrer"
                     className="p-2 rounded-lg border border-line bg-field text-mute hover:text-accent transition-colors" title="View on explorer">
                    <ArrowTopRightOnSquareIcon className="w-3.5 h-3.5" />
                  </a>
                )}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ── Deploy your own ─────────────────────────────────────────────────────

type StepState = 'pending' | 'active' | 'done' | 'error'

function DeployPanel({ connected, chainId, getFactory, onDeployed }: {
  connected: boolean
  chainId: string
  getFactory: () => Promise<FactoryKit>
  onDeployed: (entry: Instance) => void
}) {
  const known = netFor(chainId)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [supply, setSupply] = useState('1000000')
  const [maxLock, setMaxLock] = useState('100000')
  const [distPct, setDistPct] = useState('5000')
  const [rpc, setRpc] = useState(known?.rpc || '')
  const [busy, setBusy] = useState(false)
  const [forkCmd, setForkCmd] = useState('m bloctime/fork name=<yourname>')
  const [steps, setSteps] = useState<{ label: string; state: StepState }[]>([])

  useEffect(() => {
    getFactory().then(kit => {
      if (kit.fork) setForkCmd(kit.fork)
      setSupply(kit.defaults.initialSupply)
      setMaxLock(String(kit.defaults.maxLockBlocks))
      setDistPct(String(kit.defaults.distributionPercentage))
    }).catch(() => {})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Network comes from the header picker — follow it, but let the user type
  // an RPC when the wallet sits on a chain we don't know a public one for.
  useEffect(() => { setRpc(netFor(chainId)?.rpc || '') }, [chainId])

  const setStep = (i: number, state: StepState) =>
    setSteps(s => s.map((st, j) => j === i ? { ...st, state } : st))

  const handleDeploy = useCallback(async () => {
    if (!name.trim()) { toast.error('Name your instance'); return }
    if (!rpc.trim()) { toast.error('Set a public RPC URL for this network'); return }
    const w = window as any
    if (!w.ethereum) { toast.error('Install MetaMask'); return }
    setBusy(true)
    const labels = [
      'Switch network', 'Deploy NativeToken', 'Deploy BlocTime',
      'Set multiplier curve', 'Set inflation params', 'Register on market',
    ]
    setSteps(labels.map((label, i) => ({ label, state: i === 0 ? 'active' : 'pending' })))
    let step = 0
    try {
      const kit = await getFactory()
      await ensureChain({ chainId, rpc })
      const provider = new ethers.BrowserProvider(w.ethereum)
      const signer = await provider.getSigner()
      setStep(step, 'done'); step = 1; setStep(step, 'active')

      const tokenFactory = new ethers.ContractFactory(
        kit.contracts.nativeToken.abi as any, kit.contracts.nativeToken.bytecode, signer)
      const token = await tokenFactory.deploy(ethers.parseEther(supply || '1000000'))
      await token.waitForDeployment()
      const tokenAddr = await token.getAddress()
      setStep(step, 'done'); step = 2; setStep(step, 'active')

      const btFactory = new ethers.ContractFactory(
        kit.contracts.bloctime.abi as any, kit.contracts.bloctime.bytecode, signer)
      const maxLockN = BigInt(parseInt(maxLock) || 100000)
      const bt = await btFactory.deploy(tokenAddr, maxLockN, BigInt(parseInt(distPct) || 5000))
      await bt.waitForDeployment()
      const btAddr = await bt.getAddress()
      setStep(step, 'done'); step = 3; setStep(step, 'active')

      // Contract rejects points beyond maxLockBlocks.
      const points = kit.defaults.points.filter(p => BigInt(p.blocks) <= maxLockN)
      const btWrite = bt as unknown as ethers.Contract
      await (await btWrite.setPoints(points.map(p => ({ blocks: BigInt(p.blocks), multiplier: BigInt(p.multiplier) })))).wait()
      setStep(step, 'done'); step = 4; setStep(step, 'active')

      const infl = kit.defaults.inflation
      await (await btWrite.setInflationParams(
        ethers.parseEther(infl.initialRewardPerEpoch),
        BigInt(infl.halvingInterval),
        ethers.parseEther(infl.minRewardPerEpoch || '0'),
        BigInt(infl.epochLength),
      )).wait()
      setStep(step, 'done'); step = 5; setStep(step, 'active')

      const entry: Instance = await api('registry/register', {
        name: name.trim(), description: description.trim(),
        rpc, bloctime: btAddr, nativeToken: tokenAddr,
      })
      setStep(step, 'done')
      toast.success(`${entry.name} deployed and listed on the market`)
      onDeployed(entry)
    } catch (err: any) {
      setStep(step, 'error')
      toast.error(err?.reason || err?.shortMessage || err?.message || 'Deploy failed')
    }
    setBusy(false)
  }, [name, description, supply, maxLock, distPct, rpc, chainId, getFactory, onDeployed])

  const input = "w-full text-sm px-4 py-2.5 rounded-lg border border-line bg-field text-ink focus:outline-none focus:border-line2 font-mono transition-colors placeholder:text-faint"

  return (
    <div className="space-y-4">
      {/* Wallet deploy */}
      <div className="border border-line rounded-lg p-4 bg-panel space-y-3">
        <div className="flex items-center gap-2">
          <RocketLaunchIcon className="w-4 h-4 text-mute" />
          <span className="lbl">Deploy your own BlocTime</span>
        </div>
        <p className="text-[11px] text-mute">
          Deploys a fresh NativeToken + BlocTime pair from <span className="text-ink2">your wallet</span> —
          you pay gas, you own the contracts. It is then listed on the market for everyone to browse and stake.
        </p>

        <div className="grid md:grid-cols-2 gap-3">
          <input type="text" placeholder="Instance name" value={name} onChange={e => setName(e.target.value)} className={input} />
          <input type="text" placeholder="Description (optional)" value={description} onChange={e => setDescription(e.target.value)} className={input} />
          <div>
            <p className="lbl-dim mb-1">Token supply (NTV)</p>
            <input type="number" value={supply} onChange={e => setSupply(e.target.value)} className={input} />
          </div>
          <div>
            <p className="lbl-dim mb-1">Max lock (blocks)</p>
            <input type="number" value={maxLock} onChange={e => setMaxLock(e.target.value)} className={input} />
          </div>
          <div>
            <p className="lbl-dim mb-1">Distribution % (bps, 5000 = 50%)</p>
            <input type="number" value={distPct} onChange={e => setDistPct(e.target.value)} className={input} />
          </div>
          <div>
            <p className="lbl-dim mb-1">Network</p>
            <div className="flex items-center gap-2 h-[42px] px-4 rounded-lg border border-hair bg-panel">
              <span className={`w-1.5 h-1.5 rounded-full ${known ? 'bg-up' : 'bg-gold'}`} />
              <span className="text-sm text-ink2">{netLabel(chainId)}</span>
              <span className="lbl-dim ml-auto">pick it up top</span>
            </div>
          </div>
          {!known && (
            <div className="md:col-span-2">
              <p className="lbl-dim mb-1">Public RPC URL of that network (used to verify + read stats)</p>
              <input type="text" value={rpc} onChange={e => setRpc(e.target.value)} className={input} placeholder="https://..." />
            </div>
          )}
        </div>

        <button
          onClick={handleDeploy}
          disabled={busy || !connected || !name.trim()}
          className="btn btn-accent w-full py-3"
        >
          {busy ? <><ArrowPathIcon className="w-3.5 h-3.5 animate-spin" /> Deploying...</> : <><RocketLaunchIcon className="w-3.5 h-3.5" /> Deploy with wallet</>}
        </button>
        {!connected && <p className="text-[10px] text-gold text-center">Connect your wallet first</p>}

        {steps.length > 0 && (
          <div className="border border-hair rounded-lg p-3 space-y-1.5">
            {steps.map((s, i) => (
              <div key={i} className="flex items-center gap-2 text-[11px] font-mono">
                {s.state === 'done' && <CheckCircleIcon className="w-3.5 h-3.5 text-up" />}
                {s.state === 'active' && <ArrowPathIcon className="w-3.5 h-3.5 text-accent animate-spin" />}
                {s.state === 'error' && <XMarkIcon className="w-3.5 h-3.5 text-down" />}
                {s.state === 'pending' && <div className="w-3.5 h-3.5 flex items-center justify-center"><div className="w-1.5 h-1.5 rounded-full bg-fieldhi" /></div>}
                <span className={s.state === 'pending' ? 'text-faint' : s.state === 'error' ? 'text-down' : 'text-ink2'}>{s.label}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Fork the module */}
      <div className="border border-line rounded-lg p-4 bg-panel space-y-2">
        <div className="flex items-center gap-2">
          <CodeBracketIcon className="w-4 h-4 text-mute" />
          <span className="lbl">Fork the whole module</span>
        </div>
        <p className="text-[11px] text-mute">
          Want your own console, API and contracts under your own name? Fork the module itself —
          it copies everything into a sibling module with its own ports and route, ready to deploy and serve.
        </p>
        <pre className="text-[11px] font-mono text-accent bg-panel2 border border-hair rounded-md p-3 overflow-x-auto">{forkCmd}</pre>
        <p className="text-[10px] text-faint font-mono">then: m &lt;yourname&gt;/serve · deploy from its DEPLOY tab · m bloctime/register_instance to list it here</p>
      </div>
    </div>
  )
}

// ── Deploy any contract ─────────────────────────────────────────────────

function BuildPanel({ connected, chainId, onDeployed }: {
  connected: boolean
  chainId: string
  onDeployed: () => void
}) {
  const [source, setSource] = useState('')
  const [filename, setFilename] = useState('Contract.sol')
  const [compiling, setCompiling] = useState(false)
  const [compiled, setCompiled] = useState<Compiled | null>(null)
  const [error, setError] = useState('')
  const [picked, setPicked] = useState(0)
  const [args, setArgs] = useState<string[]>([])
  const [deploying, setDeploying] = useState(false)
  const [result, setResult] = useState<{ name: string; address: string; txHash: string } | null>(null)
  const [rpc, setRpc] = useState('')
  const [deployments, setDeployments] = useState<Deployment[]>([])

  const known = netFor(chainId)

  useEffect(() => {
    api('compile/starter', {}, 'GET')
      .then(d => { setSource(s => s || d.source); setFilename(f => d.filename || f) })
      .catch(() => {})
    api('deployments', {}, 'GET')
      .then(d => setDeployments(d.deployments))
      .catch(() => {})
  }, [])

  useEffect(() => { setRpc(netFor(chainId)?.rpc || '') }, [chainId])

  const contract = compiled ? compiled.contracts[picked] : null

  const handleCompile = useCallback(async () => {
    setCompiling(true); setError(''); setResult(null)
    try {
      const out: Compiled = await api('compile', { source, filename })
      setCompiled(out)
      const first = out.contracts.findIndex(c => c.deployable)
      const idx = first >= 0 ? first : 0
      setPicked(idx)
      setArgs((out.contracts[idx]?.constructor || []).map(() => ''))
      toast.success(`Compiled ${out.contracts.length} contract${out.contracts.length !== 1 ? 's' : ''}`)
    } catch (err: any) {
      setCompiled(null)
      setError(err?.message || 'Compile failed')
    }
    setCompiling(false)
  }, [source, filename])

  const pick = useCallback((i: number) => {
    setPicked(i); setResult(null)
    setArgs((compiled?.contracts[i]?.constructor || []).map(() => ''))
  }, [compiled])

  const handleDeploy = useCallback(async () => {
    if (!contract) return
    if (!rpc.trim()) { toast.error('Set a public RPC URL for this network'); return }
    const w = window as any
    if (!w.ethereum) { toast.error('Install MetaMask'); return }
    setDeploying(true); setError('')
    try {
      await ensureChain({ chainId, rpc })
      const provider = new ethers.BrowserProvider(w.ethereum)
      const signer = await provider.getSigner()
      const factory = new ethers.ContractFactory(contract.abi as any, contract.bytecode, signer)
      const ctorArgs = (contract.constructor || []).map((inp, i) => coerceForEthers(args[i] || '', inp.type))
      const deployed = await factory.deploy(...ctorArgs)
      const txHash = deployed.deploymentTransaction()?.hash || ''
      await deployed.waitForDeployment()
      const address = await deployed.getAddress()
      setResult({ name: contract.name, address, txHash })
      toast.success(`${contract.name} deployed at ${fmtAddr(address)}`)

      // Remembered server-side so it joins the CONTRACTS playground.
      try {
        const entry: Deployment = await api('deployments/record', {
          name: contract.name, address, abi: contract.abi, rpc, chainId,
          deployer: await signer.getAddress(), txHash,
          source, filename, solc: compiled?.solc || '',
        })
        setDeployments(d => [...d, entry])
        onDeployed()
      } catch (err: any) {
        toast.error(`Deployed, but not saved: ${err?.message || 'record failed'}`)
      }
    } catch (err: any) {
      setError(err?.reason || err?.shortMessage || err?.message || 'Deploy failed')
    }
    setDeploying(false)
  }, [contract, args, chainId, rpc, source, filename, compiled, onDeployed])

  const handleForget = useCallback(async (entry: Deployment) => {
    try {
      let signature = ''
      if (entry.deployer) {
        const w = window as any
        if (!w.ethereum) throw new Error('Install MetaMask to prove you deployed it')
        const signer = await new ethers.BrowserProvider(w.ethereum).getSigner()
        signature = await signer.signMessage(`bloctime:forget:${entry.id}`)
      }
      await api('deployments/forget', { id: entry.id, address: entry.deployer, signature })
      setDeployments(d => d.filter(e => e.id !== entry.id))
      onDeployed()
      toast.success(`${entry.name} removed`)
    } catch (err: any) {
      toast.error(err?.shortMessage || err?.message || 'Could not remove')
    }
  }, [onDeployed])

  const input = "w-full text-sm px-4 py-2.5 rounded-lg border border-line bg-field text-ink focus:outline-none focus:border-line2 font-mono transition-colors placeholder:text-faint"

  return (
    <div className="space-y-4">
      <div className="border border-line rounded-lg p-4 bg-panel space-y-3">
        <div className="flex items-center gap-2">
          <CodeBracketIcon className="w-4 h-4 text-mute" />
          <span className="lbl">Deploy any contract</span>
          <span className="ml-auto text-[10px] uppercase tracking-wider text-iris">{netLabel(chainId)}</span>
        </div>
        <p className="text-[11px] text-mute">
          Write Solidity, compile it here, and deploy from <span className="text-ink2">your wallet</span>.
          Imports resolve against this module — <span className="font-mono text-ink2">@openzeppelin/contracts/**</span> works.
          Anything you deploy joins the CONTRACTS tab for reads and writes.
        </p>

        <textarea
          value={source}
          onChange={e => setSource(e.target.value)}
          spellCheck={false}
          rows={16}
          className={`${input} resize-y leading-relaxed text-[12px]`}
          placeholder="// SPDX-License-Identifier: MIT&#10;pragma solidity ^0.8.20;&#10;&#10;contract MyContract { }"
        />

        <div className="grid md:grid-cols-2 gap-3">
          <div>
            <p className="lbl-dim mb-1">File name</p>
            <input type="text" value={filename} onChange={e => setFilename(e.target.value)} className={input} />
          </div>
          {!known && (
            <div>
              <p className="lbl-dim mb-1">Public RPC URL (used to verify the deployment)</p>
              <input type="text" value={rpc} onChange={e => setRpc(e.target.value)} className={input} placeholder="https://..." />
            </div>
          )}
        </div>

        <button
          onClick={handleCompile}
          disabled={compiling || !source.trim()}
          className="btn w-full"
        >
          {compiling ? <><ArrowPathIcon className="w-3.5 h-3.5 animate-spin" /> Compiling...</> : <><BoltIcon className="w-3.5 h-3.5" /> Compile</>}
        </button>

        {error && (
          <pre className="text-[11px] font-mono text-down whitespace-pre-wrap break-all border border-down/20 bg-down/[0.05] rounded-lg p-3 max-h-56 overflow-y-auto">{error}</pre>
        )}

        {compiled && (
          <div className="space-y-3 border-t border-hair pt-3">
            <div className="flex items-center gap-2 flex-wrap">
              {compiled.contracts.map((c, i) => (
                <button
                  key={c.name}
                  onClick={() => pick(i)}
                  disabled={!c.deployable}
                  title={c.deployable ? '' : 'Interface or abstract contract — nothing to deploy'}
                  className={`px-4 py-2 rounded-lg border text-[10px] font-bold uppercase tracking-wider transition-colors disabled:opacity-25 disabled:cursor-not-allowed
                    ${i === picked
                      ? 'border-accent/40 bg-accent/15 text-accent'
                      : 'border-line bg-field text-mute hover:text-ink2 hover:bg-fieldhi'}`}
                >
                  {c.name}
                </button>
              ))}
              <span className="ml-auto text-[10px] font-mono text-faint">solc {compiled.solc.split('+')[0]}</span>
            </div>

            {compiled.warnings.length > 0 && (
              <pre className="text-[10px] font-mono text-gold whitespace-pre-wrap break-all border border-gold/15 bg-gold/[0.03] rounded-lg p-3 max-h-40 overflow-y-auto">
                {compiled.warnings.join('\n')}
              </pre>
            )}

            {contract && (contract.constructor || []).map((inp, i) => (
              <div key={i} className="flex items-center gap-2">
                <span className="text-[10px] font-mono text-mute w-36 shrink-0 truncate">
                  {inp.name || `arg${i}`} <span className="text-faint">({inp.type})</span>
                </span>
                <input
                  type="text"
                  placeholder={inp.type.endsWith(']') ? '["a", "b"] (JSON array)' : inp.type}
                  value={args[i] || ''}
                  onChange={e => setArgs(a => a.map((v, j) => j === i ? e.target.value : v))}
                  className="input flex-1 text-xs px-3 py-2"
                />
              </div>
            ))}

            <button
              onClick={handleDeploy}
              disabled={deploying || !connected || !contract?.deployable}
              className="btn btn-accent w-full py-3"
            >
              {deploying
                ? <><ArrowPathIcon className="w-3.5 h-3.5 animate-spin" /> Deploying...</>
                : <><RocketLaunchIcon className="w-3.5 h-3.5" /> Deploy {contract ? contract.name : ''} with wallet</>}
            </button>
            {!connected && <p className="text-[10px] text-gold text-center">Connect your wallet first</p>}

            {result && (
              <div className="border border-up/15 bg-up/[0.03] rounded-lg p-3 space-y-1">
                <p className="text-[11px] font-mono text-up break-all">{result.name} → {result.address}</p>
                <p className="text-[10px] text-mute">Open the CONTRACTS tab to call it.</p>
              </div>
            )}
          </div>
        )}
      </div>

      {deployments.length > 0 && (
        <div className="card overflow-hidden">
          <div className="px-4 py-3 border-b border-hair">
            <span className="lbl">
              Deployed here · {deployments.length}
            </span>
          </div>
          {deployments.map(d => (
            <div key={d.id} className="flex items-center gap-3 px-4 py-2.5 border-b border-hair last:border-b-0">
              <span className="text-xs font-mono text-ink2 w-40 shrink-0 truncate">{d.name}</span>
              <span className="text-[11px] font-mono text-mute truncate">{fmtAddr(d.address)}</span>
              <span className="text-[10px] uppercase tracking-wider text-iris shrink-0">chain {d.chainId || '?'}</span>
              <div className="ml-auto flex items-center gap-1 shrink-0">
                {d.explorer && (
                  <a href={d.explorer} target="_blank" rel="noreferrer" className="p-1 rounded text-mute hover:text-accent transition-colors" title="View on explorer">
                    <ArrowTopRightOnSquareIcon className="w-3.5 h-3.5" />
                  </a>
                )}
                <button onClick={() => handleForget(d)} className="p-1 rounded text-mute hover:text-down transition-colors" title="Forget this deployment">
                  <XMarkIcon className="w-3.5 h-3.5" />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Bridge ──────────────────────────────────────────────────────────────

function BridgePanel({ account, connected }: { account: string; connected: boolean }) {
  const [info, setInfo] = useState<BridgeInfo | null>(null)
  const [loading, setLoading] = useState(true)
  const [srcAddr, setSrcAddr] = useState('')
  const [checking, setChecking] = useState(false)
  const [checkResult, setCheckResult] = useState<Record<string, any> | null>(null)

  useEffect(() => {
    api('bridge/info', {}, 'GET')
      .then(d => setInfo(d))
      .catch(() => setInfo(null))
      .finally(() => setLoading(false))
  }, [])

  const handleCheck = useCallback(async () => {
    const addr = srcAddr.trim() || account
    if (!addr) { toast.error('Enter a Substrate/Solana address'); return }
    setChecking(true); setCheckResult(null)
    try {
      const snap = await api('bridge/in_snapshot', { address: addr })
      let claimed: any = null
      try { claimed = await api('bridge/has_claimed', { address: addr }) } catch {}
      setCheckResult({ address: addr, ...snap, ...(claimed || {}) })
    } catch (err: any) {
      toast.error(err?.message || 'Snapshot check failed')
    }
    setChecking(false)
  }, [srcAddr, account])

  const totals = info?.totals || {}

  return (
    <div className="space-y-4">
      <div className="border border-line rounded-lg p-4 bg-panel space-y-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <ArrowsRightLeftIcon className="w-4 h-4 text-mute" />
            <span className="lbl">Bridge into BlocTime</span>
          </div>
          <span className={`text-[9px] uppercase tracking-wider border rounded px-1.5 py-0.5 ${
            loading ? 'text-mute border-line'
              : info?.online ? 'text-up border-up/30 bg-up/10'
              : 'text-down border-down/30 bg-down/10'}`}>
            {loading ? 'checking...' : info?.online ? 'bridge online' : 'bridge offline'}
          </span>
        </div>
        <p className="text-[11px] text-mute">
          The bridge module moves Substrate/Solana snapshot balances onto Base as bridgeable tokens.
          Bring your balance over, then stake it in any BlocTime instance on the market.
        </p>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-center">
          {[
            ['Snapshot addresses', info?.health?.snapshot_addresses],
            ['Committed', totals.total_committed != null ? Number(totals.total_committed).toLocaleString(undefined, { maximumFractionDigits: 0 }) : undefined],
            ['Unclaimed', totals.total_unclaimed != null ? Number(totals.total_unclaimed).toLocaleString(undefined, { maximumFractionDigits: 0 }) : undefined],
            ['Claims', info?.health?.claims],
          ].map(([label, value], i) => (
            <div key={i} className="border border-hair rounded-lg p-2.5">
              <p className="text-sm font-bold text-accent tabular-nums">{value ?? '--'}</p>
              <p className="lbl-dim mt-0.5">{label}</p>
            </div>
          ))}
        </div>
      </div>

      {/* Flow */}
      <div className="card p-4">
        <p className="lbl mb-3">How it works</p>
        <div className="grid md:grid-cols-4 gap-2">
          {[
            ['1 · Check', 'Look your Substrate/Solana address up in the snapshot below'],
            ['2 · Commit', 'In the bridge app, sign with SubWallet/Phantom to link your EVM address'],
            ['3 · Claim', 'Claim your bridged tokens on Base'],
            ['4 · Stake', 'Come back and stake them in any market instance'],
          ].map(([title, body], i) => (
            <div key={i} className="border border-hair rounded-lg p-3">
              <p className="text-[10px] font-bold uppercase tracking-wider text-accent mb-1">{title}</p>
              <p className="text-[10px] text-mute leading-relaxed">{body}</p>
            </div>
          ))}
        </div>
      </div>

      {/* Snapshot checker */}
      <div className="border border-line rounded-lg p-4 bg-panel space-y-3">
        <p className="lbl">Check snapshot balance</p>
        <div className="flex gap-2 flex-wrap">
          <input
            type="text"
            placeholder="Substrate / Solana address"
            value={srcAddr}
            onChange={e => setSrcAddr(e.target.value)}
            className="input flex-1 min-w-[220px]"
          />
          <button
            onClick={handleCheck}
            disabled={checking || !info?.online}
            className="btn btn-accent"
          >
            {checking ? <ArrowPathIcon className="w-3.5 h-3.5 animate-spin" /> : 'Check'}
          </button>
        </div>
        {checkResult && (
          <pre className="text-[11px] font-mono text-up whitespace-pre-wrap break-all border border-up/15 bg-up/[0.03] rounded-lg p-3">
            {JSON.stringify(checkResult, null, 2)}
          </pre>
        )}
      </div>

      <a
        href={BRIDGE_APP_URL}
        target="_blank"
        rel="noreferrer"
        className="btn w-full py-3 border-iris/40 bg-iris/15 text-iris hover:bg-iris/25"
      >
        Open the Bridge App → commit & claim
      </a>
    </div>
  )
}

// ── Main Page ───────────────────────────────────────────────────────────

// ── Network picker (header) ─────────────────────────────────────────────

function NetworkPicker({ chainId, onSelect }: {
  chainId: string
  onSelect: (net: NetworkDef) => void
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const known = netFor(chainId)

  useEffect(() => {
    if (!open) return
    const close = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false) }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [open])

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen(o => !o)}
        className="btn btn-sm px-2.5 gap-2"
        aria-haspopup="menu"
        aria-expanded={open}
      >
        <span className={`chip-dot ${known ? 'bg-up' : 'bg-gold animate-pulse-dot'}`} />
        {/* The dot alone carries the network on a phone — the label is the
            first thing to go when the header runs out of room. */}
        <span className="normal-case tracking-normal hidden sm:inline">{netLabel(chainId)}</span>
        <ChevronDownIcon className={`w-3 h-3 transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>
      {open && (
        <div className="menu right-0 mt-2 w-60" role="menu">
          <p className="lbl-dim px-2.5 pt-1.5 pb-2">Network</p>
          {NETWORKS.map(net => (
            <button
              key={net.chainId}
              role="menuitemradio"
              aria-checked={net.chainId === chainId}
              onClick={() => { setOpen(false); onSelect(net) }}
              className="menu-item"
            >
              <span className="flex-1">{net.label}</span>
              <span className="font-mono text-faint">{net.chainId}</span>
            </button>
          ))}
          {!known && (
            <p className="px-2.5 py-2 text-[10px] text-gold leading-relaxed">
              Wallet is on chain {chainId || '?'} — deploys will ask for its RPC.
            </p>
          )}
        </div>
      )}
    </div>
  )
}

function BlocTimePageInner() {
  const [overview, setOverview] = useState<Overview | null>(null)
  const [stats, setStats] = useState<Stats | null>(null)
  const [points, setPoints] = useState<MultiplierPoint[]>([])
  const [loading, setLoading] = useState(false)
  const [connected, setConnected] = useState(false)
  const [account, setAccount] = useState('')
  const [chainId, setChainId] = useState(DEFAULT_CHAIN)
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

  // Weekly pot — the countdown ticks locally between the 15s stats polls.
  const [fundAmount, setFundAmount] = useState('')
  const [funding, setFunding] = useState(false)
  const [nowSec, setNowSec] = useState(() => Math.floor(Date.now() / 1000))

  // Contracts playground
  const [contractsMeta, setContractsMeta] = useState<ContractsMeta | null>(null)

  // Marketplace / instances
  const [instances, setInstances] = useState<Instance[]>([])
  const [marketLoading, setMarketLoading] = useState(false)
  const [activeInst, setActiveInst] = useState<Instance | null>(null)  // null = official (server-backed)
  const factoryRef = useRef<FactoryKit | null>(null)

  const getFactory = useCallback(async (): Promise<FactoryKit> => {
    if (factoryRef.current) return factoryRef.current
    const kit: FactoryKit = await api('factory', {}, 'GET')
    factoryRef.current = kit
    return kit
  }, [])

  const loadMarket = useCallback(async () => {
    setMarketLoading(true)
    try {
      const d = await api('registry', {}, 'GET')
      setInstances(d.instances || [])
    } catch { /* market unavailable */ }
    setMarketLoading(false)
  }, [])

  // Run wallet-signed writes against the active market instance.
  const withInstance = useCallback(async (
    fn: (c: ethers.Contract, signer: ethers.JsonRpcSigner, kit: FactoryKit) => Promise<void>,
  ) => {
    if (!activeInst) throw new Error('No instance selected')
    const w = window as any
    if (!w.ethereum) throw new Error('Install MetaMask — instance actions go through your wallet')
    await ensureChain({ chainId: activeInst.chainId, rpc: activeInst.rpc })
    const kit = await getFactory()
    const provider = new ethers.BrowserProvider(w.ethereum)
    const signer = await provider.getSigner()
    const c = new ethers.Contract(activeInst.bloctime, kit.contracts.bloctime.abi as any, signer)
    await fn(c, signer, kit)
  }, [activeInst, getFactory])

  const instanceMode = !!(activeInst && !activeInst.official)

  // ── Network ─────────────────────────────────────────────────────────

  // The header picker mirrors the wallet's chain and drives it, so anything
  // that switches networks (instance writes, deploys) keeps the header honest.
  useEffect(() => {
    const w = window as any
    if (!w.ethereum) return
    const apply = (hex: string) => { if (hex) setChainId(String(parseInt(hex, 16))) }
    w.ethereum.request({ method: 'eth_chainId' }).then(apply).catch(() => {})
    w.ethereum.on?.('chainChanged', apply)
    return () => w.ethereum.removeListener?.('chainChanged', apply)
  }, [])

  const selectNetwork = useCallback(async (net: NetworkDef) => {
    const w = window as any
    if (!w.ethereum) { setChainId(net.chainId); return }
    try {
      await ensureChain({ chainId: net.chainId, rpc: net.rpc })
      setChainId(net.chainId)
    } catch (err: any) {
      toast.error(err?.message || `Could not switch to ${net.label}`)
    }
  }, [])

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
      if (activeInst && !activeInst.official) {
        const kit = await getFactory()
        const { stats: s, points: pts } = await readInstanceState(activeInst, kit)
        setStats(s)
        setPoints(pts)
        setInflationCurve(computeInflationCurve(s.inflationParams))
        if (account) {
          setOverview(await readInstanceOverview(activeInst, kit, account).catch(() => null))
        } else {
          setOverview(null)
        }
      } else {
        const [statsData, pointsData] = await Promise.all([
          api('stats', {}, 'GET').catch(() => null),
          api('points', {}, 'GET').catch(() => []),
        ])
        if (statsData) setStats(statsData)
        // A curve we already have beats an empty poll — the 15s refetch must
        // never blank the chart just because one sample round came back short.
        const pts = pointsData?.length ? pointsData : await sampleCurve()
        if (pts.length) setPoints(pts)

        if (account) {
          const ov = await api('overview', { address: account }).catch(() => null)
          if (ov) setOverview(ov)
        }
      }
    } catch { /* ignore */ }
    setLoading(false)
  }, [account, activeInst, getFactory])

  const fetchInflationCurve = useCallback(async () => {
    if (activeInst && !activeInst.official) return  // computed locally in fetchAll
    try {
      const d = await api('get_inflation_curve', {}, 'GET')
      if (d?.points) setInflationCurve(d.points)
    } catch {}
  }, [activeInst])

  useEffect(() => { fetchAll(); fetchInflationCurve() }, [fetchAll, fetchInflationCurve])

  // ABIs + addresses are static per deploy — fetch once when the tab opens.
  useEffect(() => {
    if (tab !== 'contracts' || contractsMeta) return
    api('contracts', {}, 'GET')
      .then(d => setContractsMeta(d))
      .catch(() => toast.error('Failed to load contracts'))
  }, [tab, contractsMeta])

  useEffect(() => {
    const iv = setInterval(fetchAll, 15000)
    return () => clearInterval(iv)
  }, [fetchAll])

  useEffect(() => {
    if (tab !== 'rewards') return
    const iv = setInterval(() => setNowSec(Math.floor(Date.now() / 1000)), 1000)
    return () => clearInterval(iv)
  }, [tab])

  useEffect(() => {
    if (tab === 'market') loadMarket()
  }, [tab, loadMarket])

  // ── Instance switching ─────────────────────────────────────────────

  const handleUse = useCallback((inst: Instance) => {
    setActiveInst(inst.official ? null : inst)
    setStats(null); setOverview(null); setPoints([]); setInflationCurve([])
    setTab('stake')
    toast.success(`Now using ${inst.name}`)
  }, [])

  // Dropping the cache makes the CONTRACTS tab refetch with the new contract.
  const handleContractDeployed = useCallback(() => setContractsMeta(null), [])

  const handleDeployed = useCallback((entry: Instance) => {
    setActiveInst(entry)
    setStats(null); setOverview(null); setPoints([]); setInflationCurve([])
    loadMarket()
    setTab('market')
  }, [loadMarket])

  // ── Staking Actions ────────────────────────────────────────────────

  const handleStake = useCallback(async () => {
    if (!stakeAmount || Number(stakeAmount) <= 0) { toast.error('Enter amount'); return }
    setStaking(true)
    try {
      if (instanceMode && activeInst) {
        await withInstance(async (c, signer, kit) => {
          const amt = ethers.parseEther(stakeAmount)
          const token = new ethers.Contract(activeInst.nativeToken, kit.contracts.nativeToken.abi as any, signer)
          await (await token.approve(activeInst.bloctime, amt)).wait()
          await (await c.stake(amt, BigInt(parseInt(lockBlocks) || 0))).wait()
        })
      } else {
        await api('stake', {
          amount: stakeAmount,
          lock_blocks: parseInt(lockBlocks),
          as_ether: true,
        })
      }
      toast.success('Staked successfully')
      setStakeAmount('')
      fetchAll()
    } catch (err: any) {
      toast.error(err?.reason || err?.shortMessage || err?.message || 'Stake failed')
    }
    setStaking(false)
  }, [stakeAmount, lockBlocks, fetchAll, instanceMode, activeInst, withInstance])

  const handleUnstake = useCallback(async (stakeId: number) => {
    try {
      if (instanceMode) {
        await withInstance(async c => { await (await c.unstake(BigInt(stakeId))).wait() })
      } else {
        await api('unstake', { stake_id: stakeId })
      }
      toast.success('Unstaked successfully')
      fetchAll()
    } catch (err: any) {
      toast.error(err?.reason || err?.shortMessage || err?.message || 'Unstake failed')
    }
  }, [fetchAll, instanceMode, withInstance])

  // ── Delegation Actions ─────────────────────────────────────────────

  const handleDelegate = useCallback(async () => {
    if (!delegateAddr.trim()) { toast.error('Enter delegate address'); return }
    setDelegating(true)
    try {
      if (instanceMode) {
        await withInstance(async c => { await (await c.delegate(delegateAddr.trim())).wait() })
      } else {
        await api('delegate', { delegate_to: delegateAddr.trim() })
      }
      toast.success('Delegated')
      setDelegateAddr('')
      fetchAll()
    } catch (err: any) {
      toast.error(err?.reason || err?.shortMessage || err?.message || 'Delegation failed')
    }
    setDelegating(false)
  }, [delegateAddr, fetchAll, instanceMode, withInstance])

  const handleUndelegate = useCallback(async () => {
    setDelegating(true)
    try {
      if (instanceMode) {
        await withInstance(async c => { await (await c.undelegate()).wait() })
      } else {
        await api('undelegate', {})
      }
      toast.success('Undelegated')
      fetchAll()
    } catch (err: any) {
      toast.error(err?.reason || err?.shortMessage || err?.message || 'Undelegate failed')
    }
    setDelegating(false)
  }, [fetchAll, instanceMode, withInstance])

  // ── Rewards Actions ────────────────────────────────────────────────

  const handleClaimRewards = useCallback(async () => {
    setClaiming(true)
    try {
      if (instanceMode) {
        await withInstance(async c => { await (await c.claimRewards()).wait() })
      } else {
        await api('claim_rewards', {})
      }
      toast.success('Rewards claimed')
      fetchAll()
    } catch (err: any) {
      toast.error(err?.reason || err?.shortMessage || err?.message || 'Claim failed')
    }
    setClaiming(false)
  }, [fetchAll, instanceMode, withInstance])

  const handleFundPot = useCallback(async () => {
    if (!fundAmount || Number(fundAmount) <= 0) { toast.error('Enter amount'); return }
    setFunding(true)
    try {
      const amt = ethers.parseEther(fundAmount)
      if (instanceMode) {
        await withInstance(async c => { await (await c.fundPot(amt)).wait() })
      } else {
        await api('fund_pot', { amount: fundAmount })
      }
      toast.success(`${fundAmount} BLOC added to the pot`)
      setFundAmount('')
      fetchAll()
    } catch (err: any) {
      toast.error(err?.reason || err?.shortMessage || err?.message || 'Funding failed')
    }
    setFunding(false)
  }, [fetchAll, fundAmount, instanceMode, withInstance])

  const handleDistribute = useCallback(async () => {
    setDistributing(true)
    try {
      if (instanceMode) {
        await withInstance(async c => { await (await c.distributeRewards()).wait() })
      } else {
        await api('distribute_rewards', {})
      }
      toast.success('Pot distributed to BLOC holders')
      fetchAll()
    } catch (err: any) {
      toast.error(err?.reason || err?.shortMessage || err?.message || 'Distribution failed')
    }
    setDistributing(false)
  }, [fetchAll, instanceMode, withInstance])

  // ── Weekly pot ─────────────────────────────────────────────────────

  const potRemaining = stats?.pot ? Math.max(0, stats.pot.nextDistribution - nowSec) : 0
  const potDue = !!stats?.pot && (stats.pot.due || potRemaining === 0)

  // Pro-rata cut of everything the pot will hold at the next payout.
  const potShare = useMemo(() => {
    if (!stats?.pot || !overview) return '0'
    const supply = BigInt(stats.pot.eligibleSupply || '0')
    if (supply === 0n) return '0'
    return (BigInt(stats.pot.projected) * BigInt(overview.blocBalance || '0') / supply).toString()
  }, [overview, stats])

  // ── Sorting ────────────────────────────────────────────────────────

  const toggleSort = (key: SortKey) => {
    if (sortKey === key) setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    else { setSortKey(key); setSortDir('desc') }
  }

  const SortIcon = ({ col }: { col: SortKey }) => {
    if (sortKey !== col) return <ChevronUpDownIcon className="w-3 h-3 text-faint" />
    return sortDir === 'asc'
      ? <ChevronUpIcon className="w-3 h-3 text-ink2" />
      : <ChevronDownIcon className="w-3 h-3 text-ink2" />
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
    <div className="min-h-screen text-ink">
      {/* The field: two washes, a grid and (on the tube skins) scanlines.
          All three scale off --glow / --scan, so paper skins get nothing. */}
      <div className="field-bg" aria-hidden />
      <div className="field-grid" aria-hidden />
      <div className="field-scan" aria-hidden />

      {/* Header — sticky, because the network you're on and the wallet
          you're signing with are the two facts you need at every scroll. */}
      <header className="sticky top-0 z-30 border-b border-hair bg-base/85 backdrop-blur-xl">
        <div className="max-w-5xl mx-auto px-4 md:px-6 py-3 flex items-center gap-3">
          <div className="w-10 h-10 shrink-0 grid place-items-center rounded-lg border border-line bg-panel2">
            <ClockIcon className="w-5 h-5 text-accent" />
          </div>
          {/* Below sm the clock mark carries the brand on its own — the
              wallet and network controls need every pixel of that row. */}
          <div className="min-w-0 hidden sm:block">
            <h1 className="text-lg md:text-xl font-semibold uppercase leading-none truncate" style={{ letterSpacing: '.16em' }}>
              BlocTime
            </h1>
            <p className="lbl-dim mt-1.5">Time-weighted staking</p>
          </div>

          <div className="ml-auto flex items-center gap-2">
            <NetworkPicker chainId={chainId} onSelect={selectNetwork} />
            {connected ? (
              <button
                className="chip hover:border-line"
                title="Copy address"
                onClick={() => { navigator.clipboard.writeText(account); toast.success('Address copied') }}
              >
                <span className="chip-dot bg-up" />
                {fmtAddr(account)}
              </button>
            ) : (
              <button onClick={connectWallet} className="btn btn-accent btn-sm">Connect</button>
            )}
            <button
              onClick={fetchAll}
              disabled={loading}
              className="btn btn-icon"
              title="Refresh"
            >
              <ArrowPathIcon className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
            </button>
            <ThemePicker />
          </div>
        </div>
      </header>

      <div className="relative z-10 px-4 md:px-6 py-5 max-w-5xl mx-auto space-y-5">

        {/* Tab Bar */}
        <div className="tabbar">
          {([
            ['stake', 'Stake', LockClosedIcon],
            ['rewards', 'Rewards', GiftIcon],
            ['market', 'Market', BuildingStorefrontIcon],
            ['deploy', 'Deploy', RocketLaunchIcon],
            ['bridge', 'Bridge', ArrowsRightLeftIcon],
            ['contracts', 'Contracts', CodeBracketIcon],
          ] as [Tab, string, any][]).map(([t, label, Icon]) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              aria-current={tab === t}
              className={`tab ${tab === t ? 'tab-on' : ''}`}
            >
              <Icon className={`w-3.5 h-3.5 ${tab === t ? 'text-accent' : ''}`} />
              {label}
            </button>
          ))}
        </div>

        {/* Active market instance banner */}
        {instanceMode && activeInst && (
          <div className="flex items-center justify-between gap-3 border border-gold/30 rounded-lg p-3 bg-gold/[0.07] flex-wrap">
            <span className="text-[11px] font-mono text-gold">
              Using <span className="font-bold">{activeInst.name}</span> · chain {activeInst.chainId} · {fmtAddr(activeInst.bloctime)} — reads via its RPC, writes via your wallet
            </span>
            <button
              onClick={() => {
                setActiveInst(null)
                setStats(null); setOverview(null); setPoints([]); setInflationCurve([])
              }}
              className="btn btn-sm"
            >
              Back to official
            </button>
          </div>
        )}

        {/* Stats */}
        {stats && (
          <StatGrid>
            <Stat label="Total Stakes" tone="accent" value={stats.totalStakes} />
            <Stat label="Total BlocTime" tone="gold" value={fmtEth(stats.totalBlocTime)} />
            <Stat label="BT Supply" tone="up" value={fmtEth(stats.totalSupply)} />
            <Stat label="Network" tone="iris" value={stats.network || '--'} />
          </StatGrid>
        )}

        {/* Account overview with rewards info */}
        {connected && overview && (
          <StatGrid>
            <Stat label="My BLOC" tone="accent" value={fmtEth(overview.totalBlocTime)} />
            <Stat label="Staked" tone="gold" value={fmtEth(overview.totalStaked)} />
            <Stat label="Pending Rewards" tone="up" value={fmtEth(overview.pendingRewards)} />
            <Stat label="Voting Power" tone="iris" value={fmtEth(overview.votingPower)} />
          </StatGrid>
        )}

        {/* ── Stake Tab ────────────────────────────────────────────────── */}
        {tab === 'stake' && (
          <div key="stake" className="space-y-5 animate-fade-up">
            {/* Stake form and the curve it moves along, side by side — the
                marker on the curve is the preview for the lock field. */}
            <div className="card">
              <div className="card-head">
                <LockClosedIcon className="w-4 h-4 text-accent" />
                <span className="lbl">Stake Tokens</span>
                <span className="lbl-dim ml-auto hidden sm:inline">Longer lock, bigger multiplier</span>
              </div>

              <div className="grid lg:grid-cols-[minmax(0,340px)_1fr] gap-4 p-4">
                <div className="space-y-3">
                  <label className="block">
                    <span className="lbl-dim mb-1.5 block">Amount (NTV)</span>
                    <input
                      type="number"
                      placeholder="0.00"
                      value={stakeAmount}
                      onChange={e => setStakeAmount(e.target.value)}
                      className="input"
                    />
                  </label>

                  <label className="block">
                    <span className="lbl-dim mb-1.5 block">Lock (blocks)</span>
                    <input
                      type="number"
                      placeholder="10000"
                      value={lockBlocks}
                      onChange={e => setLockBlocks(e.target.value)}
                      className="input"
                    />
                    {points.length > 0 && (
                      <span className="flex gap-1 mt-2">
                        {points.map(pt => (
                          <button
                            key={pt.blocks}
                            onClick={() => setLockBlocks(String(pt.blocks))}
                            className={`btn btn-sm flex-1 px-0 ${Number(lockBlocks) === pt.blocks ? 'btn-accent' : ''}`}
                            title={`${pt.blocks.toLocaleString()} blocks — ${pt.multiplierX}x`}
                          >
                            {pt.blocks >= 1000 ? `${(pt.blocks / 1000).toFixed(0)}k` : pt.blocks}
                          </button>
                        ))}
                      </span>
                    )}
                  </label>

                  <div className="flex items-center justify-between gap-3 p-3 rounded-md border border-hair bg-panel2">
                    <span>
                      <span className="lbl-dim block">You receive</span>
                      <span className="text-lg font-semibold text-up tabular-nums">
                        {stakeAmount ? (Number(stakeAmount) * currentMultiplier).toFixed(2) : '0.00'} BT
                      </span>
                    </span>
                    <span className="text-right">
                      <span className="lbl-dim block">Multiplier</span>
                      <span className="text-lg font-semibold text-accent tabular-nums">{currentMultiplier.toFixed(2)}x</span>
                    </span>
                  </div>

                  <button
                    onClick={handleStake}
                    disabled={staking || !stakeAmount}
                    className="btn btn-accent w-full"
                  >
                    {staking
                      ? <><ArrowPathIcon className="w-3.5 h-3.5 animate-spin" /> Staking…</>
                      : <><LockClosedIcon className="w-3.5 h-3.5" /> Stake</>}
                  </button>
                </div>

                {points.length > 0 ? (
                  <div className="rounded-md border border-hair bg-panel2 p-3 flex flex-col justify-center">
                    <p className="lbl-dim mb-1">Multiplier curve</p>
                    <MultiplierChart
                      points={points}
                      atBlocks={Number(lockBlocks) || 0}
                      atMultiplier={currentMultiplier}
                    />
                  </div>
                ) : (
                  <div className="rounded-md border border-hair bg-panel2 grid place-items-center min-h-[170px]">
                    {/* Sampling walks the RPC one lock length at a time, so
                        say "reading" rather than "unavailable" until it's done. */}
                    <span className="lbl-dim flex items-center gap-2">
                      {loading && <ArrowPathIcon className="w-3.5 h-3.5 animate-spin" />}
                      {loading ? 'Reading curve' : 'Curve unavailable'}
                    </span>
                  </div>
                )}
              </div>
            </div>

            {/* With no wallet there are no positions to show, so say what the
                thing does instead of leaving the page half empty. */}
            {!connected && (
              <div className="card">
                <div className="card-head">
                  <ClockIcon className="w-4 h-4 text-iris" />
                  <span className="lbl">How BlocTime works</span>
                  <button onClick={connectWallet} className="btn btn-accent btn-sm ml-auto">Connect wallet</button>
                </div>
                <div className="grid sm:grid-cols-3 gap-px bg-hair">
                  {([
                    [LockClosedIcon, 'Lock', 'Stake native tokens for a number of blocks. Longer locks earn a bigger multiplier.', 'accent'],
                    [ClockIcon, 'Accrue', 'Your stake × multiplier is minted as BLOC — time-weighted voting power you hold or delegate.', 'gold'],
                    [GiftIcon, 'Collect', 'Every Friday the pot is split across BLOC holders. Anyone can trigger the payout.', 'up'],
                  ] as [any, string, string, Tone][]).map(([Icon, title, body, tone]) => (
                    <div key={title} className="p-4 bg-panel">
                      <Icon className="w-4 h-4 mb-2" style={{ color: TONE[tone] }} />
                      <p className="lbl mb-1">{title}</p>
                      <p className="text-[11px] text-ink2 leading-relaxed">{body}</p>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* My Positions */}
            {overview && (
              <div className="card overflow-hidden">
                <div className="card-head">
                  <span className="lbl">My Positions</span>
                  <span className="chip">{overview.stakeCount}</span>
                  <div className="ml-auto flex gap-3 text-[11px]">
                    <span className="text-mute">Staked <span className="text-gold tabular-nums">{fmtEth(overview.totalStaked)}</span></span>
                    <span className="text-mute">BlocTime <span className="text-accent tabular-nums">{fmtEth(overview.totalBlocTime)}</span></span>
                  </div>
                </div>

                <div className="grid grid-cols-[52px_1fr_1fr_1fr_84px_52px] gap-2 px-4 py-2 border-b border-hair bg-panel2">
                  <div className="lbl-dim">ID</div>
                  <button onClick={() => toggleSort('amount')} className="lbl-dim flex items-center gap-1 hover:text-ink2 transition-colors justify-end">
                    Staked <SortIcon col="amount" />
                  </button>
                  <button onClick={() => toggleSort('bloctime')} className="lbl-dim flex items-center gap-1 hover:text-ink2 transition-colors justify-end">
                    BlocTime <SortIcon col="bloctime" />
                  </button>
                  <div className="lbl-dim text-right">Lock</div>
                  <button onClick={() => toggleSort('remaining')} className="lbl-dim flex items-center gap-1 hover:text-ink2 transition-colors justify-end">
                    Left <SortIcon col="remaining" />
                  </button>
                  <div className="lbl-dim text-center">Act</div>
                </div>

                {sortedPositions.length === 0 ? (
                  <div className="text-center py-14">
                    <LockOpenIcon className="w-6 h-6 mx-auto text-faint mb-2" />
                    <span className="lbl-dim">No active stakes</span>
                  </div>
                ) : (
                  <div>
                    {sortedPositions.map((pos) => {
                      const unlocked = pos.blocksRemaining === 0
                      // How far through its lock this position has served —
                      // the bar under the row is the only place you can read
                      // "nearly ready" at a glance.
                      const served = pos.lockBlocks > 0
                        ? Math.min(1, Math.max(0, 1 - pos.blocksRemaining / pos.lockBlocks))
                        : 1
                      return (
                        <div
                          key={pos.stakeId}
                          className="row relative grid grid-cols-[52px_1fr_1fr_1fr_84px_52px] gap-2 px-4 py-2.5 border-b border-hair items-center"
                        >
                          <span className="text-xs font-mono text-mute">#{pos.stakeId}</span>
                          <span className="text-xs font-semibold text-gold text-right tabular-nums">
                            {fmtEth(pos.amount)}
                          </span>
                          <span className="text-xs font-semibold text-accent text-right tabular-nums">
                            {fmtEth(pos.blocTimeBalance)}
                          </span>
                          <span className="text-xs text-mute text-right tabular-nums">
                            {pos.lockBlocks.toLocaleString()} blk
                          </span>
                          <span className={`text-xs text-right tabular-nums ${unlocked ? 'text-up font-semibold' : 'text-ink2'}`}>
                            {unlocked ? 'Ready' : pos.blocksRemaining.toLocaleString()}
                          </span>
                          <div className="flex justify-center">
                            <button
                              onClick={() => handleUnstake(pos.stakeId)}
                              disabled={!unlocked}
                              className={`btn btn-icon ${unlocked ? 'btn-up' : ''}`}
                              title={unlocked ? 'Unstake' : 'Still locked'}
                            >
                              <LockOpenIcon className="w-3.5 h-3.5" />
                            </button>
                          </div>
                          <span
                            className={`absolute left-0 bottom-0 h-px ${unlocked ? 'bg-up' : 'bg-accent/50'}`}
                            style={{ width: `${served * 100}%` }}
                            aria-hidden
                          />
                        </div>
                      )
                    })}
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {/* ── Rewards Tab ──────────────────────────────────────────────── */}
        {tab === 'rewards' && (
          <div key="rewards" className="space-y-5 animate-fade-up">
            {/* Epoch Stats */}
            {stats && (
              <StatGrid>
                <Stat label="Current Epoch" tone="accent" value={stats.currentEpoch} />
                <Stat label="Epoch Reward" tone="gold" value={fmtEth(stats.epochReward)} />
                <Stat label="Your Pending" tone="up" value={connected && overview ? fmtEth(overview.pendingRewards) : '--'} />
                <Stat label="Total Distributed" tone="iris" value={fmtEth(stats.totalDistributed)} />
              </StatGrid>
            )}

            {/* Weekly Pot — everything in it goes to BLOC holders on Friday */}
            {stats?.pot && (
              <div className="card border-gold/35">
                <div className="card-head">
                  <FireIcon className={`w-4 h-4 text-gold ${potDue ? 'animate-pulse-dot' : ''}`} />
                  <span className="lbl">Weekly Pot</span>
                  <span className="lbl-dim ml-auto normal-case tracking-normal">{stats.pot.schedule}</span>
                </div>

                <div className="p-4">
                <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mb-4">
                  <div className="p-3 rounded-md border border-hair bg-panel2">
                    <p className="lbl-dim mb-1">In the Pot</p>
                    <p className="text-2xl font-semibold text-gold tabular-nums">{fmtEth(stats.pot.projected)} BLOC</p>
                    {BigInt(stats.pot.pendingInflation || '0') > 0n && (
                      <p className="text-[10px] text-faint mt-1 tabular-nums">
                        {fmtEth(stats.pot.pot)} banked + {fmtEth(stats.pot.pendingInflation)} inflation
                      </p>
                    )}
                  </div>
                  <div className="p-3 rounded-md border border-hair bg-panel2">
                    <p className="lbl-dim mb-1">Next Payout</p>
                    <p className={`text-2xl font-semibold tabular-nums ${potDue ? 'text-up' : 'text-accent'}`}>
                      {potDue ? 'Ready' : fmtCountdown(potRemaining)}
                    </p>
                    <p className="text-[10px] text-faint mt-1">{fmtWhen(stats.pot.nextDistribution)}</p>
                  </div>
                  <div className="p-3 rounded-md border border-hair bg-panel2">
                    <p className="lbl-dim mb-1">Your Share</p>
                    <p className="text-2xl font-semibold text-up tabular-nums">
                      {connected && overview ? `${fmtEth(potShare)} BLOC` : '--'}
                    </p>
                    <p className="text-[10px] text-faint mt-1 tabular-nums">
                      {fmtEth(stats.pot.eligibleSupply)} BLOC eligible
                    </p>
                  </div>
                </div>

                <div className="flex gap-2 mb-2">
                  <input
                    type="number"
                    placeholder="Add BLOC to the pot"
                    value={fundAmount}
                    onChange={e => setFundAmount(e.target.value)}
                    className="input flex-1"
                  />
                  <button
                    onClick={handleFundPot}
                    disabled={funding || !connected || !fundAmount}
                    className="btn btn-gold shrink-0"
                  >
                    {funding ? <ArrowPathIcon className="w-3.5 h-3.5 animate-spin" /> : <FireIcon className="w-3.5 h-3.5" />}
                    Fund Pot
                  </button>
                </div>

                <button
                  onClick={handleDistribute}
                  disabled={distributing || !connected || !potDue}
                  className="btn btn-gold w-full py-3"
                >
                  {distributing
                    ? <><ArrowPathIcon className="w-3.5 h-3.5 animate-spin" /> Distributing…</>
                    : <><FireIcon className="w-3.5 h-3.5" /> {potDue ? 'Distribute Pot to Holders' : `Opens in ${fmtCountdown(potRemaining)}`}</>}
                </button>

                <p className="text-[10px] text-faint mt-2 text-center">
                  {stats.pot.lastDistribution
                    ? `Last payout ${fmtWhen(stats.pot.lastDistribution)}. `
                    : 'No payout yet. '}
                  Anyone can trigger it once the window opens.
                </p>
                </div>
              </div>
            )}

            {/* Inflation Curve */}
            {inflationCurve.length > 1 && stats && (
              <div className="card">
                <div className="card-head">
                  <ChartBarIcon className="w-4 h-4 text-gold" />
                  <span className="lbl">Bitcoin-Style Inflation Curve</span>
                  {stats.inflationParams?.halvingInterval > 0 && (
                    <span className="ml-auto text-[10px] text-gold tabular-nums">
                      Halving every {stats.inflationParams.halvingInterval} epochs (~{(stats.inflationParams.halvingInterval / 365.25).toFixed(1)} years)
                    </span>
                  )}
                </div>
                <div className="p-4">
                  <InflationChart
                    points={inflationCurve}
                    currentEpoch={stats.currentEpoch}
                    halvingInterval={stats.inflationParams?.halvingInterval || 0}
                  />
                </div>
              </div>
            )}

            <div className="grid md:grid-cols-2 gap-5">
            {/* Delegation */}
            <div className="card flex flex-col">
              <div className="card-head">
                <UserGroupIcon className="w-4 h-4 text-iris" />
                <span className="lbl">Delegation</span>
              </div>

              <div className="p-4 flex-1 flex flex-col">
              {connected && overview?.delegate ? (
                <div className="mb-4 p-3 rounded-md border border-up/25 bg-up/[0.07]">
                  <div className="flex items-center justify-between gap-3">
                    <div className="min-w-0">
                      <p className="lbl-dim mb-1">Delegated To</p>
                      <button
                        className="text-sm font-mono text-up hover:underline"
                        onClick={() => { navigator.clipboard.writeText(overview.delegate); toast.success('Copied') }}
                      >
                        {fmtAddr(overview.delegate)}
                      </button>
                    </div>
                    <button
                      onClick={handleUndelegate}
                      disabled={delegating}
                      className="btn btn-down btn-sm shrink-0"
                    >
                      {delegating ? 'Removing…' : 'Undelegate'}
                    </button>
                  </div>
                </div>
              ) : connected ? (
                <p className="text-xs text-mute mb-4">Not delegated. Delegate voting power to another address.</p>
              ) : (
                <p className="text-xs text-mute mb-4">Connect wallet to manage delegation.</p>
              )}

              <div className="flex gap-2 mt-auto">
                <input
                  type="text"
                  placeholder="Delegate address (0x...)"
                  value={delegateAddr}
                  onChange={e => setDelegateAddr(e.target.value)}
                  className="input flex-1"
                />
                <button
                  onClick={handleDelegate}
                  disabled={delegating || !connected || !delegateAddr.trim()}
                  className="btn btn-accent shrink-0"
                >
                  {delegating ? <ArrowPathIcon className="w-3.5 h-3.5 animate-spin" /> : <UserGroupIcon className="w-3.5 h-3.5" />}
                  Delegate
                </button>
              </div>
              </div>
            </div>

            {/* Claim Rewards */}
            <div className="card flex flex-col">
              <div className="card-head">
                <GiftIcon className="w-4 h-4 text-up" />
                <span className="lbl">Rewards</span>
              </div>

              <div className="p-4 flex-1 flex flex-col">
              {connected && overview && (
                <div className="mb-4 p-3 rounded-md border border-up/25 bg-up/[0.07]">
                  <p className="lbl-dim mb-1">Claimable</p>
                  <p className="text-2xl font-semibold text-up tabular-nums">{fmtEth(overview.pendingRewards)} BLOC</p>
                </div>
              )}

              <button
                onClick={handleClaimRewards}
                disabled={claiming || !connected || !overview || overview.pendingRewards === '0'}
                className="btn btn-up w-full py-3 mt-auto"
              >
                {claiming ? <><ArrowPathIcon className="w-3.5 h-3.5 animate-spin" /> Claiming…</> : <><GiftIcon className="w-3.5 h-3.5" /> Claim Rewards</>}
              </button>

              <p className="text-[10px] text-faint mt-2 text-center">
                Your share of every past payout. The next one lands {stats?.pot ? fmtWhen(stats.pot.nextDistribution) : 'Friday 12:00 EST'}.
              </p>
              </div>
            </div>
            </div>
          </div>
        )}

        {/* ── Market Tab ───────────────────────────────────────────────── */}
        {tab === 'market' && (
          <div key="market" className="animate-fade-up">
            <MarketPanel
              instances={instances}
              activeId={activeInst ? activeInst.id : 'official'}
              account={account}
              loading={marketLoading}
              onUse={handleUse}
              onRefresh={loadMarket}
            />
          </div>
        )}

        {/* ── Deploy Tab ───────────────────────────────────────────────── */}
        {tab === 'deploy' && (
          <div key="deploy" className="space-y-5 animate-fade-up">
            <DeployPanel connected={connected} chainId={chainId} getFactory={getFactory} onDeployed={handleDeployed} />
            <BuildPanel connected={connected} chainId={chainId} onDeployed={handleContractDeployed} />
          </div>
        )}

        {/* ── Bridge Tab ───────────────────────────────────────────────── */}
        {tab === 'bridge' && (
          <div key="bridge" className="animate-fade-up">
            <BridgePanel account={account} connected={connected} />
          </div>
        )}

        {/* ── Contracts Tab ────────────────────────────────────────────── */}
        {tab === 'contracts' && (
          contractsMeta
            ? <div key="contracts" className="animate-fade-up"><ContractsPlayground meta={contractsMeta} connected={connected} /></div>
            : (
              <div className="card py-16 flex items-center justify-center">
                <ArrowPathIcon className="w-5 h-5 text-mute animate-spin" />
              </div>
            )
        )}

        {/* Footer */}
        <footer className="flex items-center justify-center gap-2 py-6">
          <ClockIcon className="w-3.5 h-3.5 text-faint" />
          <span className="lbl-dim">BlocTime Module</span>
        </footer>
      </div>
    </div>
  )
}

export default dynamic(() => Promise.resolve(BlocTimePageInner), { ssr: false })
