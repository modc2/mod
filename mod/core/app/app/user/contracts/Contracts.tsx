"use client";

import { useState, useEffect, useRef } from 'react'
import { userContext } from '@/context'
import { ethers } from 'ethers'
import { getChainConfig } from '@/network/chainConfig'
import { motion, AnimatePresence } from 'framer-motion'
import { CopyButton } from '@/ui/CopyButton'
import RegistryABI from '@/contracts//registry/Registry.sol/Registry.json'
import { Zap, Eye, Edit3, RefreshCw, ChevronDown, ChevronUp, Sparkles } from 'lucide-react'

const loadAbiFromIpfs = async (client: any, cid: string) => {
  try {
    let abiData = await client.call('get', { cid })
    // if abi is in the filed then get the abi
    if (abiData.abi !== null) {
      abiData = abiData.abi
    }
    return abiData
  } catch (err) {
    console.error('Error fetching ABI from IPFS:', err)
    throw new Error('Failed to fetch ABI from IPFS')
  }
}

interface ContractMetadata {
  abi: any
  name: string
  color?: string
  emoji?: string
  address?: string
  abiCid?: string
}

interface UserMod {
  modId: string
  name: string
  data: string
  owner: string
}

const serializeBigInt = (obj: any): any => {
  if (obj === null || obj === undefined) return obj
  if (typeof obj === 'bigint') return obj.toString()
  if (Array.isArray(obj)) return obj.map(serializeBigInt)
  if (typeof obj === 'object') {
    const serialized: any = {}
    for (const key in obj) {
      serialized[key] = serializeBigInt(obj[key])
    }
    return serialized
  }
  return obj
}

