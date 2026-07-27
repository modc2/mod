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

// Remote browsers can't reach localhost:8851 — go through the gateway's
// /api/bloctime route unless we're actually running on localhost.
const isLocal = typeof window !== 'undefined' &&
  ['localhost', '127.0.0.1'].includes(window.location.hostname)
const API_URL = isLocal
  ? (process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8851')
  : '/api/bloctime'

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
  explorer: string
  abi: AbiEntry[]
  source: string
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

const BRIDGE_APP_URL = isLocal ? 'http://localhost:8841/bridge' : '/bridge'

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

  const stats: Stats = {
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
  let pending = 0n, vp = 0n, deleg = ''
  try { pending = await c.earned(addr) } catch {}
  try { vp = await c.getVotingPower(addr) } catch {}
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
    positions,
  }
}

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
      await w.ethereum.request({
        method: 'wallet_addEthereumChain',
        params: [{
          chainId: hexId,
          chainName: `Chain ${meta.chainId}`,
          nativeCurrency: { name: 'ETH', symbol: 'ETH', decimals: 18 },
          rpcUrls: [meta.rpc],
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
        await ensureChain(meta)
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
          explorer: `https://sepolia.basescan.org/tx/${tx.hash}`,
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
    ? 'border-cyan-500/40 bg-cyan-500/15 text-cyan-300 hover:bg-cyan-500/25'
    : 'border-amber-500/40 bg-amber-500/15 text-amber-300 hover:bg-amber-500/25'

  return (
    <div className="border-b border-white/[0.04] last:border-b-0">
      <button
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center justify-between px-4 py-2.5 hover:bg-white/[0.03] transition-colors text-left"
      >
        <span className="text-xs font-mono text-white/70">
          {fn.name}
          <span className="text-white/25">({inputs.map(i => i.type).join(', ')})</span>
        </span>
        <div className="flex items-center gap-2">
          {payable && <span className="text-[9px] uppercase tracking-wider text-rose-400/60 border border-rose-500/20 rounded px-1.5 py-0.5">payable</span>}
          {(fn.outputs || []).length > 0 && mode === 'read' && (
            <span className="text-[9px] font-mono text-white/25">→ {(fn.outputs || []).map(o => o.type).join(', ')}</span>
          )}
          {open ? <ChevronUpIcon className="w-3 h-3 text-white/30" /> : <ChevronDownIcon className="w-3 h-3 text-white/30" />}
        </div>
      </button>

      {open && (
        <div className="px-4 pb-4 space-y-2">
          {inputs.map((inp, i) => (
            <div key={i} className="flex items-center gap-2">
              <span className="text-[10px] font-mono text-white/35 w-36 shrink-0 truncate">
                {inp.name || `arg${i}`} <span className="text-white/20">({inp.type})</span>
              </span>
              <input
                type="text"
                placeholder={inp.type.endsWith(']') ? '["a", "b"] (JSON array)' : inp.type}
                value={args[i]}
                onChange={e => setArgs(a => a.map((v, j) => j === i ? e.target.value : v))}
                className="flex-1 text-xs px-3 py-2 rounded-lg border border-white/10 bg-white/5 text-white/90 focus:outline-none focus:border-white/30 font-mono transition-colors placeholder:text-white/15"
              />
            </div>
          ))}
          {mode === 'write' && payable && (
            <div className="flex items-center gap-2">
              <span className="text-[10px] font-mono text-rose-400/50 w-36 shrink-0">value <span className="text-white/20">(wei)</span></span>
              <input
                type="text"
                placeholder="0"
                value={payValue}
                onChange={e => setPayValue(e.target.value)}
                className="flex-1 text-xs px-3 py-2 rounded-lg border border-white/10 bg-white/5 text-white/90 focus:outline-none focus:border-white/30 font-mono transition-colors placeholder:text-white/15"
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
              <span className="text-[9px] uppercase tracking-wider text-white/25">
                via {writeVia === 'wallet' ? (connected ? 'connected wallet' : 'wallet (connect first)') : `server signer${meta.signer ? ` ${fmtAddr(meta.signer)}` : ''}`}
              </span>
            )}
          </div>

          {error && (
            <p className="text-[11px] font-mono text-rose-400/80 break-all border border-rose-500/20 bg-rose-500/[0.05] rounded-lg p-3">{error}</p>
          )}
          {result !== null && (
            <div className="border border-emerald-500/15 bg-emerald-500/[0.03] rounded-lg p-3 space-y-1">
              <pre className="text-[11px] font-mono text-emerald-300/80 whitespace-pre-wrap break-all">
                {JSON.stringify(result.output !== undefined ? result.output : result, null, 2)}
              </pre>
              {result.explorer && (
                <a href={result.explorer} target="_blank" rel="noreferrer"
                   className="inline-flex items-center gap-1 text-[10px] uppercase tracking-wider text-cyan-400/70 hover:text-cyan-300">
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
      <div className="border border-white/10 rounded-lg bg-white/[0.02] p-4 space-y-3">
        <div className="flex items-center gap-2 flex-wrap">
          {meta.contracts.map((c, i) => (
            <button
              key={c.id}
              onClick={() => setSelected(i)}
              className={`px-4 py-2 rounded-lg border text-[10px] font-bold uppercase tracking-wider transition-colors
                ${i === selected
                  ? 'border-cyan-500/40 bg-cyan-500/15 text-cyan-300'
                  : 'border-white/10 bg-white/5 text-white/40 hover:text-white/60 hover:bg-white/10'}`}
            >
              {c.name}
            </button>
          ))}
          <span className="ml-auto text-[10px] uppercase tracking-wider text-violet-400/70">{meta.network} · chain {meta.chainId}</span>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-xs font-mono text-white/60 break-all">{contract.address}</span>
          <button onClick={() => copy(contract.address, 'Address')} className="p-1 rounded text-white/30 hover:text-white/70 transition-colors" title="Copy address">
            <DocumentDuplicateIcon className="w-3.5 h-3.5" />
          </button>
          {contract.explorer && (
            <a href={contract.explorer} target="_blank" rel="noreferrer" className="p-1 rounded text-white/30 hover:text-cyan-400 transition-colors" title="View on Basescan">
              <ArrowTopRightOnSquareIcon className="w-3.5 h-3.5" />
            </a>
          )}
        </div>
      </div>

      {/* View switch */}
      <div className="flex gap-1 p-1 rounded-lg border border-white/10 bg-white/[0.02]">
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
              ${view === v ? 'bg-white/[0.06] text-white/90' : 'text-white/30 hover:text-white/50 hover:bg-white/[0.02]'}`}
          >
            {label}
          </button>
        ))}
      </div>

      {(view === 'read' || view === 'write') && (
        <div className="border border-white/10 rounded-lg bg-white/[0.02] overflow-hidden">
          <div className="flex items-center justify-between px-4 py-3 border-b border-white/5">
            <span className="text-[10px] font-bold uppercase tracking-wider text-white/40">
              {view === 'read' ? 'Read Functions' : 'Write Functions'}
            </span>
            {view === 'write' && (
              <div className="flex gap-1 p-0.5 rounded-lg border border-white/10 bg-white/[0.02]">
                {(['wallet', 'server'] as const).map(v => (
                  <button
                    key={v}
                    onClick={() => setWriteVia(v)}
                    className={`px-3 py-1 rounded-md text-[9px] font-bold uppercase tracking-wider transition-all
                      ${writeVia === v ? 'bg-white/[0.08] text-white/80' : 'text-white/30 hover:text-white/50'}`}
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
        <div className="border border-white/10 rounded-lg bg-white/[0.02] overflow-hidden">
          <div className="flex items-center justify-between px-4 py-3 border-b border-white/5">
            <span className="text-[10px] font-bold uppercase tracking-wider text-white/40">
              ABI · {fns.length} functions · {events.length} events
            </span>
            <button
              onClick={() => copy(JSON.stringify(contract.abi, null, 2), 'ABI')}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-white/10 bg-white/5 text-[10px] font-bold uppercase tracking-wider text-white/50 hover:bg-white/10 hover:text-white/70 transition-colors"
            >
              <DocumentDuplicateIcon className="w-3 h-3" /> Copy ABI
            </button>
          </div>
          <pre className="p-4 text-[11px] font-mono text-white/60 overflow-x-auto max-h-[500px] overflow-y-auto whitespace-pre">
            {JSON.stringify(contract.abi, null, 2)}
          </pre>
        </div>
      )}

      {view === 'source' && (
        <div className="border border-white/10 rounded-lg bg-white/[0.02] overflow-hidden">
          <div className="flex items-center justify-between px-4 py-3 border-b border-white/5">
            <span className="text-[10px] font-bold uppercase tracking-wider text-white/40">{contract.name}.sol</span>
            <button
              onClick={() => copy(contract.source, 'Source')}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-white/10 bg-white/5 text-[10px] font-bold uppercase tracking-wider text-white/50 hover:bg-white/10 hover:text-white/70 transition-colors"
            >
              <DocumentDuplicateIcon className="w-3 h-3" /> Copy
            </button>
          </div>
          <pre className="p-4 text-[11px] font-mono text-white/60 overflow-x-auto max-h-[600px] overflow-y-auto whitespace-pre">
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
      <div className="flex items-center justify-between border border-white/10 rounded-lg p-4 bg-white/[0.02]">
        <div className="flex items-center gap-2">
          <BuildingStorefrontIcon className="w-4 h-4 text-white/40" />
          <span className="text-[10px] font-bold uppercase tracking-wider text-white/40">
            BlocTime Market · {instances.length} deployed instance{instances.length !== 1 ? 's' : ''}
          </span>
        </div>
        <button
          onClick={onRefresh}
          disabled={loading}
          className="p-1.5 rounded-lg border border-white/10 bg-white/5 text-white/40 hover:text-white/70 hover:bg-white/10 disabled:opacity-30 transition-colors"
        >
          <ArrowPathIcon className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
        </button>
      </div>

      {instances.length === 0 && (
        <div className="border border-white/10 rounded-lg bg-white/[0.02] py-16 text-center">
          <span className="text-xs text-white/30 uppercase tracking-wider">
            {loading ? 'Loading market...' : 'No instances registered yet — deploy one from the DEPLOY tab'}
          </span>
        </div>
      )}

      <div className="grid md:grid-cols-2 gap-4">
        {instances.map(inst => {
          const active = inst.id === activeId
          const mine = account && inst.owner && account.toLowerCase() === inst.owner.toLowerCase()
          return (
            <div key={inst.id} className={`border rounded-lg p-4 bg-white/[0.02] space-y-3 transition-colors ${active ? 'border-cyan-500/40' : 'border-white/10'}`}>
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <h3 className="text-sm font-bold text-white/90 truncate">{inst.name}</h3>
                    {inst.official && (
                      <span className="text-[9px] uppercase tracking-wider text-amber-300/80 border border-amber-500/30 bg-amber-500/10 rounded px-1.5 py-0.5">official</span>
                    )}
                    {mine && (
                      <span className="text-[9px] uppercase tracking-wider text-emerald-300/80 border border-emerald-500/30 bg-emerald-500/10 rounded px-1.5 py-0.5">yours</span>
                    )}
                  </div>
                  {inst.description && <p className="text-[11px] text-white/35 mt-1 line-clamp-2">{inst.description}</p>}
                </div>
                {mine && !inst.official && (
                  <button
                    onClick={() => handleRemove(inst)}
                    disabled={removing === inst.id}
                    className="p-1 rounded text-white/25 hover:text-rose-400 disabled:opacity-30 transition-colors shrink-0"
                    title="Remove from market (signs with your wallet)"
                  >
                    {removing === inst.id ? <ArrowPathIcon className="w-3.5 h-3.5 animate-spin" /> : <XMarkIcon className="w-3.5 h-3.5" />}
                  </button>
                )}
              </div>

              <div className="grid grid-cols-3 border border-white/[0.06] rounded-lg overflow-hidden">
                <div className="p-2 text-center border-r border-white/[0.06]">
                  <p className="text-sm font-bold text-cyan-400 tabular-nums">{inst.stats ? inst.stats.totalStakes : '--'}</p>
                  <p className="text-[9px] uppercase tracking-wider text-white/25">Stakes</p>
                </div>
                <div className="p-2 text-center border-r border-white/[0.06]">
                  <p className="text-sm font-bold text-amber-400 tabular-nums">{inst.stats ? fmtEth(inst.stats.totalBlocTime) : '--'}</p>
                  <p className="text-[9px] uppercase tracking-wider text-white/25">BlocTime</p>
                </div>
                <div className="p-2 text-center">
                  <p className="text-sm font-bold text-emerald-400 tabular-nums">{inst.stats ? fmtEth(inst.stats.totalSupply) : '--'}</p>
                  <p className="text-[9px] uppercase tracking-wider text-white/25">BT Supply</p>
                </div>
              </div>

              <div className="space-y-1 text-[10px] font-mono text-white/35">
                <p>chain <span className="text-violet-400/70">{inst.chainId || '?'}</span> · contract <span className="text-white/55">{fmtAddr(inst.bloctime)}</span></p>
                {inst.owner && <p>owner <span className="text-white/55">{fmtAddr(inst.owner)}</span></p>}
              </div>

              <div className="flex items-center gap-2">
                <button
                  onClick={() => onUse(inst)}
                  disabled={active}
                  className={`flex-1 py-2 rounded-lg border text-[10px] font-bold uppercase tracking-wider transition-colors flex items-center justify-center gap-1.5
                    ${active
                      ? 'border-cyan-500/40 bg-cyan-500/15 text-cyan-300 cursor-default'
                      : 'border-white/10 bg-white/5 text-white/50 hover:bg-white/10 hover:text-white/70'}`}
                >
                  {active ? <><CheckCircleIcon className="w-3.5 h-3.5" /> Active</> : 'Use'}
                </button>
                {inst.explorer && (
                  <a href={inst.explorer} target="_blank" rel="noreferrer"
                     className="p-2 rounded-lg border border-white/10 bg-white/5 text-white/30 hover:text-cyan-400 transition-colors" title="View on explorer">
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

function DeployPanel({ connected, getFactory, onDeployed }: {
  connected: boolean
  getFactory: () => Promise<FactoryKit>
  onDeployed: (entry: Instance) => void
}) {
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [supply, setSupply] = useState('1000000')
  const [maxLock, setMaxLock] = useState('100000')
  const [distPct, setDistPct] = useState('5000')
  const [rpc, setRpc] = useState('https://sepolia.base.org')
  const [network, setNetwork] = useState<'base_sepolia' | 'wallet'>('base_sepolia')
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

  const setStep = (i: number, state: StepState) =>
    setSteps(s => s.map((st, j) => j === i ? { ...st, state } : st))

  const handleDeploy = useCallback(async () => {
    if (!name.trim()) { toast.error('Name your instance'); return }
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
      if (network === 'base_sepolia') {
        await ensureChain({ chainId: '84532', rpc: 'https://sepolia.base.org' })
      }
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
  }, [name, description, supply, maxLock, distPct, rpc, network, getFactory, onDeployed])

  const input = "w-full text-sm px-4 py-2.5 rounded-lg border border-white/10 bg-white/5 text-white/90 focus:outline-none focus:border-white/30 font-mono transition-colors placeholder:text-white/20"

  return (
    <div className="space-y-4">
      {/* Wallet deploy */}
      <div className="border border-white/10 rounded-lg p-4 bg-white/[0.02] space-y-3">
        <div className="flex items-center gap-2">
          <RocketLaunchIcon className="w-4 h-4 text-white/40" />
          <span className="text-[10px] font-bold uppercase tracking-wider text-white/40">Deploy your own BlocTime</span>
        </div>
        <p className="text-[11px] text-white/35">
          Deploys a fresh NativeToken + BlocTime pair from <span className="text-white/60">your wallet</span> —
          you pay gas, you own the contracts. It is then listed on the market for everyone to browse and stake.
        </p>

        <div className="grid md:grid-cols-2 gap-3">
          <input type="text" placeholder="Instance name" value={name} onChange={e => setName(e.target.value)} className={input} />
          <input type="text" placeholder="Description (optional)" value={description} onChange={e => setDescription(e.target.value)} className={input} />
          <div>
            <p className="text-[9px] uppercase tracking-wider text-white/25 mb-1">Token supply (NTV)</p>
            <input type="number" value={supply} onChange={e => setSupply(e.target.value)} className={input} />
          </div>
          <div>
            <p className="text-[9px] uppercase tracking-wider text-white/25 mb-1">Max lock (blocks)</p>
            <input type="number" value={maxLock} onChange={e => setMaxLock(e.target.value)} className={input} />
          </div>
          <div>
            <p className="text-[9px] uppercase tracking-wider text-white/25 mb-1">Distribution % (bps, 5000 = 50%)</p>
            <input type="number" value={distPct} onChange={e => setDistPct(e.target.value)} className={input} />
          </div>
          <div>
            <p className="text-[9px] uppercase tracking-wider text-white/25 mb-1">Network</p>
            <div className="flex gap-1 p-1 rounded-lg border border-white/10 bg-white/[0.02]">
              {([['base_sepolia', 'Base Sepolia'], ['wallet', 'Wallet network']] as const).map(([v, label]) => (
                <button key={v} onClick={() => setNetwork(v)}
                  className={`flex-1 py-1.5 rounded-md text-[9px] font-bold uppercase tracking-wider transition-all
                    ${network === v ? 'bg-white/[0.08] text-white/80' : 'text-white/30 hover:text-white/50'}`}>
                  {label}
                </button>
              ))}
            </div>
          </div>
          {network === 'wallet' && (
            <div className="md:col-span-2">
              <p className="text-[9px] uppercase tracking-wider text-white/25 mb-1">Public RPC URL of that network (used to verify + read stats)</p>
              <input type="text" value={rpc} onChange={e => setRpc(e.target.value)} className={input} />
            </div>
          )}
        </div>

        <button
          onClick={handleDeploy}
          disabled={busy || !connected || !name.trim()}
          className="w-full py-3 rounded-lg border border-cyan-500/40 bg-cyan-500/15 text-cyan-300 text-[10px] font-bold uppercase tracking-wider hover:bg-cyan-500/25 disabled:opacity-30 disabled:cursor-not-allowed transition-colors flex items-center justify-center gap-2"
        >
          {busy ? <><ArrowPathIcon className="w-3.5 h-3.5 animate-spin" /> Deploying...</> : <><RocketLaunchIcon className="w-3.5 h-3.5" /> Deploy with wallet</>}
        </button>
        {!connected && <p className="text-[10px] text-amber-400/60 text-center">Connect your wallet first</p>}

        {steps.length > 0 && (
          <div className="border border-white/[0.06] rounded-lg p-3 space-y-1.5">
            {steps.map((s, i) => (
              <div key={i} className="flex items-center gap-2 text-[11px] font-mono">
                {s.state === 'done' && <CheckCircleIcon className="w-3.5 h-3.5 text-emerald-400" />}
                {s.state === 'active' && <ArrowPathIcon className="w-3.5 h-3.5 text-cyan-400 animate-spin" />}
                {s.state === 'error' && <XMarkIcon className="w-3.5 h-3.5 text-rose-400" />}
                {s.state === 'pending' && <div className="w-3.5 h-3.5 flex items-center justify-center"><div className="w-1.5 h-1.5 rounded-full bg-white/15" /></div>}
                <span className={s.state === 'pending' ? 'text-white/25' : s.state === 'error' ? 'text-rose-400/80' : 'text-white/60'}>{s.label}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Fork the module */}
      <div className="border border-white/10 rounded-lg p-4 bg-white/[0.02] space-y-2">
        <div className="flex items-center gap-2">
          <CodeBracketIcon className="w-4 h-4 text-white/40" />
          <span className="text-[10px] font-bold uppercase tracking-wider text-white/40">Fork the whole module</span>
        </div>
        <p className="text-[11px] text-white/35">
          Want your own console, API and contracts under your own name? Fork the module itself —
          it copies everything into a sibling module with its own ports and route, ready to deploy and serve.
        </p>
        <pre className="text-[11px] font-mono text-cyan-300/80 bg-black/30 border border-white/[0.06] rounded-lg p-3 overflow-x-auto">{forkCmd}</pre>
        <p className="text-[10px] text-white/25 font-mono">then: m &lt;yourname&gt;/serve · deploy from its DEPLOY tab · m bloctime/register_instance to list it here</p>
      </div>
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
      <div className="border border-white/10 rounded-lg p-4 bg-white/[0.02] space-y-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <ArrowsRightLeftIcon className="w-4 h-4 text-white/40" />
            <span className="text-[10px] font-bold uppercase tracking-wider text-white/40">Bridge into BlocTime</span>
          </div>
          <span className={`text-[9px] uppercase tracking-wider border rounded px-1.5 py-0.5 ${
            loading ? 'text-white/30 border-white/10'
              : info?.online ? 'text-emerald-300/80 border-emerald-500/30 bg-emerald-500/10'
              : 'text-rose-300/80 border-rose-500/30 bg-rose-500/10'}`}>
            {loading ? 'checking...' : info?.online ? 'bridge online' : 'bridge offline'}
          </span>
        </div>
        <p className="text-[11px] text-white/35">
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
            <div key={i} className="border border-white/[0.06] rounded-lg p-2.5">
              <p className="text-sm font-bold text-cyan-400 tabular-nums">{value ?? '--'}</p>
              <p className="text-[9px] uppercase tracking-wider text-white/25 mt-0.5">{label}</p>
            </div>
          ))}
        </div>
      </div>

      {/* Flow */}
      <div className="border border-white/10 rounded-lg p-4 bg-white/[0.02]">
        <p className="text-[10px] font-bold uppercase tracking-wider text-white/40 mb-3">How it works</p>
        <div className="grid md:grid-cols-4 gap-2">
          {[
            ['1 · Check', 'Look your Substrate/Solana address up in the snapshot below'],
            ['2 · Commit', 'In the bridge app, sign with SubWallet/Phantom to link your EVM address'],
            ['3 · Claim', 'Claim your bridged tokens on Base'],
            ['4 · Stake', 'Come back and stake them in any market instance'],
          ].map(([title, body], i) => (
            <div key={i} className="border border-white/[0.06] rounded-lg p-3">
              <p className="text-[10px] font-bold uppercase tracking-wider text-cyan-400/70 mb-1">{title}</p>
              <p className="text-[10px] text-white/35 leading-relaxed">{body}</p>
            </div>
          ))}
        </div>
      </div>

      {/* Snapshot checker */}
      <div className="border border-white/10 rounded-lg p-4 bg-white/[0.02] space-y-3">
        <p className="text-[10px] font-bold uppercase tracking-wider text-white/40">Check snapshot balance</p>
        <div className="flex gap-2 flex-wrap">
          <input
            type="text"
            placeholder="Substrate / Solana address"
            value={srcAddr}
            onChange={e => setSrcAddr(e.target.value)}
            className="flex-1 min-w-[220px] text-sm px-4 py-2.5 rounded-lg border border-white/10 bg-white/5 text-white/90 focus:outline-none focus:border-white/30 font-mono transition-colors placeholder:text-white/20"
          />
          <button
            onClick={handleCheck}
            disabled={checking || !info?.online}
            className="px-4 py-2.5 rounded-lg border border-cyan-500/40 bg-cyan-500/15 text-cyan-300 text-[10px] font-bold uppercase tracking-wider hover:bg-cyan-500/25 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
          >
            {checking ? <ArrowPathIcon className="w-3.5 h-3.5 animate-spin" /> : 'Check'}
          </button>
        </div>
        {checkResult && (
          <pre className="text-[11px] font-mono text-emerald-300/80 whitespace-pre-wrap break-all border border-emerald-500/15 bg-emerald-500/[0.03] rounded-lg p-3">
            {JSON.stringify(checkResult, null, 2)}
          </pre>
        )}
      </div>

      <a
        href={BRIDGE_APP_URL}
        target="_blank"
        rel="noreferrer"
        className="block w-full py-3 rounded-lg border border-violet-500/40 bg-violet-500/15 text-violet-300 text-[10px] font-bold uppercase tracking-wider hover:bg-violet-500/25 transition-colors text-center"
      >
        Open the Bridge App → commit & claim
      </a>
    </div>
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
        if (pointsData) setPoints(pointsData)

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
    if (tab === 'market') loadMarket()
  }, [tab, loadMarket])

  // ── Instance switching ─────────────────────────────────────────────

  const handleUse = useCallback((inst: Instance) => {
    setActiveInst(inst.official ? null : inst)
    setStats(null); setOverview(null); setPoints([]); setInflationCurve([])
    setTab('stake')
    toast.success(`Now using ${inst.name}`)
  }, [])

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

  const handleDistribute = useCallback(async () => {
    setDistributing(true)
    try {
      if (instanceMode) {
        await withInstance(async c => { await (await c.distributeRewards()).wait() })
      } else {
        await api('distribute_rewards', {})
      }
      toast.success('Rewards distributed')
      fetchAll()
    } catch (err: any) {
      toast.error(err?.reason || err?.shortMessage || err?.message || 'Distribution failed')
    }
    setDistributing(false)
  }, [fetchAll, instanceMode, withInstance])

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
        <div className="flex gap-1 p-1 rounded-lg border border-white/10 bg-white/[0.02] overflow-x-auto">
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

        {/* Active market instance banner */}
        {instanceMode && activeInst && (
          <div className="flex items-center justify-between gap-3 border border-amber-500/25 rounded-lg p-3 bg-amber-500/[0.04] flex-wrap">
            <span className="text-[11px] font-mono text-amber-300/80">
              Using <span className="font-bold">{activeInst.name}</span> · chain {activeInst.chainId} · {fmtAddr(activeInst.bloctime)} — reads via its RPC, writes via your wallet
            </span>
            <button
              onClick={() => {
                setActiveInst(null)
                setStats(null); setOverview(null); setPoints([]); setInflationCurve([])
              }}
              className="px-3 py-1.5 rounded-lg border border-white/10 bg-white/5 text-[10px] font-bold uppercase tracking-wider text-white/50 hover:bg-white/10 hover:text-white/70 transition-colors"
            >
              Back to official
            </button>
          </div>
        )}

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

        {/* ── Market Tab ───────────────────────────────────────────────── */}
        {tab === 'market' && (
          <MarketPanel
            instances={instances}
            activeId={activeInst ? activeInst.id : 'official'}
            account={account}
            loading={marketLoading}
            onUse={handleUse}
            onRefresh={loadMarket}
          />
        )}

        {/* ── Deploy Tab ───────────────────────────────────────────────── */}
        {tab === 'deploy' && (
          <DeployPanel connected={connected} getFactory={getFactory} onDeployed={handleDeployed} />
        )}

        {/* ── Bridge Tab ───────────────────────────────────────────────── */}
        {tab === 'bridge' && (
          <BridgePanel account={account} connected={connected} />
        )}

        {/* ── Contracts Tab ────────────────────────────────────────────── */}
        {tab === 'contracts' && (
          contractsMeta
            ? <ContractsPlayground meta={contractsMeta} connected={connected} />
            : (
              <div className="border border-white/10 rounded-lg bg-white/[0.02] py-16 flex items-center justify-center">
                <ArrowPathIcon className="w-5 h-5 text-white/30 animate-spin" />
              </div>
            )
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
