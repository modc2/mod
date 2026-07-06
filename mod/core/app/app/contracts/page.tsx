"use client"

import { useState, useEffect, useRef } from 'react'
import { ethers } from 'ethers'
import { ArrowPathIcon, MagnifyingGlassIcon } from '@heroicons/react/24/outline'
import { CopyButton } from '@/ui/CopyButton'
import { toast } from 'react-toastify'
import modConfig from '@config'
import { text2color, colorWithOpacity } from '@/utils'
import { motion, AnimatePresence } from 'framer-motion'
import { CodeViewer } from './CodeViewer'

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
  kind: 'read' | 'write'
}

interface ExecutionResult {
  fn: string
  params: any
  result: any
  status: 'success' | 'error'
  time: number
  cost?: number
  delta?: number
  hash?: string
}

interface CustomContract {
  name: string
  address: string
  abi: any[]
  abiCid?: string
}

// Available networks from config
const NETWORKS = Object.entries((modConfig.chain as any) || {}).map(([key, val]: [string, any]) => ({
  key,
  chainId: val.chainId,
  label: key === 'testnet' ? 'Base Sepolia' : key === 'localhost' ? 'Localhost' : key === 'ganache' ? 'Ganache' : key,
}))

function getContracts(network: string, customContracts: CustomContract[]): { name: string; address: string; abiCid?: string }[] {
  const chainConfig = (modConfig.chain as any)?.[network]
  const baseContracts = chainConfig?.contracts ? (() => {
    const entries = Object.entries(chainConfig.contracts) as [string, any][]
    const treasury = entries.find(([name]) => name === 'Treasury')
    const rest = entries
      .filter(([name]) => name !== 'Treasury' && name !== 'Safe')
      .sort(([a], [b]) => a.localeCompare(b))
    const ordered = treasury ? [treasury, ...rest] : rest
    return ordered.map(([name, val]) => ({ name, address: val.address, abiCid: val.abi }))
  })() : []

  const custom = customContracts.map(c => ({ name: c.name, address: c.address, abiCid: c.abiCid }))
  return [...baseContracts, ...custom]
}

function getAllFunctions(abi: any[]): FnInfo[] {
  return abi
    .filter((item) => item.type === 'function')
    .map((item) => ({
      name: item.name,
      inputs: item.inputs || [],
      outputs: item.outputs || [],
      stateMutability: item.stateMutability,
      kind: (item.stateMutability === 'view' || item.stateMutability === 'pure' ? 'read' : 'write') as 'read' | 'write',
    }))
    .sort((a, b) => a.name.localeCompare(b.name))
}

function shorten(addr: string, start = 6, end = 4) {
  if (!addr || addr.length < start + end + 2) return addr
  return `${addr.slice(0, start)}...${addr.slice(-end)}`
}

function FnArgsInput({ inputs, args, setArgs }: {
  inputs?: any[]
  args: string[]
  setArgs: (a: string[]) => void
}) {
  if (!inputs || inputs.length === 0) return null
  return (
    <div className="space-y-3">
      {inputs.map((inp: any, i: number) => (
        <div key={i}>
          <label className="text-[12px] font-mono mb-1.5 block font-semibold" style={{ color: 'var(--text-secondary)' }}>
            {inp.name || `arg${i}`} <span style={{ color: 'var(--text-tertiary)' }}>:: {inp.type}</span>
          </label>
          {inp.type === 'bool' ? (
            <select
              value={args[i] || ''}
              onChange={(e) => { const next = [...args]; next[i] = e.target.value; setArgs(next) }}
              className="w-full px-4 py-2.5 rounded-lg text-[14px] font-mono focus:outline-none focus:ring-2 focus:ring-cyan-500/30 transition-all appearance-none cursor-pointer"
              style={{ backgroundColor: 'var(--bg-input)', border: '1px solid var(--border-color)', color: 'var(--text-primary)' }}
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
              className="w-full px-4 py-2.5 rounded-lg text-[14px] font-mono focus:outline-none focus:ring-2 focus:ring-cyan-500/30 transition-all"
              style={{ backgroundColor: 'var(--bg-input)', border: '1px solid var(--border-color)', color: 'var(--text-primary)' }}
            />
          )}
        </div>
      ))}
    </div>
  )
}

