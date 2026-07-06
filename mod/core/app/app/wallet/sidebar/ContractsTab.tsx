"use client"

import { useState, useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { ethers } from 'ethers'
import { ArrowPathIcon } from '@heroicons/react/24/outline'
import { CopyButton } from '@/ui/CopyButton'
import { toast } from 'react-toastify'
import { getChainConfig } from '@/network/chainConfig'

// ABI imports
import TreasuryABI from '@/contracts/treasury/Treasury.sol/Treasury.json'
import MarketABI from '@/contracts/market/Market.sol/Market.json'
import DebitABI from '@/contracts/market/debit/Debit.sol/Debit.json'
import RegistryABI from '@/contracts/registry/Registry.sol/Registry.json'
import ManualPriceOracleABI from '@/contracts/oracles/ManualPriceOracle.sol/ManualPriceOracle.json'
import TokenGateABI from '@/contracts/tokengate/TokenGate.sol/TokenGate.json'
import BlocTimeABI from '@/contracts/bloctime/BlocTime.sol/BlocTime.json'
import TokenABI from '@/contracts/token/Token.sol/Token.json'

const CONTRACT_ABIS: Record<string, any[]> = {
  Treasury: TreasuryABI.abi,
  Market: MarketABI.abi,
  Debit: DebitABI.abi,
  Registry: RegistryABI.abi,
  ManualPriceOracle: ManualPriceOracleABI.abi,
  TokenGate: TokenGateABI.abi,
  BlocTime: BlocTimeABI.abi,
  NativeToken: TokenABI.abi,
  USDC: TokenABI.abi,
  USDT: TokenABI.abi,
}

interface FnInfo {
  name: string
  inputs: any[]
  outputs: any[]
  stateMutability: string
  description?: string
}

function getContracts(): { name: string; address: string }[] {
  const chainConfig = getChainConfig()
  if (!chainConfig?.contracts) return []
  const entries = Object.entries(chainConfig.contracts) as [string, any][]
  const treasury = entries.find(([name]) => name === 'Treasury')
  const rest = entries
    .filter(([name]) => name !== 'Treasury' && name !== 'Safe')
    .sort(([a], [b]) => a.localeCompare(b))
  const ordered = treasury ? [treasury, ...rest] : rest
  return ordered.map(([name, val]) => ({ name, address: val.address }))
}

function getFunctions(abi: any[], type: 'read' | 'write'): FnInfo[] {
  return abi
    .filter((item) => {
      if (item.type !== 'function') return false
      if (type === 'read') return item.stateMutability === 'view' || item.stateMutability === 'pure'
      return item.stateMutability !== 'view' && item.stateMutability !== 'pure'
    })
    .map((item) => ({
      name: item.name,
      inputs: item.inputs || [],
      outputs: item.outputs || [],
      stateMutability: item.stateMutability,
      description: item.devdoc?.details || item.userdoc?.notice || undefined,
    }))
    .sort((a, b) => a.name.localeCompare(b.name))
}

function shorten(addr: string, start = 6, end = 4) {
  if (!addr || addr.length < start + end + 2) return addr
  return `${addr.slice(0, start)}...${addr.slice(-end)}`
}

function formatSig(fn: FnInfo): string {
  const params = fn.inputs.map((inp: any) => `${inp.type}${inp.name ? ' ' + inp.name : ''}`).join(', ')
  const ret = fn.outputs.length > 0 ? ` -> ${fn.outputs.map((o: any) => o.type).join(', ')}` : ''
  return `${fn.name}(${params})${ret}`
}

const inputClass = 'w-full bg-black/80 border border-cyan-500/20 rounded px-3 py-2 text-sm text-cyan-100 font-mono placeholder-cyan-500/20 focus:border-cyan-400/60 focus:shadow-[0_0_8px_rgba(0,255,255,0.15)] focus:outline-none transition-all'
const selectClass = 'w-full bg-black/80 border border-cyan-500/20 rounded px-3 py-2 text-sm text-cyan-100 font-mono focus:border-cyan-400/60 focus:shadow-[0_0_8px_rgba(0,255,255,0.15)] focus:outline-none transition-all appearance-none cursor-pointer'

interface ContractsTabProps {
  show: boolean
}

export function ContractsTab({ show }: ContractsTabProps) {
  const [selectedContract, setSelectedContract] = useState('')
  const [search, setSearch] = useState('')
  const [fnSearch, setFnSearch] = useState('')
  const [mode, setMode] = useState<'read' | 'write'>('read')

  // Write state
  const [selectedFunction, setSelectedFunction] = useState('')
  const [fnArgs, setFnArgs] = useState<string[]>([])
  const [ethValue, setEthValue] = useState('')
  const [sending, setSending] = useState(false)

  // Read state
  const [selectedReadFunction, setSelectedReadFunction] = useState('')
  const [readArgs, setReadArgs] = useState<string[]>([])
  const [readResult, setReadResult] = useState<string | null>(null)
  const [reading, setReading] = useState(false)

  const contracts = getContracts()
  const filteredContracts = search
    ? contracts.filter((c) => c.name.toLowerCase().includes(search.toLowerCase()))
    : contracts
  const contractInfo = contracts.find((c) => c.name === selectedContract)
  const abi = selectedContract ? CONTRACT_ABIS[selectedContract] : null
  const functions = abi ? getFunctions(abi, mode) : []
  const filteredFunctions = fnSearch
    ? functions.filter((f) => f.name.toLowerCase().includes(fnSearch.toLowerCase()))
    : functions

  const activeFnName = mode === 'read' ? selectedReadFunction : selectedFunction
  const activeFn = functions.find((f) => f.name === activeFnName)

  useEffect(() => {
    setSelectedFunction('')
    setFnArgs([])
    setSelectedReadFunction('')
    setReadArgs([])
    setReadResult(null)
    setFnSearch('')
  }, [selectedContract])

  useEffect(() => {
    setFnSearch('')
  }, [mode])

  useEffect(() => {
    if (activeFn) {
      const newArgs = new Array(activeFn.inputs.length).fill('')
      if (mode === 'read') setReadArgs(newArgs)
      else setFnArgs(newArgs)
    } else {
      if (mode === 'read') setReadArgs([])
      else setFnArgs([])
    }
    setReadResult(null)
  }, [selectedFunction, selectedReadFunction]) // eslint-disable-line react-hooks/exhaustive-deps

  function selectFn(name: string) {
    if (mode === 'read') {
      setSelectedReadFunction(selectedReadFunction === name ? '' : name)
    } else {
      setSelectedFunction(selectedFunction === name ? '' : name)
    }
  }

  async function handleRead() {
    if (!contractInfo || !activeFn || !abi) return
    setReading(true)
    setReadResult(null)
    try {
      if (!window.ethereum) throw new Error('No wallet connected')
      const provider = new ethers.BrowserProvider(window.ethereum)
      const contract = new ethers.Contract(contractInfo.address, abi, provider)
      const result = await contract[activeFn.name](...readArgs)
      if (Array.isArray(result)) {
        const formatted = result.map((v: any, i: number) => {
          const outputName = activeFn.outputs[i]?.name || `[${i}]`
          return `${outputName}: ${v.toString()}`
        })
        setReadResult(formatted.join('\n'))
      } else {
        setReadResult(result.toString())
      }
    } catch (err: any) {
      console.error(err)
      setReadResult(`Error: ${err?.message || 'Read failed'}`)
    } finally {
      setReading(false)
    }
  }

  async function handleWrite() {
    if (!contractInfo || !activeFn || !abi) return
    setSending(true)
    try {
      if (!window.ethereum) throw new Error('No wallet connected')
      const provider = new ethers.BrowserProvider(window.ethereum)
      const signer = await provider.getSigner()
      const contract = new ethers.Contract(contractInfo.address, abi, signer)
      const value = ethValue ? ethers.parseEther(ethValue) : BigInt(0)
      const tx = await contract[activeFn.name](...fnArgs, { value })
      toast.success(`TX sent: ${tx.hash.slice(0, 12)}...`)
      await tx.wait()
      toast.success('TX confirmed')
      setSelectedFunction('')
      setFnArgs([])
      setEthValue('')
    } catch (err: any) {
      console.error(err)
      toast.error(err?.reason || err?.message || 'Transaction failed')
    } finally {
      setSending(false)
    }
  }

  const isRead = mode === 'read'
  const accentColor = isRead ? 'emerald' : 'amber'

  return (
    <AnimatePresence>
    {show && (
    <motion.div
      initial={{ height: 0, opacity: 0 }}
      animate={{ height: 'auto', opacity: 1 }}
      exit={{ height: 0, opacity: 0 }}
      transition={{ duration: 0.35, ease: [0.25, 0.1, 0.25, 1] }}
      className="mt-5 overflow-hidden"
    >
      <div className="border-t border-cyan-500/10 pt-5">
        {/* Header */}
        <div className="flex items-center gap-3 mb-4">
          <div className="flex items-center gap-2">
            <div className="w-1.5 h-1.5 bg-cyan-400 rounded-full shadow-[0_0_6px_rgba(0,255,255,0.8)] animate-pulse" />
            <h3 className="text-[11px] font-bold uppercase tracking-[0.2em] text-cyan-400/90 font-mono">Contracts</h3>
          </div>
          <div className="flex-1 h-px bg-gradient-to-r from-cyan-500/30 to-transparent" />
          <span className="text-[9px] font-mono text-cyan-500/30">[{contracts.length}]</span>
        </div>

        {/* Search contracts */}
        <div className="mb-3 relative">
          <div className="absolute left-3 top-1/2 -translate-y-1/2 text-[9px] font-mono text-cyan-500/30">{'>'}_</div>
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search contracts..."
            className={`${inputClass} pl-8`}
          />
        </div>

        {/* Contract Grid */}
        <div className="grid grid-cols-3 gap-1.5 mb-4">
          {filteredContracts.map((c) => (
            <button
              key={c.name}
              onClick={() => setSelectedContract(selectedContract === c.name ? '' : c.name)}
              className={`group relative p-2.5 rounded border text-left transition-all duration-200 overflow-hidden ${
                selectedContract === c.name
                  ? 'bg-cyan-500/10 border-cyan-400/50 shadow-[0_0_12px_rgba(0,255,255,0.1),inset_0_0_20px_rgba(0,255,255,0.05)]'
                  : 'bg-black/40 border-cyan-500/10 hover:bg-cyan-500/5 hover:border-cyan-500/30 hover:shadow-[0_0_8px_rgba(0,255,255,0.08)]'
              }`}
            >
              <div className={`absolute top-0 left-0 w-2 h-px transition-colors ${selectedContract === c.name ? 'bg-cyan-400' : 'bg-cyan-500/20 group-hover:bg-cyan-500/40'}`} />
              <div className={`absolute top-0 left-0 w-px h-2 transition-colors ${selectedContract === c.name ? 'bg-cyan-400' : 'bg-cyan-500/20 group-hover:bg-cyan-500/40'}`} />
              <div className={`absolute bottom-0 right-0 w-2 h-px transition-colors ${selectedContract === c.name ? 'bg-cyan-400' : 'bg-cyan-500/20 group-hover:bg-cyan-500/40'}`} />
              <div className={`absolute bottom-0 right-0 w-px h-2 transition-colors ${selectedContract === c.name ? 'bg-cyan-400' : 'bg-cyan-500/20 group-hover:bg-cyan-500/40'}`} />
              <div className="flex items-center gap-1.5">
                <div className={`w-1 h-1 rounded-full transition-all ${
                  selectedContract === c.name ? 'bg-cyan-400 shadow-[0_0_4px_rgba(0,255,255,0.8)]' : 'bg-cyan-500/20 group-hover:bg-cyan-500/40'
                }`} />
                <div className={`text-[10px] font-bold truncate font-mono tracking-wide ${
                  selectedContract === c.name ? 'text-cyan-300' : 'text-white/60 group-hover:text-cyan-200/70'
                }`}>{c.name}</div>
              </div>
              <div className={`text-[8px] font-mono mt-1 ml-2.5 truncate transition-colors ${
                selectedContract === c.name ? 'text-cyan-500/50' : 'text-white/15 group-hover:text-cyan-500/30'
              }`}>{shorten(c.address, 4, 3)}</div>
            </button>
          ))}
          {filteredContracts.length === 0 && (
            <div className="col-span-3 text-center text-cyan-500/20 text-[10px] font-mono py-4 tracking-wider">-- NO CONTRACTS FOUND --</div>
          )}
        </div>

        {/* Selected contract info bar */}
        {contractInfo && (
          <div className="flex items-center gap-2 mb-4 px-2 py-2 bg-cyan-500/5 border border-cyan-500/10 rounded">
            <span className="text-[10px] font-bold font-mono text-cyan-300 tracking-wide">{selectedContract}</span>
            <span className="text-[9px] font-mono text-cyan-500/30">{shorten(contractInfo.address)}</span>
            <CopyButton text={contractInfo.address} size="sm" />
            <div className="flex-1" />
            {/* Read / Write toggle inline */}
            <div className="flex gap-px p-0.5 bg-black/60 border border-cyan-500/15 rounded">
              <button
                onClick={() => setMode('read')}
                className={`px-3 py-1 text-[9px] font-bold uppercase tracking-[0.15em] font-mono rounded-sm transition-all ${
                  mode === 'read'
                    ? 'bg-emerald-500/15 text-emerald-400 border border-emerald-500/30 shadow-[0_0_8px_rgba(16,185,129,0.1)]'
                    : 'text-white/25 hover:text-emerald-400/50 border border-transparent'
                }`}
              >Read</button>
              <button
                onClick={() => setMode('write')}
                className={`px-3 py-1 text-[9px] font-bold uppercase tracking-[0.15em] font-mono rounded-sm transition-all ${
                  mode === 'write'
                    ? 'bg-amber-500/15 text-amber-400 border border-amber-500/30 shadow-[0_0_8px_rgba(245,158,11,0.1)]'
                    : 'text-white/25 hover:text-amber-400/50 border border-transparent'
                }`}
              >Write</button>
            </div>
          </div>
        )}

        {/* Functions section */}
        {selectedContract && (
          <div className="space-y-3">
            {/* Function search */}
            <div className="relative">
              <div className="absolute left-3 top-1/2 -translate-y-1/2 text-[9px] font-mono text-cyan-500/30">fn_</div>
              <input
                type="text"
                value={fnSearch}
                onChange={(e) => setFnSearch(e.target.value)}
                placeholder={`Search ${mode} functions...`}
                className={`${inputClass} pl-9`}
              />
              <div className="absolute right-3 top-1/2 -translate-y-1/2 text-[9px] font-mono text-cyan-500/20">
                {filteredFunctions.length}/{functions.length}
              </div>
            </div>

            {/* Function cards grid */}
            {filteredFunctions.length > 0 ? (
              <div className="grid grid-cols-2 gap-1.5 max-h-[280px] overflow-y-auto pr-0.5 scrollbar-thin">
                {filteredFunctions.map((fn) => {
                  const isActive = fn.name === activeFnName
                  return (
                    <button
                      key={fn.name}
                      onClick={() => selectFn(fn.name)}
                      className={`group relative p-2.5 rounded border text-left transition-all duration-200 overflow-hidden ${
                        isActive
                          ? isRead
                            ? 'bg-emerald-500/10 border-emerald-500/40 shadow-[0_0_10px_rgba(16,185,129,0.1)]'
                            : 'bg-amber-500/10 border-amber-500/40 shadow-[0_0_10px_rgba(245,158,11,0.1)]'
                          : 'bg-black/40 border-cyan-500/8 hover:border-cyan-500/25 hover:bg-cyan-500/5'
                      }`}
                    >
                      {/* Corner accents */}
                      <div className={`absolute top-0 left-0 w-1.5 h-px ${isActive ? (isRead ? 'bg-emerald-400' : 'bg-amber-400') : 'bg-cyan-500/15 group-hover:bg-cyan-500/30'}`} />
                      <div className={`absolute top-0 left-0 w-px h-1.5 ${isActive ? (isRead ? 'bg-emerald-400' : 'bg-amber-400') : 'bg-cyan-500/15 group-hover:bg-cyan-500/30'}`} />
                      <div className={`absolute bottom-0 right-0 w-1.5 h-px ${isActive ? (isRead ? 'bg-emerald-400' : 'bg-amber-400') : 'bg-cyan-500/15 group-hover:bg-cyan-500/30'}`} />
                      <div className={`absolute bottom-0 right-0 w-px h-1.5 ${isActive ? (isRead ? 'bg-emerald-400' : 'bg-amber-400') : 'bg-cyan-500/15 group-hover:bg-cyan-500/30'}`} />

                      {/* Function name */}
                      <div className="flex items-center gap-1.5 mb-1">
                        <div className={`w-1 h-1 rounded-full ${
                          isActive
                            ? isRead ? 'bg-emerald-400 shadow-[0_0_4px_rgba(16,185,129,0.8)]' : 'bg-amber-400 shadow-[0_0_4px_rgba(245,158,11,0.8)]'
                            : 'bg-cyan-500/20 group-hover:bg-cyan-500/40'
                        }`} />
                        <span className={`text-[10px] font-bold font-mono truncate ${
                          isActive
                            ? isRead ? 'text-emerald-300' : 'text-amber-300'
                            : 'text-white/60 group-hover:text-cyan-200/70'
                        }`}>{fn.name}</span>
                      </div>

                      {/* Schema: inputs */}
                      {fn.inputs.length > 0 && (
                        <div className="ml-2.5 mb-0.5">
                          <div className="text-[7px] font-mono text-cyan-500/25 uppercase tracking-wider mb-0.5">in</div>
                          {fn.inputs.map((inp: any, i: number) => (
                            <div key={i} className="text-[8px] font-mono text-cyan-500/35 truncate">
                              {inp.name || `_${i}`}<span className="text-cyan-500/20">::{inp.type}</span>
                            </div>
                          ))}
                        </div>
                      )}

                      {/* Schema: outputs */}
                      {fn.outputs.length > 0 && (
                        <div className="ml-2.5">
                          <div className="text-[7px] font-mono text-cyan-500/25 uppercase tracking-wider mb-0.5">out</div>
                          {fn.outputs.map((out: any, i: number) => (
                            <div key={i} className="text-[8px] font-mono text-cyan-500/35 truncate">
                              {out.name || `_${i}`}<span className="text-cyan-500/20">::{out.type}</span>
                            </div>
                          ))}
                        </div>
                      )}

                      {/* No params indicator */}
                      {fn.inputs.length === 0 && fn.outputs.length === 0 && (
                        <div className="ml-2.5 text-[8px] font-mono text-cyan-500/20">{'() -> void'}</div>
                      )}

                      {/* Mutability badge */}
                      <div className="mt-1.5 ml-2.5">
                        <span className={`text-[7px] font-mono px-1.5 py-0.5 rounded border ${
                          fn.stateMutability === 'view' ? 'text-emerald-500/50 border-emerald-500/15 bg-emerald-500/5' :
                          fn.stateMutability === 'pure' ? 'text-blue-400/50 border-blue-400/15 bg-blue-400/5' :
                          fn.stateMutability === 'payable' ? 'text-amber-400/50 border-amber-400/15 bg-amber-400/5' :
                          'text-white/25 border-white/8 bg-white/3'
                        }`}>{fn.stateMutability}</span>
                      </div>

                      {/* Description if available */}
                      {fn.description && (
                        <div className="mt-1.5 ml-2.5 text-[8px] font-mono text-white/20 truncate">{fn.description}</div>
                      )}
                    </button>
                  )
                })}
              </div>
            ) : (
              <div className="text-center text-cyan-500/20 text-[10px] font-mono py-4 tracking-wider">
                {functions.length === 0 ? `-- NO ${mode.toUpperCase()} FUNCTIONS --` : '-- NO MATCHES --'}
              </div>
            )}

            {/* Active function execution area */}
            {activeFn && (
              <div className={`p-3 rounded border relative overflow-hidden ${
                isRead
                  ? 'bg-emerald-500/5 border-emerald-500/20'
                  : 'bg-amber-500/5 border-amber-500/20'
              }`}>
                <div className={`absolute inset-0 bg-gradient-to-b ${isRead ? 'from-emerald-500/5' : 'from-amber-500/5'} to-transparent pointer-events-none`} />

                {/* Function header */}
                <div className="flex items-center gap-2 mb-3 relative">
                  <span className={`text-[9px] font-mono font-bold uppercase tracking-[0.15em] ${isRead ? 'text-emerald-500/60' : 'text-amber-500/60'}`}>
                    {'>'} {activeFn.name}
                  </span>
                  <div className={`flex-1 h-px ${isRead ? 'bg-emerald-500/10' : 'bg-amber-500/10'}`} />
                </div>

                {/* Signature display */}
                <div className="mb-3 relative">
                  <pre className={`text-[9px] font-mono ${isRead ? 'text-emerald-400/40' : 'text-amber-400/40'} break-all whitespace-pre-wrap`}>
                    {formatSig(activeFn)}
                  </pre>
                </div>

                {/* Args input */}
                <div className="relative">
                  <FnArgsInput
                    inputs={activeFn.inputs}
                    args={mode === 'read' ? readArgs : fnArgs}
                    setArgs={mode === 'read' ? setReadArgs : setFnArgs}
                  />
                </div>

                {/* ETH value for write */}
                {mode === 'write' && (
                  <div className="mt-2 relative">
                    <label className="text-[9px] font-mono text-cyan-500/40 mb-0.5 block tracking-wide">ETH_VALUE <span className="text-cyan-500/20">::optional</span></label>
                    <input
                      type="text"
                      value={ethValue}
                      onChange={(e) => setEthValue(e.target.value)}
                      placeholder="0"
                      className={inputClass}
                    />
                  </div>
                )}

                {/* Execute button */}
                <button
                  onClick={isRead ? handleRead : handleWrite}
                  disabled={isRead ? reading : sending}
                  className={`w-full mt-3 py-2 rounded text-[11px] font-bold font-mono uppercase tracking-wider transition-all disabled:opacity-40 flex items-center justify-center gap-2 relative ${
                    isRead
                      ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/30 hover:bg-emerald-500/20 hover:shadow-[0_0_12px_rgba(16,185,129,0.15)]'
                      : 'bg-amber-500/10 text-amber-400 border border-amber-500/30 hover:bg-amber-500/20 hover:shadow-[0_0_12px_rgba(245,158,11,0.15)]'
                  }`}
                >
                  {(isRead ? reading : sending)
                    ? <ArrowPathIcon className="w-4 h-4 animate-spin" />
                    : isRead ? '// EXECUTE CALL' : '// SEND TX'
                  }
                </button>

                {/* Read result */}
                {isRead && readResult !== null && (
                  <div className="mt-3 p-3 bg-black/60 border border-emerald-500/20 rounded relative overflow-hidden">
                    <div className="absolute inset-0 bg-gradient-to-b from-emerald-500/5 to-transparent pointer-events-none" />
                    <div className="flex items-center justify-between mb-1.5 relative">
                      <span className="text-[9px] font-bold uppercase tracking-[0.15em] font-mono text-emerald-500/60">{'>'} OUTPUT</span>
                      <CopyButton text={readResult} size="sm" />
                    </div>
                    <pre className="text-[11px] font-mono text-emerald-300/80 whitespace-pre-wrap break-all relative">{readResult}</pre>
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </motion.div>
    )}
    </AnimatePresence>
  )
}

function FnArgsInput({ inputs, args, setArgs }: {
  inputs?: any[]
  args: string[]
  setArgs: (a: string[]) => void
}) {
  if (!inputs || inputs.length === 0) return null
  return (
    <div className="space-y-2">
      {inputs.map((inp: any, i: number) => (
        <div key={i}>
          <label className="text-[9px] font-mono text-cyan-500/40 mb-0.5 block tracking-wide">
            {inp.name || `arg${i}`} <span className="text-cyan-500/20">::{inp.type}</span>
          </label>
          {inp.type === 'bool' ? (
            <select
              value={args[i] || ''}
              onChange={(e) => { const next = [...args]; next[i] = e.target.value; setArgs(next) }}
              className={selectClass}
            >
              <option value="">Select...</option>
              <option value="true">true</option>
              <option value="false">false</option>
            </select>
          ) : (
            <input
              type="text"
              value={args[i] || ''}
              onChange={(e) => { const next = [...args]; next[i] = e.target.value; setArgs(next) }}
              placeholder={inp.type}
              className={inputClass}
            />
          )}
        </div>
      ))}
    </div>
  )
}
