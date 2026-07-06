"use client";

import { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import dynamic from 'next/dynamic'
import { toast } from 'react-toastify'
import {
  ArrowPathIcon,
  RocketLaunchIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  ArrowTopRightOnSquareIcon,
  MagnifyingGlassIcon,
  LinkIcon,
} from '@heroicons/react/24/outline'
import { Shell, NetworkSelect, RefreshBtn, BlockChip } from './components/Shell'

// ── Constants ──────────────────────────────────────────────────────────────

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8800'

const RPC_URLS: Record<string, string> = {
  testnet: 'https://sepolia.base.org',
  mainnet: 'https://mainnet.base.org',
  ganache: 'http://localhost:8545',
  localhost: 'http://localhost:8545',
}

const EXPLORER_URLS: Record<string, string> = {
  testnet: 'https://sepolia.basescan.org',
  mainnet: 'https://basescan.org',
  ganache: '',
  localhost: '',
}

const CHAIN_NAMES: Record<string, string> = {
  testnet: 'Base Sepolia',
  mainnet: 'Base',
  ganache: 'Ganache',
  localhost: 'Localhost',
}

const MOD_CONTRACTS: Record<string, string[]> = {
  token: ['USDC', 'USDT', 'NativeToken', 'DAI'],
  oracle: ['ManualPriceOracle', 'ChainlinkOracle', 'PythOracle', 'Oracle'],
  registry: ['Registry'],
  perms: ['Perms'],
  tokengate: ['TokenGate'],
  bloctime: ['BlocTime'],
  treasury: ['Treasury'],
  market: ['Market'],
  defi: ['YieldVault', 'MockYieldAdapter_USDC', 'MockYieldAdapter_USDT', 'AaveV3Adapter'],
  debit: ['Debit'],
  safe: ['Safe', 'SafeProxy'],
  bridge: ['Bridge'],
}

const MOD_COLORS: Record<string, { text: string; glow: string; dot: string }> = {
  token:     { text: 'text-emerald-300', glow: 'rgba(52,211,153,0.5)',  dot: 'bg-emerald-400' },
  oracle:    { text: 'text-amber-300',   glow: 'rgba(251,191,36,0.5)',  dot: 'bg-amber-400' },
  registry:  { text: 'text-cyan-300',    glow: 'rgba(34,211,238,0.5)',  dot: 'bg-cyan-400' },
  perms:     { text: 'text-violet-300',  glow: 'rgba(167,139,250,0.5)', dot: 'bg-violet-400' },
  tokengate: { text: 'text-rose-300',    glow: 'rgba(251,113,133,0.5)', dot: 'bg-rose-400' },
  bloctime:  { text: 'text-sky-300',     glow: 'rgba(56,189,248,0.5)',  dot: 'bg-sky-400' },
  treasury:  { text: 'text-lime-300',    glow: 'rgba(163,230,53,0.5)',  dot: 'bg-lime-400' },
  market:    { text: 'text-orange-300',  glow: 'rgba(251,146,60,0.5)',  dot: 'bg-orange-400' },
  defi:      { text: 'text-green-300',   glow: 'rgba(74,222,128,0.5)',  dot: 'bg-green-400' },
  debit:     { text: 'text-pink-300',    glow: 'rgba(244,114,182,0.5)', dot: 'bg-pink-400' },
  safe:      { text: 'text-teal-300',    glow: 'rgba(45,212,191,0.5)',  dot: 'bg-teal-400' },
  bridge:    { text: 'text-indigo-300',  glow: 'rgba(129,140,248,0.5)', dot: 'bg-indigo-400' },
}

const defaultColors = { text: 'text-white/70', glow: 'rgba(255,255,255,0.25)', dot: 'bg-white/40' }

// ── Types ──────────────────────────────────────────────────────────────────

interface ModInfo {
  name: string
  api_port: number
  app_port: number
  api_url: string
  app_url: string
  alive: boolean
}

interface NetworkDeployment {
  chainId: string
  deployer: string
  url: string
  contract_count: number
  contracts: { [key: string]: string }
}

interface ChainData {
  blockNumber: number | null
  balance: string | null
}

// ── Helpers ────────────────────────────────────────────────────────────────

async function apiCall(path: string, params: Record<string, any> = {}, method = 'GET') {
  const opts: RequestInit = method === 'GET'
    ? { method: 'GET' }
    : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(params) }
  const res = await fetch(`${API_URL}/${path}`, opts)
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || 'Request failed')
  }
  return res.json()
}

const fmtAddr = (s: string, chars = 6) =>
  s && s.length > 16 ? `${s.slice(0, chars + 2)}…${s.slice(-chars)}` : (s || '—')