export default function ContractsPage() {
  const [selectedNetwork, setSelectedNetwork] = useState(() => {
    if (typeof window !== 'undefined') return localStorage.getItem('contracts_network') || 'testnet'
    return 'testnet'
  })
  const [selectedContract, setSelectedContract] = useState('Market')
  // 'interact' = read/write function explorer · 'code' = Solidity source view.
  const [viewMode, setViewMode] = useState<'interact' | 'code'>('interact')
  const [search, setSearch] = useState('')
  const [showRead, setShowRead] = useState(true)
  const [showWrite, setShowWrite] = useState(true)
  const [fnSearch, setFnSearch] = useState('')

  const [selectedFnName, setSelectedFnName] = useState('')
  const [fnArgs, setFnArgs] = useState<string[]>([])
  const [ethValue, setEthValue] = useState('')
  const [sending, setSending] = useState(false)
  const [readResult, setReadResult] = useState<string | null>(null)
  const [reading, setReading] = useState(false)
  const [executionHistory, setExecutionHistory] = useState<ExecutionResult[]>([])
  const [expandedTxIdx, setExpandedTxIdx] = useState<number | null>(null)
  const [customContracts, setCustomContracts] = useState<CustomContract[]>([])
  const [showAddContract, setShowAddContract] = useState(false)
  const [newContractName, setNewContractName] = useState('')
  const [newContractAddress, setNewContractAddress] = useState('')
  const [newContractAbiCid, setNewContractAbiCid] = useState('')
  const [newContractAbiJson, setNewContractAbiJson] = useState('')
  const [addingContract, setAddingContract] = useState(false)
  const [showNetworkDropdown, setShowNetworkDropdown] = useState(false)

  const fnSearchRef = useRef<HTMLInputElement>(null)

  // Close network dropdown on click outside
  useEffect(() => {
    if (!showNetworkDropdown) return
    const handler = (e: MouseEvent) => setShowNetworkDropdown(false)
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [showNetworkDropdown])

  useEffect(() => { localStorage.setItem('contracts_network', selectedNetwork) }, [selectedNetwork])

  // Switch wallet network + reset contract selection when network changes
  const switchNetwork = async (networkKey: string) => {
    setSelectedNetwork(networkKey)
    const cfg = (modConfig.chain as any)?.[networkKey]
    if (!cfg?.chainId || !window.ethereum) return
    const hexChainId = '0x' + parseInt(cfg.chainId).toString(16)
    try {
      await window.ethereum.request({ method: 'wallet_switchEthereumChain', params: [{ chainId: hexChainId }] })
    } catch (err: any) {
      // 4902 = chain not added — try adding it
      if (err.code === 4902 && cfg.url) {
        const net = NETWORKS.find(n => n.key === networkKey)
        try {
          await window.ethereum.request({
            method: 'wallet_addEthereumChain',
            params: [{
              chainId: hexChainId,
              chainName: net?.label || networkKey,
              rpcUrls: [cfg.url],
            }],
          })
        } catch { /* user rejected */ }
      }
    }
  }

  useEffect(() => {
    setSelectedContract('')
    setSelectedFnName('')
    setFnArgs([])
    setReadResult(null)
  }, [selectedNetwork])

  const activeChainConfig = (modConfig.chain as any)?.[selectedNetwork]
  const contracts = getContracts(selectedNetwork, customContracts)
  const filteredContracts = search
    ? contracts.filter((c) => c.name.toLowerCase().includes(search.toLowerCase()))
    : contracts
  const contractInfo = contracts.find((c) => c.name === selectedContract)
  const customContract = customContracts.find(c => c.name === selectedContract)
  // Solidity source is available for the bundled core contracts (those with a
  // built-in ABI), not user-added custom contracts.
  const hasSource = !customContract && !!selectedContract && !!CONTRACT_ABIS[selectedContract]
  const abi = customContract ? customContract.abi : (selectedContract ? CONTRACT_ABIS[selectedContract] : null)
  const allFunctions = abi ? getAllFunctions(abi) : []

  const visibleFunctions = allFunctions.filter(f => {
    if (f.kind === 'read' && !showRead) return false
    if (f.kind === 'write' && !showWrite) return false
    return true
  })

  const filteredFunctions = fnSearch
    ? visibleFunctions.filter(f => f.name.toLowerCase().includes(fnSearch.toLowerCase()))
    : visibleFunctions

  const selectedFnEntry = allFunctions.find((f) => `${f.kind}:${f.name}` === selectedFnName)

  useEffect(() => {
    setSelectedFnName('')
    setFnArgs([])
    setReadResult(null)
    setFnSearch('')
    setViewMode('interact')
  }, [selectedContract])

  useEffect(() => {
    if (selectedFnEntry) setFnArgs(new Array(selectedFnEntry.inputs.length).fill(''))
    else setFnArgs([])
    setReadResult(null)
  }, [selectedFnName]) // eslint-disable-line react-hooks/exhaustive-deps

  // Load custom contracts from localStorage
  useEffect(() => {
    const stored = localStorage.getItem('customContracts')
    if (stored) {
      try {
        setCustomContracts(JSON.parse(stored))
      } catch (err) {
        console.error('Failed to load custom contracts:', err)
      }
    }
  }, [])

  async function handleAddContract() {
    if (!newContractName || !newContractAddress) {
      toast.error('Contract name and address are required')
      return
    }

    if (!newContractAbiCid && !newContractAbiJson) {
      toast.error('Either ABI CID or ABI JSON is required')
      return
    }

    setAddingContract(true)
    try {
      let abi: any[]

      if (newContractAbiJson) {
        // Parse JSON ABI
        try {
          abi = JSON.parse(newContractAbiJson)
          if (!Array.isArray(abi)) {
            throw new Error('ABI must be an array')
          }
        } catch (err: any) {
          toast.error('Invalid ABI JSON: ' + err.message)
          setAddingContract(false)
          return
        }
      } else {
        // Fetch ABI from IPFS using CID
        // TODO: Implement IPFS fetching when client is available
        // For now, just show error
        toast.error('IPFS ABI fetching not yet implemented. Please use ABI JSON instead.')
        setAddingContract(false)
        return
      }

      const newContract: CustomContract = {
        name: newContractName,
        address: newContractAddress,
        abi,
        abiCid: newContractAbiCid || undefined
      }

      const updated = [...customContracts, newContract]
      setCustomContracts(updated)
      localStorage.setItem('customContracts', JSON.stringify(updated))

      toast.success(`Contract "${newContractName}" added successfully`)

      // Reset form
      setNewContractName('')
      setNewContractAddress('')
      setNewContractAbiCid('')
      setNewContractAbiJson('')
      setShowAddContract(false)
    } catch (err: any) {
      console.error('Failed to add contract:', err)
      toast.error(err.message || 'Failed to add contract')
    } finally {
      setAddingContract(false)
    }
  }

  function handleRemoveContract(contractName: string) {
    const updated = customContracts.filter(c => c.name !== contractName)
    setCustomContracts(updated)
    localStorage.setItem('customContracts', JSON.stringify(updated))
    toast.success(`Contract "${contractName}" removed`)
    if (selectedContract === contractName) {
      setSelectedContract('Market')
    }
  }

  async function handleRead() {
    if (!contractInfo || !selectedFnEntry || !abi) return
    setReading(true)
    setReadResult(null)
    const startTime = Date.now()
    try {
      if (!window.ethereum) throw new Error('No wallet connected')
      const provider = new ethers.BrowserProvider(window.ethereum)
      const contract = new ethers.Contract(contractInfo.address, abi, provider)
      const result = await contract[selectedFnEntry.name](...fnArgs)
      const delta = (Date.now() - startTime) / 1000

      let formattedResult: any
      if (Array.isArray(result)) {
        const formatted = result.map((v: any, i: number) => {
          const outputName = selectedFnEntry.outputs[i]?.name || `[${i}]`
          return `${outputName}: ${v.toString()}`
        })
        formattedResult = formatted.join('\n')
        setReadResult(formattedResult)
      } else {
        formattedResult = result.toString()
        setReadResult(formattedResult)
      }

      // Add to execution history
      const execution: ExecutionResult = {
        fn: selectedFnEntry.name,
        params: fnArgs.reduce((acc, val, idx) => {
          const paramName = selectedFnEntry.inputs[idx]?.name || `arg${idx}`
          acc[paramName] = val
          return acc
        }, {} as any),
        result: formattedResult,
        status: 'success',
        time: Math.floor(Date.now() / 1000),
        delta,
        cost: 0
      }
      setExecutionHistory(prev => [execution, ...prev])
    } catch (err: any) {
      console.error(err)
      let errorMsg = err?.message || 'Read failed'
      // Detect BAD_DATA (contract not deployed / wrong network)
      if (err?.code === 'BAD_DATA' || errorMsg.includes('BAD_DATA') || errorMsg.includes('could not decode result data')) {
        try {
          const provider = new ethers.BrowserProvider(window.ethereum!)
          const network = await provider.getNetwork()
          const expectedChainId = activeChainConfig?.chainId
          const networkLabel = NETWORKS.find(n => n.key === selectedNetwork)?.label || selectedNetwork
          if (expectedChainId && network.chainId.toString() !== expectedChainId) {
            errorMsg = `Wrong network: connected to chain ${network.chainId}, expected ${expectedChainId} (${networkLabel}). Switch your wallet network.`
          } else {
            errorMsg = `Contract not deployed at this address on chain ${network.chainId}. The contract may not exist or the ABI doesn't match.`
          }
        } catch {
          const networkLabel2 = NETWORKS.find(n => n.key === selectedNetwork)?.label || selectedNetwork
          errorMsg = `Contract returned empty data (0x). Check you are connected to ${networkLabel2} (chainId: ${activeChainConfig?.chainId || '?'}).`
        }
      }
      setReadResult(`Error: ${errorMsg}`)

      // Add error to execution history
      const execution: ExecutionResult = {
        fn: selectedFnEntry.name,
        params: fnArgs.reduce((acc, val, idx) => {
          const paramName = selectedFnEntry.inputs[idx]?.name || `arg${idx}`
          acc[paramName] = val
          return acc
        }, {} as any),
        result: errorMsg,
        status: 'error',
        time: Math.floor(Date.now() / 1000),
        delta: (Date.now() - startTime) / 1000,
        cost: 0
      }
      setExecutionHistory(prev => [execution, ...prev])
    } finally {
      setReading(false)
    }
  }

  async function handleWrite() {
    if (!contractInfo || !selectedFnEntry || !abi) return
    setSending(true)
    const startTime = Date.now()
    try {
      if (!window.ethereum) throw new Error('No wallet connected')
      const provider = new ethers.BrowserProvider(window.ethereum)
      const signer = await provider.getSigner()
      const contract = new ethers.Contract(contractInfo.address, abi, signer)
      const value = ethValue ? ethers.parseEther(ethValue) : BigInt(0)
      const tx = await contract[selectedFnEntry.name](...fnArgs, { value })
      toast.success(`TX sent: ${tx.hash.slice(0, 12)}...`)
      const receipt = await tx.wait()
      const delta = (Date.now() - startTime) / 1000
      toast.success('TX confirmed')

      // Add to execution history
      const execution: ExecutionResult = {
        fn: selectedFnEntry.name,
        params: fnArgs.reduce((acc, val, idx) => {
          const paramName = selectedFnEntry.inputs[idx]?.name || `arg${idx}`
          acc[paramName] = val
          return acc
        }, {} as any),
        result: { hash: receipt.hash, status: receipt.status },
        status: 'success',
        time: Math.floor(Date.now() / 1000),
        delta,
        hash: receipt.hash,
        cost: 0
      }
      setExecutionHistory(prev => [execution, ...prev])

      setSelectedFnName('')
      setFnArgs([])
      setEthValue('')
    } catch (err: any) {
      console.error(err)
      let errorMsg = err?.reason || err?.message || 'Transaction failed'
      if (err?.code === 'BAD_DATA' || errorMsg.includes('BAD_DATA') || errorMsg.includes('could not decode result data')) {
        const networkLabel = NETWORKS.find(n => n.key === selectedNetwork)?.label || selectedNetwork
        errorMsg = `Contract not found on this network. Switch to ${networkLabel} (chainId: ${activeChainConfig?.chainId || '?'}).`
      }
      toast.error(errorMsg)

      // Add error to execution history
      const execution: ExecutionResult = {
        fn: selectedFnEntry.name,
        params: fnArgs.reduce((acc, val, idx) => {
          const paramName = selectedFnEntry.inputs[idx]?.name || `arg${idx}`
          acc[paramName] = val
          return acc
        }, {} as any),
        result: errorMsg,
        status: 'error',
        time: Math.floor(Date.now() / 1000),
        delta: (Date.now() - startTime) / 1000,
        cost: 0
      }
      setExecutionHistory(prev => [execution, ...prev])
    } finally {
      setSending(false)
    }
  }

  const readCount = allFunctions.filter(f => f.kind === 'read').length
  const writeCount = allFunctions.filter(f => f.kind === 'write').length

  const renderTransactionCard = (exec: ExecutionResult, idx: number) => {
    const isExpanded = expandedTxIdx === idx
    const statusText = exec.status === 'success' ? 'SUC' : 'ERR'

    return (
      <div
        key={idx}
        onClick={() => setExpandedTxIdx(isExpanded ? null : idx)}
        className="rounded-xl backdrop-blur-sm transition-all cursor-pointer relative overflow-hidden hover:shadow-lg"
        style={{
          fontFamily: 'IBM Plex Mono, monospace',
          backgroundColor: 'var(--bg-surface)',
          border: exec.status === 'success' ? '1px solid rgba(16,185,129,0.4)' : '1px solid rgba(239,68,68,0.4)'
        }}
      >
        {/* Header */}
        <div className="flex items-center gap-2 px-4 py-2.5" style={{ borderBottom: '1px solid var(--border-color)' }}>
          {exec.hash && (
            <div className="flex items-center gap-1">
              <CopyButton text={exec.hash} size="sm" showValueOnHover={true} />
            </div>
          )}

          <div className="flex items-center gap-1">
            <span className={`text-[10px] font-bold tracking-wider px-1.5 py-0.5 rounded ${
              exec.status === 'success' ? 'text-emerald-500 bg-emerald-500/10' : 'text-red-500 bg-red-500/10'
            }`}>
              {statusText}
            </span>
          </div>

          <div className="text-xs font-bold truncate text-cyan-500 flex-1 min-w-0">
            {exec.fn}
          </div>

          <div className="flex items-center gap-3 text-xs">
            <div className="flex items-center gap-1">
              <span className="text-purple-500/60 text-[10px]">$</span>
              <span className="text-cyan-500 font-bold">{exec.cost?.toFixed(2) || '0.00'}</span>
            </div>

            {exec.delta !== undefined && (
              <div className="flex items-center gap-1">
                <span className="text-purple-500/60 text-[10px]">t</span>
                <span className="text-cyan-500 font-bold">{exec.delta.toFixed(1)}s</span>
              </div>
            )}
          </div>
        </div>

        {/* Input preview when collapsed */}
        {!isExpanded && (
          <div className="px-4 py-2" style={{ backgroundColor: 'var(--bg-input)' }}>
            <div className="flex items-center gap-2">
              <span className="text-[10px] font-bold tracking-wider text-purple-400/70">INPUT</span>
              <span className="text-xs font-mono truncate" style={{ color: 'var(--text-tertiary)' }}>
                {JSON.stringify(exec.params)}
              </span>
            </div>
          </div>
        )}

        {/* Expanded Content */}
        {isExpanded && (
          <div className="px-4 pb-4 pt-3 space-y-3">
            {/* Input */}
            <div>
              <div className="text-[10px] font-bold tracking-wider text-purple-400/70 mb-1.5">INPUT</div>
              <pre className="text-xs font-mono p-3 rounded-lg" style={{ backgroundColor: 'var(--bg-input)', color: 'var(--text-secondary)', border: '1px solid var(--border-color)' }}>
                {JSON.stringify(exec.params, null, 2)}
              </pre>
            </div>

            {/* Result */}
            <div>
              <div className="text-[10px] font-bold tracking-wider text-purple-400/70 mb-1.5">RESULT</div>
              <pre className={`text-xs font-mono p-3 rounded-lg ${
                exec.status === 'success' ? 'text-emerald-400' : 'text-red-400'
              }`} style={{ backgroundColor: 'var(--bg-input)', border: '1px solid var(--border-color)' }}>
                {typeof exec.result === 'object' ? JSON.stringify(exec.result, null, 2) : exec.result}
              </pre>
            </div>
          </div>
        )}
      </div>
    )
  }

  const renderChatMessage = (exec: ExecutionResult, idx: number) => {
    return (
      <div key={idx} className="space-y-2">
        {/* User message */}
        <div className="flex justify-end">
          <div className="max-w-[80%] px-4 py-3 rounded-xl" style={{ backgroundColor: 'var(--bg-secondary)', border: '1px solid var(--border-color)' }}>
            <div className="text-[10px] font-bold tracking-wider text-cyan-500 mb-1">{exec.fn}</div>
            <div className="text-xs font-mono" style={{ color: 'var(--text-secondary)' }}>
              {JSON.stringify(exec.params, null, 2)}
            </div>
          </div>
        </div>

        {/* Assistant response */}
        <div className="flex justify-start">
          <div className={`max-w-[80%] px-4 py-3 rounded-xl ${
            exec.status === 'success'
              ? 'bg-emerald-500/10'
              : 'bg-red-500/10'
          }`} style={{
            border: exec.status === 'success' ? '1px solid rgba(16,185,129,0.3)' : '1px solid rgba(239,68,68,0.3)'
          }}>
            <div className="flex items-center gap-2 mb-1">
              <span className={`text-[10px] font-bold tracking-wider ${
                exec.status === 'success' ? 'text-emerald-500' : 'text-red-500'
              }`}>
                {exec.status === 'success' ? 'SUCCESS' : 'ERROR'}
              </span>
              {exec.delta && (
                <span className="text-[9px] font-mono text-purple-500/60">{exec.delta.toFixed(1)}s</span>
              )}
            </div>
            <pre className={`text-xs font-mono whitespace-pre-wrap ${
              exec.status === 'success' ? 'text-emerald-400' : 'text-red-400'
            }`}>
              {typeof exec.result === 'object' ? JSON.stringify(exec.result, null, 2) : exec.result}
            </pre>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-full flex flex-col">
      <div className="max-w-7xl mx-auto px-8 py-6 w-full flex-1 flex flex-col">
        {/* Page header with search and add - all in one line */}
        <div className="flex items-center gap-4 mb-5">
          <div className="w-2 h-2 rounded-full bg-cyan-400 shrink-0" style={{ boxShadow: '0 0 8px rgba(0,255,255,0.6)' }} />
          <h1 className="text-4xl font-bold lowercase tracking-[0.15em] font-mono shrink-0" style={{ color: 'var(--text-primary)' }}>Contracts</h1>
          <div className="flex-1 h-px" style={{ background: 'linear-gradient(to right, var(--border-color), transparent)' }} />
          <span className="text-[12px] font-mono shrink-0" style={{ color: 'var(--text-tertiary)' }}>[{contracts.length}]</span>

          {/* Network dropdown */}
          {(() => {
            const activeNet = NETWORKS.find(n => n.key === selectedNetwork) || NETWORKS[0]
            return (
              <div className="relative shrink-0">
                <button
                  onClick={() => setShowNetworkDropdown(!showNetworkDropdown)}
                  className="flex items-center gap-2 px-3 py-2 rounded-lg text-[13px] font-bold font-mono tracking-wider transition-all"
                  style={{
                    backgroundColor: 'var(--bg-input)',
                    border: '1px solid rgba(0, 255, 255, 0.3)',
                    color: '#22d3ee',
                    cursor: 'pointer',
                  }}
                >
                  <span className="w-2 h-2 rounded-full" style={{ background: '#22d3ee', boxShadow: '0 0 6px rgba(0,255,255,0.5)' }} />
                  {activeNet.label}
                  <span style={{ fontSize: '10px', opacity: 0.5 }}>{activeNet.chainId}</span>
                  <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" style={{ opacity: 0.5 }}>
                    <polyline points="6 9 12 15 18 9" />
                  </svg>
                </button>
                {showNetworkDropdown && (
                  <div
                    className="absolute top-full left-0 mt-1 z-50 overflow-hidden"
                    style={{
                      background: 'var(--bg-secondary)',
                      border: '1px solid var(--border-color)',
                      borderRadius: '8px',
                      boxShadow: '0 12px 40px rgba(0,0,0,0.4)',
                      minWidth: '180px',
                    }}
                  >
                    {NETWORKS.map(net => {
                      const isActive = selectedNetwork === net.key
                      return (
                        <button
                          key={net.key}
                          onClick={() => { switchNetwork(net.key); setShowNetworkDropdown(false) }}
                          className="w-full flex items-center gap-2.5 px-4 py-2.5 text-left transition-all"
                          style={{
                            fontSize: '13px',
                            fontFamily: 'monospace',
                            fontWeight: isActive ? 700 : 500,
                            color: isActive ? '#22d3ee' : 'var(--text-primary)',
                            background: isActive ? 'rgba(0, 255, 255, 0.08)' : 'transparent',
                            borderBottom: '1px solid var(--border-color)',
                            cursor: 'pointer',
                            border: 'none',
                            borderBlockEnd: '1px solid var(--border-color)',
                          }}
                          onMouseEnter={e => { if (!isActive) e.currentTarget.style.background = 'var(--hover-bg)' }}
                          onMouseLeave={e => { if (!isActive) e.currentTarget.style.background = 'transparent' }}
                        >
                          <span className="w-2 h-2 rounded-full" style={{ background: isActive ? '#22d3ee' : 'var(--text-tertiary)', boxShadow: isActive ? '0 0 6px rgba(0,255,255,0.5)' : 'none' }} />
                          <span className="flex-1">{net.label}</span>
                          <span style={{ fontSize: '10px', color: 'var(--text-tertiary)' }}>{net.chainId}</span>
                          {isActive && <span style={{ fontSize: '11px', color: '#22d3ee' }}>&#10003;</span>}
                        </button>
                      )
                    })}
                  </div>
                )}
              </div>
            )
          })()}

          {/* Search */}
          <div className="relative w-[500px] shrink-0">
            <MagnifyingGlassIcon className="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5" style={{ color: 'var(--text-tertiary)' }} />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && filteredContracts.length === 1) {
                  setSelectedContract(filteredContracts[0].name)
                  setSearch('')
                }
              }}
              placeholder="SEARCH CONTRACTS..."
              className="w-full pl-12 pr-6 py-3.5 rounded-xl text-[14px] font-mono focus:outline-none focus:ring-2 focus:ring-cyan-500/30 transition-all"
              style={{ backgroundColor: 'var(--bg-input)', border: '1px solid var(--border-color)', color: 'var(--text-primary)' }}
            />
          </div>

          {/* Add button */}
          <button
            onClick={() => setShowAddContract(!showAddContract)}
            className="px-5 py-3 rounded-xl text-[14px] font-bold font-mono tracking-wider transition-all text-cyan-400 hover:bg-cyan-500/10 shrink-0"
            style={{ backgroundColor: 'var(--bg-secondary)', border: '1px solid var(--border-strong)' }}
          >
            + ADD
          </button>
        </div>

        {/* Add Contract Form */}
        <AnimatePresence>
          {showAddContract && (
            <motion.div
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: 'auto', opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              transition={{ duration: 0.2 }}
              className="overflow-hidden mb-5"
            >
              <div className="p-5 rounded-xl" style={{ backgroundColor: 'var(--bg-secondary)', border: '1px solid var(--border-strong)' }}>
                <div className="flex items-center gap-2.5 mb-5">
                  <div className="w-1.5 h-1.5 rounded-full bg-cyan-400" style={{ boxShadow: '0 0 6px rgba(0,255,255,0.8)' }} />
                  <h3 className="text-[14px] font-bold tracking-wider font-mono" style={{ color: 'var(--text-primary)' }}>Add Custom Contract</h3>
                </div>

                <div className="space-y-4">
                  {/* Contract Name */}
                  <div>
                    <label className="text-[11px] font-mono mb-1.5 block font-semibold" style={{ color: 'var(--text-secondary)' }}>
                      Contract Name
                    </label>
                    <input
                      type="text"
                      value={newContractName}
                      onChange={(e) => setNewContractName(e.target.value)}
                      placeholder="MYCONTRACT"
                      className="w-full px-4 py-2.5 rounded-lg text-[13px] font-mono focus:outline-none focus:ring-2 focus:ring-cyan-500/30 transition-all"
                      style={{ backgroundColor: 'var(--bg-input)', border: '1px solid var(--border-color)', color: 'var(--text-primary)' }}
                    />
                  </div>

                  {/* Contract Address */}
                  <div>
                    <label className="text-[11px] font-mono mb-1.5 block font-semibold" style={{ color: 'var(--text-secondary)' }}>
                      Contract Address
                    </label>
                    <input
                      type="text"
                      value={newContractAddress}
                      onChange={(e) => setNewContractAddress(e.target.value)}
                      placeholder="0x..."
                      className="w-full px-4 py-2.5 rounded-lg text-[13px] font-mono focus:outline-none focus:ring-2 focus:ring-cyan-500/30 transition-all"
                      style={{ backgroundColor: 'var(--bg-input)', border: '1px solid var(--border-color)', color: 'var(--text-primary)' }}
                    />
                  </div>

                  {/* ABI CID (optional) */}
                  <div>
                    <label className="text-[11px] font-mono mb-1.5 block font-semibold" style={{ color: 'var(--text-secondary)' }}>
                      ABI CID <span style={{ color: 'var(--text-tertiary)' }}>(optional - not yet supported)</span>
                    </label>
                    <input
                      type="text"
                      value={newContractAbiCid}
                      onChange={(e) => setNewContractAbiCid(e.target.value)}
                      placeholder="Qm..."
                      disabled
                      className="w-full px-4 py-2.5 rounded-lg text-[13px] font-mono focus:outline-none transition-all opacity-50 cursor-not-allowed"
                      style={{ backgroundColor: 'var(--bg-input)', border: '1px solid var(--border-color)', color: 'var(--text-primary)' }}
                    />
                  </div>

                  {/* ABI JSON */}
                  <div>
                    <label className="text-[11px] font-mono mb-1.5 block font-semibold" style={{ color: 'var(--text-secondary)' }}>
                      ABI JSON <span style={{ color: 'var(--text-tertiary)' }}>(paste ABI array)</span>
                    </label>
                    <textarea
                      value={newContractAbiJson}
                      onChange={(e) => setNewContractAbiJson(e.target.value)}
                      placeholder='[{"type":"function","name":"transfer","inputs":[...],...}]'
                      rows={6}
                      className="w-full px-4 py-2.5 rounded-lg text-[12px] font-mono focus:outline-none focus:ring-2 focus:ring-cyan-500/30 transition-all resize-none"
                      style={{ backgroundColor: 'var(--bg-input)', border: '1px solid var(--border-color)', color: 'var(--text-primary)' }}
                    />
                  </div>

                  {/* Actions */}
                  <div className="flex gap-3 pt-1">
                    <button
                      onClick={handleAddContract}
                      disabled={addingContract}
                      className="flex-1 py-2.5 rounded-lg text-[13px] font-bold font-mono tracking-wider transition-all disabled:opacity-40 flex items-center justify-center gap-2 text-emerald-400 hover:bg-emerald-500/15"
                      style={{ backgroundColor: 'var(--bg-surface)', border: '1px solid rgba(16,185,129,0.3)' }}
                    >
                      {addingContract ? (
                        <>
                          <ArrowPathIcon className="w-4 h-4 animate-spin" />
                          Adding...
                        </>
                      ) : (
                        'Add Contract'
                      )}
                    </button>
                    <button
                      onClick={() => {
                        setShowAddContract(false)
                        setNewContractName('')
                        setNewContractAddress('')
                        setNewContractAbiCid('')
                        setNewContractAbiJson('')
                      }}
                      className="px-5 py-2.5 rounded-lg text-[13px] font-bold font-mono tracking-wider transition-all hover:bg-white/5"
                      style={{ color: 'var(--text-tertiary)', border: '1px solid var(--border-color)', backgroundColor: 'var(--bg-surface)' }}
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        {/* Contract Grid - Show when no contract is selected OR when searching */}
        {(!selectedContract || search) && (
          <div className="flex flex-wrap gap-2.5 mb-6">
            {(filteredContracts.length > 0 ? filteredContracts : contracts).map((c) => {
              const cardColor = text2color(c.name)
              const isCustom = customContracts.some(cc => cc.name === c.name)
              return (
                <div key={c.name} className="relative group/card">
                  <button
                    onClick={() => setSelectedContract(c.name)}
                    className="relative px-4 py-2.5 rounded-lg text-left transition-all duration-200 overflow-hidden hover:shadow-md"
                    style={{
                      border: '1px solid var(--border-strong)',
                      backgroundColor: 'var(--bg-secondary)',
                      boxShadow: 'var(--card-shadow)',
                    }}
                  >
                    <div className="space-y-1">
                      <div className="flex items-center gap-2">
                        <div className="w-1.5 h-1.5 rounded-full transition-all" style={{
                          backgroundColor: cardColor,
                          boxShadow: `0 0 4px ${colorWithOpacity(cardColor, 0.5)}`,
                        }} />
                        <div className="text-[13px] font-bold font-mono" style={{ color: 'var(--text-primary)' }}>{c.name}</div>
                        {isCustom && (
                          <span className="text-[8px] px-1.5 py-0.5 rounded-full font-mono font-bold bg-cyan-500/10 text-cyan-400" style={{ border: '1px solid rgba(0,255,255,0.2)' }}>CUSTOM</span>
                        )}
                        <div className="text-[10px] font-mono" style={{ color: 'var(--text-tertiary)' }}>{shorten(c.address, 4, 3)}</div>
                      </div>
                      {c.abiCid && (
                        <div className="flex items-center gap-1 ml-4">
                          <span className="text-[9px] font-mono" style={{ color: 'var(--text-tertiary)' }}>ABI:</span>
                          <span className="text-[9px] font-mono" style={{ color: 'var(--text-secondary)' }}>{shorten(c.abiCid, 8, 6)}</span>
                        </div>
                      )}
                    </div>
                  </button>

                  {/* Remove button for custom contracts */}
                  {isCustom && (
                    <button
                      onClick={(e) => {
                        e.stopPropagation()
                        if (confirm(`Remove custom contract "${c.name}"?`)) {
                          handleRemoveContract(c.name)
                        }
                      }}
                      className="absolute -top-1.5 -right-1.5 w-5 h-5 rounded-full bg-red-500/80 text-white text-xs font-bold opacity-0 group-hover/card:opacity-100 transition-opacity hover:bg-red-500 flex items-center justify-center shadow-lg"
                      title="Remove custom contract"
                    >
                      ×
                    </button>
                  )}
                </div>
              )
            })}
          </div>
        )}

        {/* Selected contract interaction */}
        {contractInfo && (() => {
          const activeColor = text2color(contractInfo.name)
          return (
          <div className="p-6 rounded-xl relative overflow-hidden" style={{
            border: `1px solid ${colorWithOpacity(activeColor, 0.3)}`,
            backgroundColor: 'var(--bg-secondary)',
          }}>
            {/* Subtle top glow */}
            <div className="absolute top-0 left-0 right-0 h-px" style={{ background: `linear-gradient(to right, transparent, ${colorWithOpacity(activeColor, 0.5)}, transparent)` }} />

            {/* Contract header bar */}
            <div className="flex items-center gap-4 mb-6">
              {/* Back button */}
              <button
                onClick={() => setSelectedContract('')}
                className="px-3 py-2 rounded-lg text-[12px] font-bold font-mono tracking-wider transition-all flex items-center gap-2 hover:bg-white/5 shrink-0"
                style={{
                  border: '1px solid var(--border-color)',
                  backgroundColor: 'var(--bg-surface)',
                  color: 'var(--text-secondary)',
                }}
              >
                <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M10 19l-7-7m0 0l7-7m-7 7h18" />
                </svg>
                Back to Contracts
              </button>

              {/* Contract name */}
              <div className="flex items-center gap-3 shrink-0">
                <div className="w-2 h-2 rounded-full" style={{ backgroundColor: activeColor, boxShadow: `0 0 8px ${activeColor}` }} />
                <h2 className="text-xl font-bold font-mono" style={{ color: activeColor }}>{contractInfo.name}</h2>
              </div>

              {/* Contract address */}
              <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg shrink-0 cursor-pointer hover:bg-white/5 transition-colors"
                   style={{ backgroundColor: 'var(--bg-input)', border: '1px solid var(--border-color)' }}
                   onClick={() => {
                     navigator.clipboard.writeText(contractInfo.address)
                     toast.success('Address copied!')
                   }}
                   title="Click to copy address">
                <span className="text-[13px] font-mono" style={{ color: 'var(--text-secondary)' }}>{shorten(contractInfo.address)}</span>
                <CopyButton text={contractInfo.address} size="sm" />
              </div>

              {/* ABI CID */}
              {contractInfo.abiCid && (
                <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg shrink-0 cursor-pointer hover:bg-white/5 transition-colors"
                     style={{ backgroundColor: 'var(--bg-input)', border: '1px solid var(--border-color)' }}
                     onClick={() => {
                       navigator.clipboard.writeText(contractInfo.abiCid!)
                       toast.success('ABI CID copied!')
                     }}
                     title="Click to copy ABI CID">
                  <span className="text-[11px] font-mono" style={{ color: 'var(--text-tertiary)' }}>ABI:</span>
                  <span className="text-[12px] font-mono" style={{ color: 'var(--text-secondary)' }}>{shorten(contractInfo.abiCid, 10, 8)}</span>
                  <CopyButton text={contractInfo.abiCid} size="sm" />
                </div>
              )}

              <div className="flex-1" />

              {/* Interact / Code mode toggle (code only for bundled contracts) */}
              {hasSource && (
                <div className="flex gap-1.5 shrink-0 p-1 rounded-lg" style={{ backgroundColor: 'var(--bg-input)', border: '1px solid var(--border-color)' }}>
                  {(['interact', 'code'] as const).map((mode) => (
                    <button
                      key={mode}
                      onClick={() => setViewMode(mode)}
                      className="px-4 py-1.5 rounded-md text-[12px] font-bold tracking-wider font-mono transition-all"
                      style={viewMode === mode
                        ? { color: activeColor, backgroundColor: colorWithOpacity(activeColor, 0.15) }
                        : { color: 'var(--text-tertiary)' }}
                    >
                      {mode === 'interact' ? 'Interact' : 'Code'}
                    </button>
                  ))}
                </div>
              )}

              {/* Read / Write filter toggles (interact mode only) */}
              {viewMode === 'interact' && (
                <div className="flex gap-1.5 shrink-0 p-1 rounded-lg" style={{ backgroundColor: 'var(--bg-input)', border: '1px solid var(--border-color)' }}>
                  <button
                    onClick={() => setShowRead(!showRead)}
                    className={`px-4 py-1.5 rounded-md text-[12px] font-bold tracking-wider font-mono transition-all ${
                      showRead
                        ? 'bg-emerald-500/15 text-emerald-500'
                        : ''
                    }`}
                    style={!showRead ? { color: 'var(--text-tertiary)' } : undefined}
                  >
                    Read{readCount > 0 && ` (${readCount})`}
                  </button>
                  <button
                    onClick={() => setShowWrite(!showWrite)}
                    className={`px-4 py-1.5 rounded-md text-[12px] font-bold tracking-wider font-mono transition-all ${
                      showWrite
                        ? 'bg-amber-500/15 text-amber-500'
                        : ''
                    }`}
                    style={!showWrite ? { color: 'var(--text-tertiary)' } : undefined}
                  >
                    Write{writeCount > 0 && ` (${writeCount})`}
                  </button>
                </div>
              )}
            </div>

            {/* Code view — Solidity source of the deployed contract */}
            {viewMode === 'code' ? (
              <CodeViewer name={contractInfo.name} accent={activeColor} />
            ) : (
            <>
            {/* Two-column layout */}
            <div className="flex gap-6">
              {/* Left: function cards */}
              <div className="w-[380px] shrink-0">
                {/* Function search */}
                <div className="relative mb-4">
                  <MagnifyingGlassIcon className="absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4" style={{ color: 'var(--text-tertiary)' }} />
                  <input
                    ref={fnSearchRef}
                    type="text"
                    value={fnSearch}
                    onChange={(e) => setFnSearch(e.target.value)}
                    placeholder="SEARCH FUNCTIONS..."
                    className="w-full pl-11 pr-20 py-2.5 rounded-lg text-[13px] font-mono focus:outline-none focus:ring-2 focus:ring-cyan-500/30 transition-all"
                    style={{ backgroundColor: 'var(--bg-input)', border: '1px solid var(--border-color)', color: 'var(--text-primary)' }}
                  />
                  <div className="absolute right-4 top-1/2 -translate-y-1/2 flex items-center gap-2">
                    {fnSearch && (
                      <button
                        onClick={() => { setFnSearch(''); fnSearchRef.current?.focus() }}
                        className="text-[10px] font-bold font-mono px-2 py-0.5 rounded transition-colors hover:bg-white/10"
                        style={{ color: 'var(--text-tertiary)' }}
                      >
                        CLR
                      </button>
                    )}
                    <span className="text-[10px] font-mono" style={{ color: 'var(--text-tertiary)' }}>
                      {filteredFunctions.length}/{visibleFunctions.length}
                    </span>
                  </div>
                </div>

                {/* Scrollable function cards */}
                <div className="max-h-[600px] overflow-y-auto pr-1 space-y-1 custom-scrollbar">
                  {filteredFunctions.map((fn) => {
                    const key = `${fn.kind}:${fn.name}`
                    const isActive = selectedFnName === key
                    const isRead = fn.kind === 'read'
                    return (
                      <button
                        key={key}
                        onClick={() => setSelectedFnName(isActive ? '' : key)}
                        className={`w-full text-left px-4 py-3 rounded-lg transition-all duration-150 group relative overflow-hidden ${
                          isActive
                            ? isRead
                              ? 'bg-emerald-500/8'
                              : 'bg-amber-500/8'
                            : 'hover:bg-white/[0.03]'
                        }`}
                        style={{
                          border: isActive
                            ? `1px solid ${isRead ? 'rgba(16,185,129,0.4)' : 'rgba(245,158,11,0.4)'}`
                            : '1px solid transparent',
                          backgroundColor: isActive ? undefined : 'transparent',
                        }}
                      >
                        {/* Left accent bar */}
                        <div className="absolute left-0 top-2 bottom-2 w-0.5 rounded-full transition-colors" style={{
                          backgroundColor: isActive
                            ? isRead ? 'rgb(16,185,129)' : 'rgb(245,158,11)'
                            : 'transparent',
                        }} />

                        {/* Function name row */}
                        <div className="flex items-center gap-2.5 mb-1">
                          <span className={`shrink-0 px-1.5 py-0.5 rounded text-[10px] font-bold font-mono ${
                            isRead
                              ? 'bg-emerald-500/12 text-emerald-500'
                              : 'bg-amber-500/12 text-amber-500'
                          }`}>
                            {isRead ? 'R' : 'W'}
                          </span>
                          <span className="text-[13px] font-bold font-mono truncate" style={{
                            color: isActive
                              ? isRead ? 'rgb(16,185,129)' : 'rgb(245,158,11)'
                              : 'var(--text-primary)',
                          }}>{fn.name}</span>
                          {fn.stateMutability === 'payable' && (
                            <span className="text-[9px] font-mono px-1.5 py-0.5 rounded-full bg-amber-500/10 text-amber-500/60 ml-auto shrink-0">PAYABLE</span>
                          )}
                        </div>

                        {/* Schema: params */}
                        <div className="ml-7 space-y-0.5">
                          {fn.inputs.length > 0 && (
                            <div className="flex flex-wrap gap-x-3 gap-y-0.5">
                              {fn.inputs.map((inp: any, i: number) => (
                                <span key={i} className="text-[11px] font-mono" style={{ color: 'var(--text-tertiary)' }}>
                                  <span style={{ color: 'var(--text-secondary)' }}>{inp.name || `_${i}`}</span>
                                  <span style={{ color: 'var(--text-tertiary)' }}> : {inp.type}</span>
                                </span>
                              ))}
                            </div>
                          )}
                          {fn.outputs.length > 0 && (
                            <div className="text-[11px] font-mono" style={{ color: 'var(--text-tertiary)' }}>
                              <span className="text-emerald-500/40">{'\u2192'} </span>
                              {fn.outputs.map((o: any, i: number) => (
                                <span key={i}>
                                  {i > 0 && ', '}
                                  {o.name && <span style={{ color: 'var(--text-secondary)' }}>{o.name}: </span>}
                                  <span>{o.type}</span>
                                </span>
                              ))}
                            </div>
                          )}
                          {fn.inputs.length === 0 && fn.outputs.length === 0 && (
                            <span className="text-[11px] font-mono" style={{ color: 'var(--text-tertiary)' }}>{'() -> void'}</span>
                          )}
                        </div>
                      </button>
                    )
                  })}
                  {filteredFunctions.length === 0 && (
                    <div className="text-center text-[13px] font-mono py-12 rounded-lg font-bold" style={{ color: 'var(--text-tertiary)', backgroundColor: 'var(--bg-input)', border: '1px dashed var(--border-color)' }}>
                      {visibleFunctions.length === 0 ? '-- NO FUNCTIONS --' : '-- NO MATCHES --'}
                    </div>
                  )}
                </div>
              </div>

              {/* Right: interaction panel */}
              <div className="flex-1 min-w-0">
                {selectedFnEntry ? (() => {
                  const isRead = selectedFnEntry.kind === 'read'
                  return (
                  <div className="p-6 rounded-xl space-y-5 relative overflow-hidden" style={{
                    border: `1px solid ${isRead ? 'rgba(16,185,129,0.3)' : 'rgba(245,158,11,0.3)'}`,
                    backgroundColor: 'var(--bg-surface)',
                  }}>
                    {/* Top accent line */}
                    <div className="absolute top-0 left-4 right-4 h-px rounded-full" style={{ background: `linear-gradient(to right, transparent, ${isRead ? 'rgba(16,185,129,0.5)' : 'rgba(245,158,11,0.5)'}, transparent)` }} />

                    {/* Function header */}
                    <div className="flex items-center gap-3">
                      <span className={`px-3 py-1 rounded-md text-[11px] font-bold font-mono uppercase ${
                        isRead
                          ? 'bg-emerald-500/12 text-emerald-500'
                          : 'bg-amber-500/12 text-amber-500'
                      }`}>{selectedFnEntry.kind}</span>
                      <span className="text-lg font-bold font-mono" style={{ color: 'var(--text-primary)' }}>{selectedFnEntry.name}</span>
                      {selectedFnEntry.stateMutability === 'payable' && (
                        <span className="text-[10px] font-mono px-2 py-0.5 rounded-full bg-amber-500/10 text-amber-500/60">PAYABLE</span>
                      )}
                    </div>

                    {/* Full signature */}
                    <div className="px-4 py-3 rounded-lg" style={{ backgroundColor: 'var(--bg-input)', border: '1px solid var(--border-color)' }}>
                      <code className="text-[13px] font-mono leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
                        <span style={{ color: isRead ? 'rgb(16,185,129)' : 'rgb(245,158,11)', opacity: 0.6 }}>fn </span>
                        {selectedFnEntry.name}
                        <span style={{ color: 'var(--text-tertiary)' }}>(</span>
                        {selectedFnEntry.inputs.map((inp: any, i: number) => (
                          <span key={i}>
                            {i > 0 && <span style={{ color: 'var(--text-tertiary)' }}>, </span>}
                            <span>{inp.name || `_${i}`}</span>
                            <span style={{ color: 'var(--text-tertiary)' }}> : {inp.type}</span>
                          </span>
                        ))}
                        <span style={{ color: 'var(--text-tertiary)' }}>)</span>
                        {isRead && selectedFnEntry.outputs.length > 0 && (
                          <span style={{ color: 'var(--text-tertiary)' }}>
                            {' -> '}
                            {selectedFnEntry.outputs.map((o: any, i: number) => (
                              <span key={i}>{i > 0 && ', '}{o.type}{o.name && ` ${o.name}`}</span>
                            ))}
                          </span>
                        )}
                      </code>
                    </div>

                    {/* Args */}
                    <FnArgsInput inputs={selectedFnEntry.inputs} args={fnArgs} setArgs={setFnArgs} />

                    {/* ETH value for write */}
                    {selectedFnEntry.kind === 'write' && (
                      <div>
                        <label className="text-[12px] font-mono mb-1.5 block font-semibold" style={{ color: 'var(--text-secondary)' }}>
                          ETH value <span style={{ color: 'var(--text-tertiary)' }}>:: optional</span>
                        </label>
                        <input
                          type="text"
                          value={ethValue}
                          onChange={(e) => setEthValue(e.target.value)}
                          placeholder="0"
                          className="w-full px-4 py-2.5 rounded-lg text-[14px] font-mono focus:outline-none focus:ring-2 focus:ring-cyan-500/30 transition-all"
                          style={{ backgroundColor: 'var(--bg-input)', border: '1px solid var(--border-color)', color: 'var(--text-primary)' }}
                        />
                      </div>
                    )}

                    {/* Execute button */}
                    <button
                      onClick={isRead ? handleRead : handleWrite}
                      disabled={reading || sending}
                      className={`w-full py-3 rounded-lg text-[14px] font-bold font-mono tracking-wider transition-all duration-200 disabled:opacity-40 flex items-center justify-center gap-2.5 ${
                        isRead
                          ? 'bg-emerald-500/12 text-emerald-500 hover:bg-emerald-500/20'
                          : 'bg-amber-500/12 text-amber-500 hover:bg-amber-500/20'
                      }`}
                      style={{
                        border: isRead ? '1px solid rgba(16,185,129,0.3)' : '1px solid rgba(245,158,11,0.3)',
                        boxShadow: isRead
                          ? '0 0 20px rgba(16,185,129,0.06)'
                          : '0 0 20px rgba(245,158,11,0.06)',
                      }}
                    >
                      {(reading || sending) ? (
                        <ArrowPathIcon className="w-5 h-5 animate-spin" />
                      ) : (
                        <>
                          <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
                            <path strokeLinecap="round" strokeLinejoin="round" d="M13 10V3L4 14h7v7l9-11h-7z" />
                          </svg>
                          {isRead ? `Execute ${selectedFnEntry.name}` : `Send ${selectedFnEntry.name}`}
                        </>
                      )}
                    </button>

                    {/* Read result */}
                    {readResult !== null && (
                      <motion.div
                        initial={{ opacity: 0, y: 8 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ duration: 0.2 }}
                        className="p-4 rounded-lg relative overflow-hidden"
                        style={{ backgroundColor: 'var(--bg-input)', border: '1px solid rgba(16,185,129,0.3)' }}
                      >
                        <div className="absolute top-0 left-0 right-0 h-px bg-emerald-500/30" />
                        <div className="flex items-center justify-between mb-2">
                          <span className="text-[11px] font-bold tracking-wider font-mono text-emerald-500/60">Output</span>
                          <CopyButton text={readResult} size="sm" />
                        </div>
                        <pre className="text-[15px] font-mono font-bold whitespace-pre-wrap break-all leading-relaxed" style={{ color: 'var(--text-primary)' }}>{readResult}</pre>
                      </motion.div>
                    )}
                  </div>
                  )
                })() : (
                  <div className="flex flex-col items-center justify-center h-80 rounded-xl" style={{
                    color: 'var(--text-tertiary)',
                    border: '1px dashed var(--border-color)',
                    backgroundColor: 'var(--bg-input)',
                  }}>
                    <div className="w-12 h-12 mb-4 rounded-xl flex items-center justify-center" style={{ backgroundColor: 'var(--bg-surface)', border: '1px solid var(--border-color)' }}>
                      <svg className="w-5 h-5" style={{ color: 'var(--text-tertiary)', opacity: 0.6 }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M13 10V3L4 14h7v7l9-11h-7z" />
                      </svg>
                    </div>
                    <span className="text-[14px] font-mono" style={{ color: 'var(--text-tertiary)' }}>Select a function to interact</span>
                  </div>
                )}
              </div>
            </div>

            {!showRead && !showWrite && (
              <p className="text-[14px] mt-6 text-center font-mono" style={{ color: 'var(--text-tertiary)' }}>Enable Read or Write to see functions</p>
            )}
            </>
            )}
          </div>
          )
        })()}
      </div>
    </div>
  )
}
