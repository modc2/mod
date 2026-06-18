"use client";

import { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import dynamic from 'next/dynamic'
import { toast } from 'react-toastify'
import {
  CubeTransparentIcon,
  ArrowPathIcon,
  RocketLaunchIcon,
  SignalIcon,
  SignalSlashIcon,
  LinkIcon,
  ServerStackIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  ArrowTopRightOnSquareIcon,
  MagnifyingGlassIcon,
  CommandLineIcon,
  WalletIcon,
  DocumentTextIcon,
} from '@heroicons/react/24/outline'

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
  debit: ['Debit'],
  safe: ['Safe', 'SafeProxy'],
  bridge: ['Bridge'],
}

const MOD_COLORS: Record<string, { text: string; border: string; bg: string; dot: string }> = {
  token:     { text: 'text-emerald-400', border: 'border-emerald-500/20', bg: 'bg-emerald-500/[0.06]', dot: 'bg-emerald-400' },
  oracle:    { text: 'text-amber-400',   border: 'border-amber-500/20',   bg: 'bg-amber-500/[0.06]',   dot: 'bg-amber-400' },
  registry:  { text: 'text-cyan-400',    border: 'border-cyan-500/20',    bg: 'bg-cyan-500/[0.06]',    dot: 'bg-cyan-400' },
  perms:     { text: 'text-violet-400',  border: 'border-violet-500/20',  bg: 'bg-violet-500/[0.06]',  dot: 'bg-violet-400' },
  tokengate: { text: 'text-rose-400',    border: 'border-rose-500/20',    bg: 'bg-rose-500/[0.06]',    dot: 'bg-rose-400' },
  bloctime:  { text: 'text-sky-400',     border: 'border-sky-500/20',     bg: 'bg-sky-500/[0.06]',     dot: 'bg-sky-400' },
  treasury:  { text: 'text-lime-400',    border: 'border-lime-500/20',    bg: 'bg-lime-500/[0.06]',    dot: 'bg-lime-400' },
  market:    { text: 'text-orange-400',  border: 'border-orange-500/20',  bg: 'bg-orange-500/[0.06]',  dot: 'bg-orange-400' },
  debit:     { text: 'text-pink-400',    border: 'border-pink-500/20',    bg: 'bg-pink-500/[0.06]',    dot: 'bg-pink-400' },
  safe:      { text: 'text-teal-400',    border: 'border-teal-500/20',    bg: 'bg-teal-500/[0.06]',    dot: 'bg-teal-400' },
  bridge:    { text: 'text-indigo-400',  border: 'border-indigo-500/20',  bg: 'bg-indigo-500/[0.06]',  dot: 'bg-indigo-400' },
}

const defaultColors = { text: 'text-white/70', border: 'border-white/10', bg: 'bg-white/[0.03]', dot: 'bg-white/40' }

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
  s && s.length > 16 ? `${s.slice(0, chars + 2)}...${s.slice(-chars)}` : (s || '--')