export default function ContractsInterface() {
  const { client, user } = userContext()
  const [selectedContract, setSelectedContract] = useState<string>('')
  const [selectedFunction, setSelectedFunction] = useState<string>('')
  const [functionParams, setFunctionParams] = useState<Record<string, any>>({})
  const [result, setResult] = useState<any>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [contracts, setContracts] = useState<Record<string, ContractMetadata>>({})
  const [loadingAbi, setLoadingAbi] = useState<string | null>(null)
  const [searchTerm, setSearchTerm] = useState('')
  const [showContractDropdown, setShowContractDropdown] = useState(false)
  const [showNetworkInfo, setShowNetworkInfo] = useState(true)
  const [showContractInfo, setShowContractInfo] = useState(true)
  const [userMods, setUserMods] = useState<UserMod[]>([])
  const [loadingMods, setLoadingMods] = useState(false)
  const [selectedModId, setSelectedModId] = useState<string>('')
  const [editingModData, setEditingModData] = useState<string>('')

  const contractDropdownRef = useRef<HTMLDivElement>(null)

  const network = 'testnet'
  const chainConfig = getChainConfig()

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (contractDropdownRef.current && !contractDropdownRef.current.contains(event.target as Node)) {
        setShowContractDropdown(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  useEffect(() => {
    if (!chainConfig?.contracts) return
    const contractsMetadata: Record<string, ContractMetadata> = {}
    for (const [contractName, contractInfo] of Object.entries(chainConfig.contracts)) {
      const info = contractInfo as any
      contractsMetadata[contractName] = {
        abi: contractName === 'Registry' ? RegistryABI.abi : null,
        name: contractName,
        color: getContractColor(contractName),
        emoji: getContractEmoji(contractName),
        address: info.address,
        abiCid: info.abi
      }
    }
    setContracts(contractsMetadata)
  }, [chainConfig])

  useEffect(() => {
    const loadContractAbi = async () => {
      if (!selectedContract || !client || typeof window === 'undefined') return
      const contract = contracts[selectedContract]
      if (!contract || contract.abi) return
      if (!contract.abiCid) {
        setError('No ABI CID found for this contract')
        return
      }
      setLoadingAbi(selectedContract)
      setError(null)
      try {
        const abiData = await loadAbiFromIpfs(client, contract.abiCid)
        setContracts(prev => ({
          ...prev,
          [selectedContract]: {
            ...prev[selectedContract],
            abi: abiData
          }
        }))
      } catch (err) {
        console.error(`Failed to load ABI for ${selectedContract}:`, err)
        setError(`Failed to load ABI for ${selectedContract}`)
      } finally {
        setLoadingAbi(null)
      }
    }
    loadContractAbi()
  }, [selectedContract, client])

  const fetchUserMods = async () => {
    if (!user?.key || !selectedContract || selectedContract !== 'Registry') return
    const contract = contracts['Registry']
    if (!contract?.abi || !contract?.address) return

    setLoadingMods(true)
    try {
      if (!window.ethereum) throw new Error('MetaMask not detected')
      const provider = new ethers.BrowserProvider(window.ethereum)
      const registryContract = new ethers.Contract(contract.address, contract.abi, provider)
      const modIds = await registryContract.getUserMods(user!.key)

      const modsData: UserMod[] = []
      for (const modId of modIds) {
        const [owner, data] = await registryContract.getMod(modId)
        modsData.push({
          modId: modId.toString(),
          name: `Mod #${modId.toString()}`,
          data: data,
          owner: owner
        })
      }
      setUserMods(modsData)
    } catch (err: any) {
      console.error('Failed to fetch user mods:', err)
      setError(err.message || 'Failed to fetch user mods')
    } finally {
      setLoadingMods(false)
    }
  }

  useEffect(() => {
    if (selectedContract === 'Registry' && user?.key) {
      fetchUserMods()
    }
  }, [selectedContract, user?.key])

  const handleUpdateMod = async () => {
    if (!selectedModId || !editingModData || !user?.key) return

    setLoading(true)
    setError(null)
    try {
      const contract = contracts['Registry']
      if (!contract?.abi || !contract?.address) throw new Error('Registry contract not loaded')

      if (!window.ethereum) throw new Error('MetaMask not detected')
      const provider = new ethers.BrowserProvider(window.ethereum)
      const signer = await provider.getSigner(user!.key)
      const registryContract = new ethers.Contract(contract.address, contract.abi, signer)

      const tx = await registryContract.updateMod(selectedModId, editingModData)
      const receipt = await tx.wait()

      setResult({ success: true, txHash: receipt.hash, modId: selectedModId })
      await fetchUserMods()
      setSelectedModId('')
      setEditingModData('')
    } catch (err: any) {
      console.error('Update mod error:', err)
      setError(err.message || 'Failed to update mod')
    } finally {
      setLoading(false)
    }
  }

  const getContractColor = (name: string): string => {
    const colors: Record<string, string> = {
      BlocTime: '#00ff88',
      Market: '#ff0088',
      Registry: '#0088ff',
      TokenGate: '#ff8800',
      NativeToken: '#8800ff',
      USDC: '#2775ca',
      USDT: '#26a17b',
      ManualPriceOracle: '#ffaa00'
    }
    return colors[name] || '#ffffff'
  }

  const getContractEmoji = (name: string): string => {
    const emojis: Record<string, string> = {
      BlocTime: '⏰',
      Market: '🏪',
      Registry: '📋',
      TokenGate: '🚪',
      NativeToken: '💎',
      USDC: '💵',
      USDT: '💴',
      ManualPriceOracle: '🔮'
    }
    return emojis[name] || '📄'
  }

  const getContractFunctions = () => {
    if (!selectedContract || !contracts[selectedContract]) return []
    const contract = contracts[selectedContract]
    if (!contract || !contract.abi) return []
    return contract.abi
      .filter((item: any) => item.type === 'function')
      .map((item: any) => ({
        name: item.name,
        inputs: item.inputs || [],
        outputs: item.outputs || [],
        stateMutability: item.stateMutability
      }))
  }

  const getFunctionIcon = (stateMutability: string) => {
    if (stateMutability === 'view' || stateMutability === 'pure') {
      return <Eye className="w-4 h-4" />
    }
    return <Zap className="w-4 h-4" />
  }

  const getFunctionColor = (stateMutability: string) => {
    if (stateMutability === 'view' || stateMutability === 'pure') {
      return '#06b6d4' // cyan
    }
    return '#f59e0b' // amber
  }

  const handleExecute = async () => {
    if (!selectedContract || !selectedFunction || !client) return
    setLoading(true)
    setError(null)
    setResult(null)
    try {
      const contract = contracts[selectedContract]
      if (!contract || !contract.abi) throw new Error('Contract ABI not loaded')
      if (!contract.address) throw new Error('Contract address not found')
      const functionAbi = contract.abi.find(
        (item: any) => item.type === 'function' && item.name === selectedFunction
      )
      if (!functionAbi) throw new Error('Function not found in ABI')

      const params = functionAbi.inputs.map((input: any) => {
        if (functionParams[input.name] !== undefined && functionParams[input.name] !== '') {
          return functionParams[input.name]
        }
        if (input.type === 'address' && user?.key) {
          return user.key
        }
        return ''
      })

      if (!window.ethereum) throw new Error('MetaMask not detected')
      const provider = new ethers.BrowserProvider(window.ethereum)
      const signer = await provider.getSigner(user!.key)
      const contractInstance = new ethers.Contract(contract.address, contract.abi, signer)
      let txResult
      if (functionAbi.stateMutability === 'view' || functionAbi.stateMutability === 'pure') {
        txResult = await contractInstance[selectedFunction](...params)
      } else {
        const tx = await contractInstance[selectedFunction](...params)
        const receipt = await tx.wait()
        txResult = receipt
      }
      setResult(serializeBigInt(txResult))
      if (selectedContract === 'Registry') {
        await fetchUserMods()
      }
    } catch (err: any) {
      console.error('Contract execution error:', err)
      setError(err.message || 'Failed to execute contract function')
    } finally {
      setLoading(false)
    }
  }

  const selectedFunctionData = getContractFunctions().find((f: any) => f.name === selectedFunction)
  const selectedContractData = selectedContract ? contracts[selectedContract] : null
  const filteredContracts = Object.entries(contracts).filter(([key, contract]) =>
    contract.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
    contract.address?.toLowerCase().includes(searchTerm.toLowerCase())
  )

  return (
    <div className="space-y-6" style={{ fontFamily: 'IBM Plex Mono, monospace' }}>
      {/* Header */}
      <motion.div
        initial={{ opacity: 0, y: -20 }}
        animate={{ opacity: 1, y: 0 }}
        className="text-center"
      >
        <h1 className="text-5xl font-black mb-3 bg-gradient-to-r from-cyan-400 via-purple-500 to-pink-500 bg-clip-text text-transparent flex items-center justify-center gap-3">
          <Sparkles className="w-10 h-10 text-cyan-400" />
          CONTRACTS
          <Sparkles className="w-10 h-10 text-pink-500" />
        </h1>
        <p className="text-sm text-gray-400 font-mono tracking-wider">Blockchain Interaction Hub</p>
      </motion.div>

      {/* Network Info Card */}
      <motion.div
        initial={{ opacity: 0, x: -20 }}
        animate={{ opacity: 1, x: 0 }}
        className="relative"
      >
        <div
          className="backdrop-blur-xl rounded-2xl border-2 transition-all duration-300 overflow-hidden"
          style={{
            background: 'linear-gradient(135deg, rgba(16, 185, 129, 0.1) 0%, rgba(16, 185, 129, 0.05) 100%)',
            borderColor: '#10b98180',
            boxShadow: '0 0 30px rgba(16, 185, 129, 0.2)'
          }}
        >
          <button
            onClick={() => setShowNetworkInfo(!showNetworkInfo)}
            className="w-full flex items-center justify-between p-4 hover:bg-white/5 transition-all group"
          >
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-green-500/30 to-emerald-500/30 flex items-center justify-center border-2 border-green-500/50">
                <span className="text-2xl">🌐</span>
              </div>
              <h3 className="text-xl font-black text-green-400 tracking-wide">Network</h3>
            </div>
            <div className="text-green-400 transition-transform duration-300" style={{ transform: showNetworkInfo ? 'rotate(180deg)' : 'rotate(0deg)' }}>
              <ChevronDown className="w-6 h-6" />
            </div>
          </button>
          <AnimatePresence>
            {showNetworkInfo && (
              <motion.div
                initial={{ height: 0, opacity: 0 }}
                animate={{ height: 'auto', opacity: 1 }}
                exit={{ height: 0, opacity: 0 }}
                transition={{ duration: 0.2 }}
                className="px-4 pb-4 space-y-3"
              >
                <div className="flex items-center gap-3 bg-black/40 rounded-xl p-3 border border-green-500/30">
                  <span className="text-green-400/70 font-bold text-xs min-w-[80px]">Network:</span>
                  <span className="font-mono text-sm text-green-400 font-black">{network.toUpperCase()}</span>
                </div>
                <div className="flex items-center gap-3 bg-black/40 rounded-xl p-3 border border-green-500/30">
                  <span className="text-green-400/70 font-bold text-xs min-w-[80px]">Connected:</span>
                  <span className="font-mono text-sm text-white/90 truncate flex-1">{user?.key || 'Not connected'}</span>
                  {user?.key && <CopyButton text={user.key} size="sm" />}
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </motion.div>

      {/* Contract Info Card */}
      {selectedContract && selectedContractData && (
        <motion.div
          initial={{ opacity: 0, x: 20 }}
          animate={{ opacity: 1, x: 0 }}
          className="relative"
        >
          <div
            className="backdrop-blur-xl rounded-2xl border-2 transition-all duration-300 overflow-hidden"
            style={{
              background: `linear-gradient(135deg, ${selectedContractData.color}20 0%, ${selectedContractData.color}10 100%)`,
              borderColor: `${selectedContractData.color}80`,
              boxShadow: `0 0 30px ${selectedContractData.color}40`
            }}
          >
            <button
              onClick={() => setShowContractInfo(!showContractInfo)}
              className="w-full flex items-center justify-between p-4 hover:bg-white/5 transition-all"
            >
              <div className="flex items-center gap-3">
                <div
                  className="w-10 h-10 rounded-xl flex items-center justify-center border-2"
                  style={{
                    background: `linear-gradient(135deg, ${selectedContractData.color}30 0%, ${selectedContractData.color}20 100%)`,
                    borderColor: `${selectedContractData.color}60`
                  }}
                >
                  <span className="text-2xl">{selectedContractData.emoji}</span>
                </div>
                <h3 className="text-xl font-black tracking-wide" style={{ color: selectedContractData.color }}>
                  {selectedContractData.name}
                </h3>
              </div>
              <div className="transition-transform duration-300" style={{
                transform: showContractInfo ? 'rotate(180deg)' : 'rotate(0deg)',
                color: selectedContractData.color
              }}>
                <ChevronDown className="w-6 h-6" />
              </div>
            </button>
            <AnimatePresence>
              {showContractInfo && (
                <motion.div
                  initial={{ height: 0, opacity: 0 }}
                  animate={{ height: 'auto', opacity: 1 }}
                  exit={{ height: 0, opacity: 0 }}
                  transition={{ duration: 0.2 }}
                  className="px-4 pb-4 space-y-3"
                >
                  <div className="flex items-center gap-3 bg-black/40 rounded-xl p-3 border" style={{ borderColor: `${selectedContractData.color}30` }}>
                    <span className="font-bold text-xs min-w-[80px]" style={{ color: `${selectedContractData.color}90` }}>Address:</span>
                    <span className="font-mono text-sm text-white/90 truncate flex-1">{selectedContractData.address}</span>
                    <CopyButton text={selectedContractData.address || ''} size="sm" />
                  </div>
                  <div className="flex items-center gap-3 bg-black/40 rounded-xl p-3 border" style={{ borderColor: `${selectedContractData.color}30` }}>
                    <span className="font-bold text-xs min-w-[80px]" style={{ color: `${selectedContractData.color}90` }}>ABI CID:</span>
                    <span className="font-mono text-sm text-white/90 truncate flex-1">{selectedContractData.abiCid}</span>
                    <CopyButton text={selectedContractData.abiCid || ''} size="sm" />
                  </div>
                  <div className="flex items-center gap-3 bg-black/40 rounded-xl p-3 border" style={{ borderColor: `${selectedContractData.color}30` }}>
                    <span className="font-bold text-xs min-w-[80px]" style={{ color: `${selectedContractData.color}90` }}>Status:</span>
                    <span className="font-mono text-sm font-bold">
                      {selectedContractData.abi ? '✅ Loaded' : '⏳ Loading...'}
                    </span>
                  </div>
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        </motion.div>
      )}

      {/* User Mods Section */}
      {selectedContract === 'Registry' && userMods.length > 0 && (
        <motion.div
          initial={{ opacity: 0, scale: 0.95 }}
          animate={{ opacity: 1, scale: 1 }}
          className="backdrop-blur-xl rounded-2xl border-2 border-purple-500/50 p-5"
          style={{
            background: 'linear-gradient(135deg, rgba(168, 85, 247, 0.15) 0%, rgba(168, 85, 247, 0.05) 100%)',
            boxShadow: '0 0 40px rgba(168, 85, 247, 0.3)'
          }}
        >
          <div className="flex items-center gap-3 mb-5">
            <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-purple-500/30 to-pink-500/30 flex items-center justify-center border-2 border-purple-500/50">
              <span className="text-2xl">📝</span>
            </div>
            <h3 className="text-2xl font-black text-purple-400 tracking-wide">Your Mods</h3>
            <button
              onClick={fetchUserMods}
              disabled={loadingMods}
              className="ml-auto p-2 bg-purple-500/20 hover:bg-purple-500/30 rounded-xl border border-purple-500/40 transition-all disabled:opacity-50"
            >
              <RefreshCw className={`w-5 h-5 text-purple-400 ${loadingMods ? 'animate-spin' : ''}`} />
            </button>
          </div>
          <div className="space-y-3">
            {userMods.map((mod, index) => (
              <motion.div
                key={mod.modId}
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: index * 0.05 }}
                className="bg-black/60 backdrop-blur-sm border-2 border-purple-500/40 rounded-xl p-4 hover:border-purple-400/60 transition-all"
                style={{
                  boxShadow: '0 0 20px rgba(168, 85, 247, 0.2)'
                }}
              >
                <div className="flex items-center justify-between mb-3">
                  <div className="flex items-center gap-3">
                    <span className="text-2xl">🎯</span>
                    <span className="font-black text-purple-300 text-lg">{mod.name}</span>
                  </div>
                  <button
                    onClick={() => {
                      setSelectedModId(mod.modId)
                      setEditingModData(mod.data)
                    }}
                    className="px-4 py-2 bg-gradient-to-r from-purple-500/30 to-pink-500/30 hover:from-purple-500/40 hover:to-pink-500/40 rounded-xl border-2 border-purple-400/50 font-bold text-sm text-purple-300 transition-all flex items-center gap-2 hover:scale-105"
                  >
                    <Edit3 className="w-4 h-4" />
                    Edit
                  </button>
                </div>
                <div className="space-y-2 text-sm">
                  <div className="flex items-center gap-2 bg-purple-950/30 rounded-lg p-2">
                    <span className="text-purple-400/70 font-bold text-xs">ID:</span>
                    <span className="text-purple-300 font-mono">{mod.modId}</span>
                  </div>
                  <div className="flex items-center gap-2 bg-purple-950/30 rounded-lg p-2">
                    <span className="text-purple-400/70 font-bold text-xs">Data:</span>
                    <span className="text-purple-300 font-mono truncate flex-1">{mod.data}</span>
                    <CopyButton text={mod.data} size="sm" />
                  </div>
                </div>
              </motion.div>
            ))}
          </div>
        </motion.div>
      )}

      {/* Edit Mod Panel */}
      {selectedModId && (
        <motion.div
          initial={{ opacity: 0, scale: 0.95 }}
          animate={{ opacity: 1, scale: 1 }}
          className="backdrop-blur-xl rounded-2xl border-2 border-orange-500/50 p-5"
          style={{
            background: 'linear-gradient(135deg, rgba(251, 146, 60, 0.15) 0%, rgba(251, 146, 60, 0.05) 100%)',
            boxShadow: '0 0 40px rgba(251, 146, 60, 0.3)'
          }}
        >
          <div className="flex items-center gap-3 mb-5">
            <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-orange-500/30 to-red-500/30 flex items-center justify-center border-2 border-orange-500/50">
              <Edit3 className="w-5 h-5 text-orange-400" />
            </div>
            <h3 className="text-2xl font-black text-orange-400 tracking-wide">Edit Mod #{selectedModId}</h3>
          </div>
          <div className="space-y-4">
            <div>
              <label className="block text-sm font-bold mb-3 text-orange-300 tracking-wide">Mod Data</label>
              <textarea
                value={editingModData}
                onChange={(e) => setEditingModData(e.target.value)}
                className="w-full bg-black/60 border-2 border-orange-500/50 rounded-xl px-4 py-3 text-white font-mono focus:border-orange-400 focus:outline-none focus:ring-2 focus:ring-orange-500/30 min-h-[120px] transition-all"
                placeholder="Enter new mod data..."
                style={{
                  boxShadow: '0 0 15px rgba(251, 146, 60, 0.2)'
                }}
              />
            </div>
            <div className="flex gap-3">
              <button
                onClick={handleUpdateMod}
                disabled={loading || !editingModData}
                className="flex-1 bg-gradient-to-r from-orange-500/40 to-red-600/40 hover:from-orange-500/50 hover:to-red-600/50 text-white text-base font-black py-3 rounded-xl border-2 border-orange-400/50 disabled:opacity-50 transition-all hover:scale-[1.02] flex items-center justify-center gap-2"
                style={{
                  boxShadow: '0 0 20px rgba(251, 146, 60, 0.3)'
                }}
              >
                {loading ? (
                  <>
                    <RefreshCw className="w-5 h-5 animate-spin" />
                    UPDATING...
                  </>
                ) : (
                  <>
                    <Zap className="w-5 h-5" />
                    UPDATE MOD
                  </>
                )}
              </button>
              <button
                onClick={() => {
                  setSelectedModId('')
                  setEditingModData('')
                }}
                className="px-6 bg-gray-700/40 hover:bg-gray-600/50 text-white text-base font-black py-3 rounded-xl border-2 border-gray-500/50 transition-all hover:scale-[1.02]"
              >
                CANCEL
              </button>
            </div>
          </div>
        </motion.div>
      )}

      {/* Contract Selection */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* Contract Selector */}
        <div className="relative" ref={contractDropdownRef}>
          <label className="block text-sm font-bold mb-2 text-cyan-400 tracking-wide">Select Contract</label>
          <input
            type="text"
            placeholder="🔍 Search contracts..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            onFocus={() => setShowContractDropdown(true)}
            className="w-full bg-gradient-to-r from-black/80 to-cyan-950/30 backdrop-blur-sm border-2 border-cyan-500/50 rounded-xl px-4 py-3 text-white font-bold focus:border-cyan-400 focus:outline-none focus:ring-2 focus:ring-cyan-500/30 transition-all"
            style={{
              boxShadow: '0 0 20px rgba(6, 182, 212, 0.2)'
            }}
          />
          {showContractDropdown && filteredContracts.length > 0 && (
            <div
              className="absolute w-full mt-2 bg-gray-900/95 backdrop-blur-xl border-2 border-cyan-400/60 rounded-xl shadow-2xl max-h-80 overflow-y-auto z-50"
              style={{
                boxShadow: '0 0 40px rgba(6, 182, 212, 0.4)'
              }}
            >
              {filteredContracts.map(([key, contract]) => (
                <button
                  key={key}
                  onClick={() => {
                    setSelectedContract(key)
                    setSelectedFunction('')
                    setFunctionParams({})
                    setResult(null)
                    setError(null)
                    setSearchTerm(contract.name)
                    setShowContractDropdown(false)
                  }}
                  className="w-full text-left px-4 py-4 hover:bg-white/10 transition-all border-l-4 group"
                  style={{
                    borderLeftColor: contract.color
                  }}
                >
                  <div className="flex items-center gap-3">
                    <div
                      className="w-12 h-12 rounded-xl flex items-center justify-center border-2 group-hover:scale-110 transition-transform"
                      style={{
                        background: `linear-gradient(135deg, ${contract.color}30 0%, ${contract.color}20 100%)`,
                        borderColor: `${contract.color}60`
                      }}
                    >
                      <span className="text-2xl">{contract.emoji}</span>
                    </div>
                    <div className="flex-1">
                      <div className="font-black text-lg" style={{ color: contract.color }}>{contract.name}</div>
                      <div className="text-xs text-gray-500 font-mono">
                        {contract.address?.slice(0, 12)}...{contract.address?.slice(-10)}
                      </div>
                    </div>
                    {loadingAbi === key && (
                      <RefreshCw className="w-5 h-5 text-yellow-400 animate-spin" />
                    )}
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Function Selector */}
        {selectedContract && selectedContractData?.abi && (
          <div>
            <label className="block text-sm font-bold mb-2 text-purple-400 tracking-wide">Select Function</label>
            <select
              value={selectedFunction}
              onChange={(e) => {
                setSelectedFunction(e.target.value)
                setFunctionParams({})
                setResult(null)
                setError(null)
              }}
              className="w-full bg-gradient-to-r from-black/80 to-purple-950/30 backdrop-blur-sm border-2 border-purple-500/50 rounded-xl px-4 py-3 text-white font-bold focus:border-purple-400 focus:outline-none focus:ring-2 focus:ring-purple-500/30 transition-all"
              style={{
                boxShadow: '0 0 20px rgba(168, 85, 247, 0.2)'
              }}
            >
              <option value="">⚡ Select function...</option>
              {getContractFunctions().map((func: any) => {
                const isReadOnly = func.stateMutability === 'view' || func.stateMutability === 'pure'
                return (
                  <option key={func.name} value={func.name}>
                    {isReadOnly ? '👁️' : '⚡'} {func.name}
                  </option>
                )
              })}
            </select>
          </div>
        )}
      </div>

      {/* Function Parameters */}
      <AnimatePresence>
        {selectedContract && selectedFunctionData && selectedFunctionData.inputs.length > 0 && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            className="backdrop-blur-xl rounded-2xl border-2 border-pink-500/50 p-5"
            style={{
              background: 'linear-gradient(135deg, rgba(236, 72, 153, 0.15) 0%, rgba(236, 72, 153, 0.05) 100%)',
              boxShadow: '0 0 40px rgba(236, 72, 153, 0.3)'
            }}
          >
            <div className="flex items-center gap-3 mb-5">
              <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-pink-500/30 to-purple-500/30 flex items-center justify-center border-2 border-pink-500/50">
                <span className="text-2xl">🎯</span>
              </div>
              <h3 className="text-2xl font-black text-pink-400 tracking-wide">Parameters</h3>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {selectedFunctionData.inputs.map((input: any, index: number) => (
                <div key={input.name}>
                  <label className="block text-sm font-bold mb-2 text-pink-300 tracking-wide">
                    {input.name} <span className="text-gray-500 text-xs">({input.type})</span>
                    {input.type === 'address' && index === 0 && user?.key && (
                      <span className="text-xs text-emerald-400 ml-2 normal-case">✓ Auto-filled</span>
                    )}
                  </label>
                  <input
                    type="text"
                    value={functionParams[input.name] || ''}
                    onChange={(e) => setFunctionParams({ ...functionParams, [input.name]: e.target.value })}
                    className="w-full bg-black/60 border-2 border-pink-500/50 rounded-xl px-4 py-3 text-white font-mono focus:border-pink-400 focus:outline-none focus:ring-2 focus:ring-pink-500/30 transition-all"
                    placeholder={input.type === 'address' && index === 0 && user?.key ? user.key : input.type}
                    style={{
                      boxShadow: '0 0 15px rgba(236, 72, 153, 0.2)'
                    }}
                  />
                </div>
              ))}
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Execute Button */}
      {selectedFunction && selectedFunctionData && (
        <motion.button
          whileHover={{ scale: 1.02 }}
          whileTap={{ scale: 0.98 }}
          onClick={handleExecute}
          disabled={loading}
          className="w-full font-black py-4 rounded-xl border-2 disabled:opacity-50 transition-all flex items-center justify-center gap-3 text-xl tracking-wide"
          style={{
            background: `linear-gradient(135deg, ${getFunctionColor(selectedFunctionData.stateMutability)}40 0%, ${getFunctionColor(selectedFunctionData.stateMutability)}20 100%)`,
            borderColor: `${getFunctionColor(selectedFunctionData.stateMutability)}80`,
            color: getFunctionColor(selectedFunctionData.stateMutability),
            boxShadow: `0 0 30px ${getFunctionColor(selectedFunctionData.stateMutability)}40`
          }}
        >
          {loading ? (
            <>
              <RefreshCw className="w-6 h-6 animate-spin" />
              Executing...
            </>
          ) : (
            <>
              {getFunctionIcon(selectedFunctionData.stateMutability)}
              {selectedFunctionData.stateMutability === 'view' || selectedFunctionData.stateMutability === 'pure' ? 'Read' : 'Execute'}
            </>
          )}
        </motion.button>
      )}

      {/* Error Display */}
      {error && (
        <motion.div
          initial={{ opacity: 0, scale: 0.95 }}
          animate={{ opacity: 1, scale: 1 }}
          className="bg-gradient-to-r from-red-900/60 to-red-800/40 backdrop-blur-xl border-2 border-red-500/50 rounded-2xl p-5"
          style={{
            boxShadow: '0 0 40px rgba(239, 68, 68, 0.3)'
          }}
        >
          <div className="flex items-center gap-3 mb-3">
            <div className="w-10 h-10 rounded-xl bg-red-500/30 flex items-center justify-center border-2 border-red-500/50">
              <span className="text-2xl">❌</span>
            </div>
            <h3 className="text-xl font-black text-red-400 tracking-wide">Error</h3>
          </div>
          <p className="text-red-300 font-mono text-sm bg-black/40 p-4 rounded-xl border border-red-500/30">{error}</p>
        </motion.div>
      )}

      {/* Result Display */}
      {result && (
        <motion.div
          initial={{ opacity: 0, scale: 0.95 }}
          animate={{ opacity: 1, scale: 1 }}
          className="bg-gradient-to-r from-emerald-900/60 to-emerald-800/40 backdrop-blur-xl border-2 border-emerald-500/50 rounded-2xl p-5"
          style={{
            boxShadow: '0 0 40px rgba(16, 185, 129, 0.3)'
          }}
        >
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-xl bg-emerald-500/30 flex items-center justify-center border-2 border-emerald-500/50">
                <span className="text-2xl">✅</span>
              </div>
              <h3 className="text-xl font-black text-emerald-400 tracking-wide">Result</h3>
            </div>
            <CopyButton text={JSON.stringify(result, null, 2)} size="md" />
          </div>
          <pre className="text-sm overflow-auto bg-black/60 p-4 rounded-xl border-2 border-emerald-500/30 text-emerald-200 font-mono max-h-64">
            {JSON.stringify(result, null, 2)}
          </pre>
        </motion.div>
      )}
    </div>
  )
}