const fmtBalance = (wei: string | null) => {
  if (!wei) return '—'
  try {
    const eth = Number(BigInt(wei)) / 1e18
    return eth.toFixed(4)
  } catch { return '—' }
}

const getExplorerUrl = (network: string, address: string) => {
  const base = EXPLORER_URLS[network]
  return base ? `${base}/address/${address}` : ''
}

function getModuleContracts(modName: string, allContracts: Record<string, string>): [string, string][] {
  const patterns = MOD_CONTRACTS[modName] || []
  return Object.entries(allContracts).filter(([name]) =>
    patterns.some(p => name.toLowerCase() === p.toLowerCase())
  )
}

// ── Copy / Explorer buttons ────────────────────────────────────────────────

function CopyBtn({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <button
      onClick={() => { navigator.clipboard.writeText(text); setCopied(true); setTimeout(() => setCopied(false), 1500) }}
      className="p-1 rounded-md hover:bg-white/10 transition-all"
      title="Copy"
    >
      {copied
        ? <span className="text-[10px] text-emerald-300 font-semibold px-0.5">✓</span>
        : <LinkIcon className="w-3 h-3 text-white/25 hover:text-white/60" />
      }
    </button>
  )
}

function ExplorerBtn({ network, address }: { network: string; address: string }) {
  const url = getExplorerUrl(network, address)
  if (!url) return null
  return (
    <a href={url} target="_blank" rel="noopener noreferrer"
      className="p-1 rounded-md hover:bg-white/10 transition-all" title="View on Explorer">
      <ArrowTopRightOnSquareIcon className="w-3 h-3 text-white/25 hover:text-white/60" />
    </a>
  )
}

// ── Main Component ─────────────────────────────────────────────────────────