const fmtBalance = (wei: string | null) => {
  if (!wei) return '--'
  try {
    const eth = Number(BigInt(wei)) / 1e18
    return eth.toFixed(4)
  } catch { return '--' }
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

// ── Copy Button ────────────────────────────────────────────────────────────

function CopyBtn({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <button
      onClick={() => { navigator.clipboard.writeText(text); setCopied(true); setTimeout(() => setCopied(false), 1500) }}
      className="p-1 rounded hover:bg-white/10 transition-all"
      title="Copy"
    >
      {copied
        ? <span className="text-[9px] text-emerald-400 font-bold px-0.5">OK</span>
        : <LinkIcon className="w-3 h-3 text-white/20 hover:text-white/40" />
      }
    </button>
  )
}

// ── Explorer Link ──────────────────────────────────────────────────────────

function ExplorerBtn({ network, address }: { network: string; address: string }) {
  const url = getExplorerUrl(network, address)
  if (!url) return null
  return (
    <a href={url} target="_blank" rel="noopener noreferrer"
      className="p-1 rounded hover:bg-white/10 transition-all" title="View on Explorer">
      <ArrowTopRightOnSquareIcon className="w-3 h-3 text-white/20 hover:text-white/40" />
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
    <div className="min-h-screen bg-[#0a0a0f] text-[#e5e5e5] font-mono">
      <div className="relative z-10 p-4 md:p-6 max-w-7xl mx-auto space-y-4">

        {/* ═══ Header ═══ */}
        <div className="flex items-center justify-between border border-white/10 rounded-xl p-4 bg-white/[0.02]">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 flex items-center justify-center rounded-lg bg-gradient-to-br from-cyan-500/20 to-violet-500/20 border border-white/10">
              <CubeTransparentIcon className="w-5 h-5 text-white/80" />
            </div>
            <div>
              <h1 className="text-xl font-bold tracking-wider uppercase">Chain Hub</h1>
              <p className="text-[10px] text-white/30 uppercase tracking-widest">Modular Contract Ecosystem</p>
            </div>
          </div>

          <div className="flex items-center gap-3">
            <a href="/contracts"
              className="hidden sm:flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-white/10 bg-white/[0.04] text-white/50 text-[10px] font-bold uppercase tracking-wider hover:text-white/80 hover:bg-white/[0.08] transition-all">
              <DocumentTextIcon className="w-3.5 h-3.5" /> Contracts
            </a>
            <a href="/protocol"
              className="hidden sm:flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-cyan-500/30 bg-cyan-500/10 text-cyan-300 text-[10px] font-bold uppercase tracking-wider hover:bg-cyan-500/20 transition-all">
              <WalletIcon className="w-3.5 h-3.5" /> Protocol App
            </a>
            {/* Live block */}
            {chainData.blockNumber && (
              <div className="hidden sm:flex items-center gap-2 px-3 py-1.5 rounded-lg bg-white/[0.03] border border-white/[0.06]">
                <div className={`w-1.5 h-1.5 rounded-full bg-emerald-400 ${blockPulse ? 'animate-ping' : 'animate-pulse'}`} />
                <span className="text-[10px] text-white/30 uppercase">Blk</span>
                <span className="text-xs text-emerald-400 tabular-nums font-bold">
                  {chainData.blockNumber.toLocaleString()}
                </span>
              </div>
            )}

            <select
              value={network}
              onChange={e => setNetwork(e.target.value)}
              className="text-xs px-3 py-2 rounded-lg border border-white/10 bg-white/[0.04] text-white/70 focus:outline-none focus:border-white/20 font-mono cursor-pointer"
            >
              <option value="testnet">Base Sepolia</option>
              <option value="ganache">Ganache</option>
              <option value="mainnet">Base Mainnet</option>
            </select>

            <button
              onClick={() => { fetchAll(); fetchChainData() }}
              disabled={loading}
              className="p-2 rounded-lg border border-white/10 bg-white/[0.04] text-white/40 hover:text-white/70 hover:bg-white/[0.08] disabled:opacity-30 transition-colors"
            >
              <ArrowPathIcon className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
            </button>
          </div>
        </div>

        {/* ═══ Chain Info ═══ */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <div className="p-3 rounded-xl border border-white/[0.06] bg-white/[0.02]">
            <p className="text-[9px] font-bold uppercase tracking-wider text-white/25 mb-1">Chain</p>
            <p className="text-sm font-bold text-white/60">{CHAIN_NAMES[network]}</p>
            <p className="text-[10px] text-white/20 tabular-nums mt-0.5">ID {currentDeployment?.chainId || '--'}</p>
          </div>
          <div className="p-3 rounded-xl border border-white/[0.06] bg-white/[0.02]">
            <p className="text-[9px] font-bold uppercase tracking-wider text-white/25 mb-1">Deployer</p>
            <div className="flex items-center gap-1">
              <p className="text-sm font-bold text-white/60 tabular-nums">{fmtAddr(currentDeployment?.deployer || '', 4)}</p>
              {currentDeployment?.deployer && <CopyBtn text={currentDeployment.deployer} />}
            </div>
            {explorerBase && currentDeployment?.deployer && (
              <a href={getExplorerUrl(network, currentDeployment.deployer)} target="_blank" rel="noopener noreferrer"
                className="text-[10px] text-cyan-500/40 hover:text-cyan-400 transition-colors">
                explorer ↗
              </a>
            )}
          </div>
          <div className="p-3 rounded-xl border border-white/[0.06] bg-white/[0.02]">
            <p className="text-[9px] font-bold uppercase tracking-wider text-white/25 mb-1">Balance</p>
            <p className="text-sm font-bold text-white/60 tabular-nums">{fmtBalance(chainData.balance)} ETH</p>
            <p className="text-[10px] text-white/20 mt-0.5">native</p>
          </div>
          <div className="p-3 rounded-xl border border-white/[0.06] bg-white/[0.02]">
            <p className="text-[9px] font-bold uppercase tracking-wider text-white/25 mb-1">Status</p>
            <div className="flex items-baseline gap-2">
              <span className="text-sm font-bold text-emerald-400 tabular-nums">{contractCount}</span>
              <span className="text-[10px] text-white/20">contracts</span>
            </div>
            <div className="flex items-baseline gap-2 mt-0.5">
              <span className="text-[10px] text-amber-400 tabular-nums font-bold">{aliveCount}/{mods.length}</span>
              <span className="text-[10px] text-white/20">APIs live</span>
            </div>
          </div>
        </div>

        {/* ═══ Controls ═══ */}
        <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-3">
          <div className="relative flex-1 max-w-xs">
            <MagnifyingGlassIcon className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-white/20" />
            <input
              type="text"
              placeholder="Filter modules..."
              value={search}
              onChange={e => setSearch(e.target.value)}
              className="w-full pl-9 pr-3 py-2 text-xs rounded-lg border border-white/10 bg-white/[0.03] text-white/70 placeholder:text-white/20 focus:outline-none focus:border-white/20 font-mono"
            />
          </div>
          <button
            onClick={() => handleDeploy()}
            disabled={deploying !== null}
            className="px-5 py-2 rounded-lg border border-cyan-500/30 bg-cyan-500/10 text-cyan-300 text-[10px] font-bold uppercase tracking-wider hover:bg-cyan-500/20 disabled:opacity-30 transition-all flex items-center justify-center gap-2"
          >
            {deploying === 'all'
              ? <ArrowPathIcon className="w-3.5 h-3.5 animate-spin" />
              : <RocketLaunchIcon className="w-3.5 h-3.5" />
            }
            Deploy All
          </button>
        </div>

        {/* ═══ Module Grid ═══ */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
          {filteredMods.map((mod) => {
            const colors = MOD_COLORS[mod.name] || defaultColors
            const modContracts = getModuleContracts(mod.name, contracts)
            const isExpanded = expanded.has(mod.name)
            const isDeploying = deploying === mod.name

            return (
              <div
                key={mod.name}
                className={`border ${colors.border} rounded-xl ${colors.bg} hover:brightness-125 transition-all overflow-hidden`}
              >
                <div className="p-4">
                  {/* Name + status */}
                  <div className="flex items-center justify-between mb-2">
                    <div className="flex items-center gap-2">
                      <div className={`w-2 h-2 rounded-full ${mod.alive ? colors.dot : 'bg-white/10'} ${mod.alive ? 'animate-pulse' : ''}`} />
                      <span className={`text-sm font-bold uppercase tracking-wider ${colors.text}`}>
                        {mod.name}
                      </span>
                    </div>
                    <span className={`text-[9px] uppercase tracking-wider px-2 py-0.5 rounded-full ${
                      mod.alive ? 'bg-emerald-500/10 text-emerald-400/80' : 'bg-white/5 text-white/20'
                    }`}>
                      {mod.alive ? 'Live' : 'Off'}
                    </span>
                  </div>

                  {/* Ports + contract count */}
                  <div className="flex items-center gap-4 mb-3 text-[10px] text-white/25">
                    <span>:{mod.api_port}</span>
                    <span>:{mod.app_port}</span>
                    {modContracts.length > 0 && (
                      <span className={`${colors.text} opacity-50`}>
                        {modContracts.length} contract{modContracts.length !== 1 ? 's' : ''}
                      </span>
                    )}
                  </div>

                  {/* Action buttons */}
                  <div className="flex gap-2">
                    {mod.alive && (
                      <>
                        <a href={`${mod.api_url}/docs`} target="_blank" rel="noopener noreferrer"
                          className="flex-1 text-center py-1.5 rounded-lg border border-white/[0.08] bg-white/[0.03] text-[9px] font-bold uppercase tracking-wider text-white/35 hover:text-white/60 hover:bg-white/[0.06] transition-colors">
                          Docs
                        </a>
                        <a href={mod.app_url} target="_blank" rel="noopener noreferrer"
                          className="flex-1 text-center py-1.5 rounded-lg border border-white/[0.08] bg-white/[0.03] text-[9px] font-bold uppercase tracking-wider text-white/35 hover:text-white/60 hover:bg-white/[0.06] transition-colors">
                          App
                        </a>
                      </>
                    )}
                    <button
                      onClick={() => handleDeploy([mod.name])}
                      disabled={deploying !== null}
                      className={`flex-1 py-1.5 rounded-lg border ${colors.border} bg-white/[0.02] text-[9px] font-bold uppercase tracking-wider ${colors.text} opacity-50 hover:opacity-100 disabled:opacity-20 transition-all flex items-center justify-center gap-1`}
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
                      className="w-full flex items-center justify-between px-4 py-2 border-t border-white/[0.04] text-[9px] uppercase tracking-wider text-white/20 hover:text-white/35 hover:bg-white/[0.02] transition-colors"
                    >
                      <span>Contracts</span>
                      {isExpanded
                        ? <ChevronDownIcon className="w-3 h-3" />
                        : <ChevronRightIcon className="w-3 h-3" />
                      }
                    </button>
                    {isExpanded && (
                      <div className="border-t border-white/[0.04] bg-black/20">
                        {modContracts.map(([name, address]) => (
                          <div key={name} className="flex items-center justify-between px-4 py-2 hover:bg-white/[0.02]">
                            <span className="text-[10px] text-white/35">{name}</span>
                            <div className="flex items-center gap-1">
                              <span className="text-[10px] text-white/20 tabular-nums">{fmtAddr(address)}</span>
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
          <div className="border border-white/[0.08] rounded-xl bg-white/[0.015] overflow-hidden">
            <div className="flex items-center justify-between p-4 border-b border-white/[0.05]">
              <div className="flex items-center gap-2">
                <CommandLineIcon className="w-4 h-4 text-white/20" />
                <span className="text-[10px] font-bold uppercase tracking-wider text-white/30">
                  Deployed Contracts — {CHAIN_NAMES[network]}
                </span>
              </div>
              <span className="text-[10px] text-white/15 tabular-nums">{Object.keys(contracts).length}</span>
            </div>
            <div className="divide-y divide-white/[0.03]">
              {Object.entries(contracts).map(([name, address]) => (
                <div key={name} className="flex items-center justify-between px-4 py-2.5 hover:bg-white/[0.02] transition-colors group">
                  <span className="text-xs font-bold text-white/45 group-hover:text-white/65 transition-colors">{name}</span>
                  <div className="flex items-center gap-2">
                    <span className="text-xs text-white/20 font-mono tabular-nums group-hover:text-white/30 transition-colors">
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

        {/* ═══ Footer ═══ */}
        <div className="text-center text-[10px] text-white/10 uppercase tracking-widest py-6">
          Chain Hub — mod
        </div>
      </div>
    </div>
  )
}

export default dynamic(() => Promise.resolve(ChainHubInner), { ssr: false })