function ChainHubInner() {
  const [mods, setMods] = useState<ModInfo[]>([])
  const [deployments, setDeployments] = useState<Record<string, NetworkDeployment>>({})
  const [loading, setLoading] = useState(false)
  const [deploying, setDeploying] = useState<string | null>(null)
  const [network, setNetwork] = useState('testnet')
  const [search, setSearch] = useState('')
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [chainData, setChainData] = useState<ChainData>({ blockNumber: null, balance: null })
  const [blockPulse, setBlockPulse] = useState(false)
  const prevBlock = useRef<number | null>(null)

  // ── Fetch API data ──

  const fetchAll = useCallback(async () => {
    setLoading(true)
    try {
      const [modsData, statusData] = await Promise.all([
        apiCall('mods').catch(() => ({ mods: [] })),
        apiCall('status').catch(() => ({ deployments: {} })),
      ])
      setMods(modsData.mods || [])
      setDeployments(statusData.deployments || {})
    } catch {}
    setLoading(false)
  }, [])

  // ── Fetch live chain data via RPC ──

  const fetchChainData = useCallback(async () => {
    const rpc = RPC_URLS[network]
    if (!rpc) return
    try {
      const { JsonRpcProvider } = await import('ethers')
      const provider = new JsonRpcProvider(rpc)
      const blockNumber = await provider.getBlockNumber()

      let balance: string | null = null
      const deployer = deployments[network]?.deployer
      if (deployer) {
        try {
          const bal = await provider.getBalance(deployer)
          balance = bal.toString()
        } catch {}
      }

      if (prevBlock.current !== null && blockNumber > prevBlock.current) {
        setBlockPulse(true)
        setTimeout(() => setBlockPulse(false), 1000)
      }
      prevBlock.current = blockNumber
      setChainData({ blockNumber, balance })
    } catch {}
  }, [network, deployments])

  // ── Effects ──

  useEffect(() => { fetchAll() }, [fetchAll])
  useEffect(() => {
    const iv = setInterval(fetchAll, 12000)
    return () => clearInterval(iv)
  }, [fetchAll])

  useEffect(() => { fetchChainData() }, [fetchChainData])
  useEffect(() => {
    const iv = setInterval(fetchChainData, 6000)
    return () => clearInterval(iv)
  }, [fetchChainData])

  // ── Deploy ──

  const handleDeploy = useCallback(async (modNames?: string[]) => {
    const key = modNames ? modNames[0] : 'all'
    setDeploying(key)
    try {
      await apiCall('deploy', { network, mods: modNames || null }, 'POST')
      toast.success(modNames ? `${modNames[0]} deployed` : 'All contracts deployed')
      fetchAll()
      fetchChainData()
    } catch (err: any) {
      toast.error(err?.message || 'Deploy failed')
    }
    setDeploying(null)
  }, [network, fetchAll, fetchChainData])

  // ── Expand toggle ──

  const toggleExpand = (name: string) => {
    setExpanded(prev => {
      const next = new Set(prev)
      next.has(name) ? next.delete(name) : next.add(name)
      return next
    })
  }

  // ── Derived ──

  const currentDeployment = deployments[network]
  const contracts = currentDeployment?.contracts || {}
  const contractCount = currentDeployment?.contract_count || 0
  const aliveCount = mods.filter(m => m.alive).length
  const explorerBase = EXPLORER_URLS[network]

  const filteredMods = useMemo(() =>
    mods.filter(m => !search || m.name.toLowerCase().includes(search.toLowerCase())),
    [mods, search]
  )

  // ── Render ──

  return (
    <Shell
      active="hub"
      right={
        <>
          <BlockChip block={chainData.blockNumber} pulse={blockPulse} />
          <NetworkSelect value={network} onChange={setNetwork} />
          <RefreshBtn onClick={() => { fetchAll(); fetchChainData() }} loading={loading} />
        </>
      }
      footer={`${CHAIN_NAMES[network]} — chain hub`}
    >
      {/* ═══ Hero ═══ */}
      <div className="fade-up pt-4 pb-2" style={{ '--i': 0 } as any}>
        <h1 className="text-[34px] md:text-[42px] font-semibold tracking-[-0.03em] leading-tight text-white">
          The modular contract{' '}
          <span className="bg-gradient-to-r from-cyan-300 via-sky-300 to-violet-300 bg-clip-text text-transparent">
            ecosystem
          </span>
        </h1>
        <p className="mt-2 text-[14px] text-white/40 max-w-xl">
          Deploy, inspect and operate every protocol module on {CHAIN_NAMES[network]} — from one surface.
        </p>
      </div>

      {/* ═══ Stats ═══ */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        {[
          {
            label: 'Network', value: CHAIN_NAMES[network],
            sub: <span className="tabular-nums">chain id {currentDeployment?.chainId || '—'}</span>,
          },
          {
            label: 'Deployer',
            value: (
              <span className="flex items-center gap-1 font-mono text-[17px]">
                {fmtAddr(currentDeployment?.deployer || '', 4)}
                {currentDeployment?.deployer && <CopyBtn text={currentDeployment.deployer} />}
              </span>
            ),
            sub: explorerBase && currentDeployment?.deployer ? (
              <a href={getExplorerUrl(network, currentDeployment.deployer)} target="_blank" rel="noopener noreferrer"
                className="text-cyan-400/50 hover:text-cyan-300 transition-colors">view on explorer ↗</a>
            ) : 'no deployment',
          },
          {
            label: 'Balance',
            value: <span className="tabular-nums">{fmtBalance(chainData.balance)} <span className="text-[13px] text-white/35 font-medium">ETH</span></span>,
            sub: 'native gas balance',
          },
          {
            label: 'Fleet',
            value: (
              <span className="tabular-nums">
                {contractCount} <span className="text-[13px] text-white/35 font-medium">contracts</span>
              </span>
            ),
            sub: <span><span className="text-emerald-300/90 tabular-nums">{aliveCount}/{mods.length}</span> module APIs live</span>,
          },
        ].map((s, i) => (
          <div key={s.label} className="glass glass-hover p-4 fade-up" style={{ '--i': i + 1 } as any}>
            <p className="label mb-2">{s.label}</p>
            <div className="stat-value text-[20px] md:text-[22px]">{s.value}</div>
            <p className="text-[11px] text-white/30 mt-1.5">{s.sub}</p>
          </div>
        ))}
      </div>

      {/* ═══ Controls ═══ */}
      <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-3 fade-up" style={{ '--i': 5 } as any}>
        <div className="relative flex-1 max-w-sm">
          <MagnifyingGlassIcon className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-white/25" />
          <input
            type="text"
            placeholder="Filter modules…"
            value={search}
            onChange={e => setSearch(e.target.value)}
            className="input !pl-10 !font-sans"
          />
        </div>
        <button
          onClick={() => handleDeploy()}
          disabled={deploying !== null}
          className="btn btn-primary !px-6 !py-2.5"
        >
          {deploying === 'all'
            ? <ArrowPathIcon className="w-4 h-4 animate-spin" />
            : <RocketLaunchIcon className="w-4 h-4" />
          }
          Deploy all
        </button>
      </div>

      {/* ═══ Module Grid ═══ */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
        {filteredMods.map((mod, i) => {
          const colors = MOD_COLORS[mod.name] || defaultColors
          const modContracts = getModuleContracts(mod.name, contracts)
          const isExpanded = expanded.has(mod.name)
          const isDeploying = deploying === mod.name

          return (
            <div
              key={mod.name}
              className="glass glass-hover overflow-hidden fade-up"
              style={{ '--i': Math.min(i + 6, 14) } as any}
            >
              <div className="p-4">
                {/* Name + status */}
                <div className="flex items-center justify-between mb-1.5">
                  <div className="flex items-center gap-2.5">
                    <span
                      className={`dot ${mod.alive ? colors.dot : 'bg-white/10'}`}
                      style={mod.alive ? { boxShadow: `0 0 10px 1px ${colors.glow}` } : undefined}
                    />
                    <span className={`text-[15px] font-semibold tracking-tight ${colors.text}`}>
                      {mod.name}
                    </span>
                  </div>
                  <span className={`chip ${mod.alive ? 'chip-live' : 'chip-off'}`}>
                    {mod.alive ? 'live' : 'off'}
                  </span>
                </div>

                {/* Ports + contract count */}
                <div className="flex items-center gap-3 mb-4 text-[11px] text-white/30 font-mono">
                  <span>:{mod.api_port}</span>
                  <span>:{mod.app_port}</span>
                  {modContracts.length > 0 && (
                    <span className={`${colors.text} opacity-60 font-sans`}>
                      {modContracts.length} contract{modContracts.length !== 1 ? 's' : ''}
                    </span>
                  )}
                </div>

                {/* Action buttons */}
                <div className="flex gap-2">
                  {mod.alive && (
                    <>
                      <a href={`${mod.api_url}/docs`} target="_blank" rel="noopener noreferrer"
                        className="btn btn-ghost flex-1 !py-1.5 !text-[11px]">
                        Docs
                      </a>
                      <a href={mod.app_url} target="_blank" rel="noopener noreferrer"
                        className="btn btn-ghost flex-1 !py-1.5 !text-[11px]">
                        App
                      </a>
                    </>
                  )}
                  <button
                    onClick={() => handleDeploy([mod.name])}
                    disabled={deploying !== null}
                    className="btn btn-ghost flex-1 !py-1.5 !text-[11px] hover:!border-cyan-400/40 hover:!text-cyan-200"
                  >
                    {isDeploying && <ArrowPathIcon className="w-3 h-3 animate-spin" />}
                    Deploy
                  </button>
                </div>
              </div>

              {/* Expandable contracts section */}
              {modContracts.length > 0 && (
                <>
                  <button
                    onClick={() => toggleExpand(mod.name)}
                    className="w-full flex items-center justify-between px-4 py-2 border-t hairline text-[11px] text-white/30 hover:text-white/55 hover:bg-white/[0.02] transition-colors"
                  >
                    <span>Contracts</span>
                    {isExpanded
                      ? <ChevronDownIcon className="w-3 h-3" />
                      : <ChevronRightIcon className="w-3 h-3" />
                    }
                  </button>
                  {isExpanded && (
                    <div className="border-t hairline bg-black/25">
                      {modContracts.map(([name, address]) => (
                        <div key={name} className="row flex items-center justify-between px-4 py-2">
                          <span className="text-[11.5px] text-white/45">{name}</span>
                          <div className="flex items-center gap-1">
                            <span className="text-[11px] text-white/25 font-mono tabular-nums">{fmtAddr(address)}</span>
                            <CopyBtn text={address} />
                            <ExplorerBtn network={network} address={address} />
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </>
              )}
            </div>
          )
        })}
      </div>

      {/* ═══ All Contracts ═══ */}
      {Object.keys(contracts).length > 0 && (
        <div className="glass overflow-hidden fade-up" style={{ '--i': 8 } as any}>
          <div className="flex items-center justify-between px-5 py-4 border-b hairline">
            <div>
              <p className="text-[14px] font-semibold text-white/85 tracking-tight">Deployed contracts</p>
              <p className="text-[11px] text-white/30 mt-0.5">{CHAIN_NAMES[network]}</p>
            </div>
            <span className="stat-value !text-[20px] text-white/60">{Object.keys(contracts).length}</span>
          </div>
          <div className="divide-y divide-white/[0.04]">
            {Object.entries(contracts).map(([name, address]) => (
              <div key={name} className="row group flex items-center justify-between px-5 py-2.5">
                <span className="text-[12.5px] font-medium text-white/55 group-hover:text-white/85 transition-colors">{name}</span>
                <div className="flex items-center gap-2">
                  <span className="text-[12px] text-white/25 font-mono tabular-nums group-hover:text-white/45 transition-colors">
                    {fmtAddr(address, 8)}
                  </span>
                  <CopyBtn text={address} />
                  <ExplorerBtn network={network} address={address} />
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </Shell>
  )
}

export default dynamic(() => Promise.resolve(ChainHubInner), { ssr: false })
