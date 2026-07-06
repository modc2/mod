"use client";

import { useState, useEffect, useCallback, useMemo } from 'react'
import dynamic from 'next/dynamic'
import { toast } from 'react-toastify'
import { ethers } from 'ethers'
import {
  CubeIcon,
  ArrowPathIcon,
  BoltIcon,
  UserPlusIcon,
  SignalIcon,
  ChevronUpIcon,
  ChevronDownIcon,
  ChevronUpDownIcon,
  PlayIcon,
  LockClosedIcon,
  LockOpenIcon,
  BanknotesIcon,
  GiftIcon,
  TrashIcon,
  ServerStackIcon,
  ShieldCheckIcon,
  XCircleIcon,
  SparklesIcon,
  CheckIcon,
  PencilIcon,
} from '@heroicons/react/24/outline'
import { motion } from 'framer-motion'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8849'

// ── Types ───────────────────────────────────────────────────────────────

interface ConsensusState {
  currentBlock: number
  lastEmissionBlock: number
  totalBlocktime: number
  emissionRate: string
  decayBps: number
  epochLength: number
}

interface ValidatorInfo {
  key: string
  keyHash: string
  keyType: number
  registeredBlock: number
  commissionBps: number
  active: boolean
  lastSeenBlock: number
  blocktimeScore: number
  earned: string
  balance: string
  totalSTT: string
}

interface StakePosition {
  stakeId: number
  staker: string
  validatorKeyHash: string
  amount: string
  startBlock: number
  lockBlocks: number
  stakeTimeBalance: string
  blocksRemaining: number
}

interface SubnetInfo {
  id: number
  owner: string
  name: string
  subnet: string
  stakeTime: string
  consensus: string
  registeredBlock: number
  active: boolean
  stakeScore: string
  immune: boolean
}

interface WeakestSubnet {
  id: number
  score: string
  found: boolean
}

// ── API helper ──────────────────────────────────────────────────────────

async function api(fn: string, params: Record<string, any> = {}) {
  const res = await fetch(`${API_URL}/${fn}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || err.error || 'Request failed')
  }
  const data = await res.json()
  return data.result !== undefined ? data.result : data
}

const KEY_TYPE_LABELS = ['ECDSA', 'Ed25519', 'Sr25519']

const fmtEth = (wei: string) => {
  if (!wei || wei === '0') return '0'
  const val = Number(ethers.formatEther(wei))
  return val < 0.01 ? val.toExponential(2) : val.toLocaleString(undefined, { maximumFractionDigits: 2 })
}

const fmtAddr = (s: string) => s.length > 16 ? `${s.slice(0, 8)}...${s.slice(-6)}` : s

// ── Tabs ────────────────────────────────────────────────────────────────

type Tab = 'subnets' | 'validators' | 'staking' | 'rewards'

// ── Main Page ───────────────────────────────────────────────────────────

function StakeTimeApp() {
  const [consensus, setConsensus] = useState<ConsensusState | null>(null)
  const [validators, setValidators] = useState<ValidatorInfo[]>([])
  const [stakes, setStakes] = useState<StakePosition[]>([])
  const [stakerRewards, setStakerRewards] = useState('0')
  const [loading, setLoading] = useState(false)
  const [connected, setConnected] = useState(false)
  const [account, setAccount] = useState('')
  const [tab, setTab] = useState<Tab>('subnets')

  // Subnets
  const [subnets, setSubnets] = useState<SubnetInfo[]>([])
  const [weakest, setWeakest] = useState<WeakestSubnet | null>(null)
  const [subName, setSubName] = useState('')
  const [subSubnet, setSubSubnet] = useState('')
  const [subStakeTime, setSubStakeTime] = useState('')
  const [subConsensus, setSubConsensus] = useState('')
  const [registeringSub, setRegisteringSub] = useState(false)
  const [registrationCost, setRegistrationCost] = useState('0')

  // LLM subnet creation
  const [llmPrompt, setLlmPrompt] = useState('')
  const [llmParams, setLlmParams] = useState<any>(null)
  const [llmGenerating, setLlmGenerating] = useState(false)
  const [llmDeploying, setLlmDeploying] = useState(false)
  const [llmEditing, setLlmEditing] = useState(false)

  // Register form
  const [regKey, setRegKey] = useState('')
  const [regKeyType, setRegKeyType] = useState(0)
  const [regCommission, setRegCommission] = useState('10')
  const [registering, setRegistering] = useState(false)

  // Stake form
  const [stakeValidator, setStakeValidator] = useState('')
  const [stakeAmount, setStakeAmount] = useState('')
  const [stakeLockBlocks, setStakeLockBlocks] = useState('0')
  const [staking, setStaking] = useState(false)

  // Search/sort
  const [search, setSearch] = useState('')
  type SortKey = 'score' | 'earned' | 'lastSeen' | 'totalSTT'
  type SortDir = 'asc' | 'desc'
  const [sortKey, setSortKey] = useState<SortKey>('score')
  const [sortDir, setSortDir] = useState<SortDir>('desc')

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

  // ── Data fetching ───────────────────────────────────────────────────

  const fetchAll = useCallback(async () => {
    setLoading(true)
    try {
      const [consensusData, validatorsData, subnetsData, weakestData, costData] = await Promise.all([
        api('get_consensus').catch(() => null),
        api('get_validators').catch(() => []),
        api('get_subnets').catch(() => []),
        api('get_weakest_subnet').catch(() => null),
        api('get_registration_cost').catch(() => '0'),
      ])
      if (consensusData) setConsensus(consensusData)
      if (validatorsData) setValidators(validatorsData)
      if (subnetsData) setSubnets(subnetsData)
      if (weakestData) setWeakest(weakestData)
      if (costData) setRegistrationCost(typeof costData === 'string' ? costData : '0')
    } catch { /* ignore */ }
    setLoading(false)
  }, [])

  const fetchStakes = useCallback(async () => {
    if (!account) return
    try {
      const [stakesData, rewards] = await Promise.all([
        api('get_user_stakes', { address: account }).catch(() => []),
        api('get_staker_rewards', { address: account }).catch(() => '0'),
      ])
      setStakes(stakesData || [])
      setStakerRewards(typeof rewards === 'string' ? rewards : '0')
    } catch { /* ignore */ }
  }, [account])

  useEffect(() => { fetchAll() }, [fetchAll])
  useEffect(() => { if (account) fetchStakes() }, [account, fetchStakes])

  useEffect(() => {
    const iv = setInterval(() => { fetchAll(); if (account) fetchStakes() }, 10000)
    return () => clearInterval(iv)
  }, [fetchAll, fetchStakes, account])

  // ── Actions ─────────────────────────────────────────────────────────

  const handleRegister = useCallback(async () => {
    if (!regKey.trim()) { toast.error('Enter a key'); return }
    setRegistering(true)
    try {
      await api('register', {
        key: regKey.trim(),
        key_type: regKeyType,
        commission_bps: Math.round(parseFloat(regCommission) * 100),
      })
      toast.success('Validator registered')
      setRegKey('')
      fetchAll()
    } catch (err: any) {
      toast.error(err?.message || 'Registration failed')
    }
    setRegistering(false)
  }, [regKey, regKeyType, regCommission, fetchAll])

  const handleCheckin = useCallback(async (key: string) => {
    try {
      await api('checkin', { key })
      toast.success('Checked in')
      fetchAll()
    } catch (err: any) {
      toast.error(err?.message || 'Checkin failed')
    }
  }, [fetchAll])

  const handleStake = useCallback(async () => {
    if (!stakeValidator.trim() || !stakeAmount.trim()) {
      toast.error('Enter validator key and amount')
      return
    }
    setStaking(true)
    try {
      await api('stake_on', {
        validator_key: stakeValidator.trim(),
        amount: stakeAmount.trim(),
        lock_blocks: parseInt(stakeLockBlocks) || 0,
      })
      toast.success('Staked successfully')
      setStakeAmount('')
      fetchAll()
      fetchStakes()
    } catch (err: any) {
      toast.error(err?.message || 'Staking failed')
    }
    setStaking(false)
  }, [stakeValidator, stakeAmount, stakeLockBlocks, fetchAll, fetchStakes])

  const handleUnstake = useCallback(async (stakeId: number) => {
    try {
      await api('unstake_from', { stake_id: stakeId })
      toast.success('Unstaked')
      fetchAll()
      fetchStakes()
    } catch (err: any) {
      toast.error(err?.message || 'Unstake failed')
    }
  }, [fetchAll, fetchStakes])

  const handleClaimStaker = useCallback(async () => {
    try {
      await api('claim_staker_rewards')
      toast.success('Rewards claimed')
      fetchStakes()
    } catch (err: any) {
      toast.error(err?.message || 'Claim failed')
    }
  }, [fetchStakes])

  const handleProduceBlock = useCallback(async () => {
    try {
      const result = await api('produce_block')
      toast.success(`Block #${result?.block || '?'} produced`)
      fetchAll()
    } catch (err: any) {
      toast.error(err?.message || 'Block production failed')
    }
  }, [fetchAll])

  const handleDistribute = useCallback(async () => {
    try {
      await api('distribute')
      toast.success('Emissions distributed')
      fetchAll()
      fetchStakes()
    } catch (err: any) {
      toast.error(err?.message || 'Distribution failed')
    }
  }, [fetchAll, fetchStakes])

  const handleRegisterSubnet = useCallback(async () => {
    if (!subName.trim() || !subSubnet.trim() || !subStakeTime.trim() || !subConsensus.trim()) {
      toast.error('Fill in all subnet fields'); return
    }
    setRegisteringSub(true)
    try {
      await api('register_subnet', {
        name: subName.trim(),
        subnet: subSubnet.trim(),
        stake_time: subStakeTime.trim(),
        consensus: subConsensus.trim(),
      })
      toast.success('Subnet registered')
      setSubName(''); setSubSubnet(''); setSubStakeTime(''); setSubConsensus('')
      fetchAll()
    } catch (err: any) {
      toast.error(err?.message || 'Subnet registration failed')
    }
    setRegisteringSub(false)
  }, [subName, subSubnet, subStakeTime, subConsensus, fetchAll])

  const handleDeregisterSubnet = useCallback(async (subnetId: number) => {
    try {
      await api('deregister_subnet', { subnet_id: subnetId })
      toast.success('Subnet deregistered')
      fetchAll()
    } catch (err: any) {
      toast.error(err?.message || 'Deregister failed')
    }
  }, [fetchAll])

  const handleLlmGenerate = useCallback(async () => {
    if (!llmPrompt.trim()) { toast.error('Describe the subnet you want to create'); return }
    setLlmGenerating(true)
    try {
      const params = await api('generate_subnet_params', { prompt: llmPrompt.trim() })
      setLlmParams(params)
      setLlmEditing(false)
      toast.success('Parameters generated')
    } catch (err: any) {
      toast.error(err?.message || 'Generation failed')
    }
    setLlmGenerating(false)
  }, [llmPrompt])

  const handleLlmDeploy = useCallback(async () => {
    if (!llmParams) return
    setLlmDeploying(true)
    try {
      const result = await api('deploy_subnet', {
        name: llmParams.name,
        symbol: llmParams.symbol,
        initial_supply: llmParams.initialSupply || '1000000',
        max_lock_blocks: llmParams.maxLockBlocks || 100000,
        max_stakers_per_validator: llmParams.maxStakersPerValidator || 100,
        default_commission_bps: llmParams.defaultCommissionBps || 1000,
        epoch_length: llmParams.epochLength || 43200,
        emission_rate: llmParams.emissionRate || '100',
        decay_bps: llmParams.decayBps || 500,
      })
      toast.success(`Subnet "${llmParams.name}" deployed!`)
      setLlmParams(null)
      setLlmPrompt('')
      fetchAll()
    } catch (err: any) {
      toast.error(err?.message || 'Deploy failed')
    }
    setLlmDeploying(false)
  }, [llmParams, fetchAll])

  // ── Sorting ─────────────────────────────────────────────────────────

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

  const processed = useMemo(() => {
    let filtered = validators
    if (search) {
      const q = search.toLowerCase()
      filtered = filtered.filter(v =>
        v.key.toLowerCase().includes(q) || v.keyHash.toLowerCase().includes(q)
      )
    }
    return [...filtered].sort((a, b) => {
      let cmp = 0
      if (sortKey === 'score') cmp = a.blocktimeScore - b.blocktimeScore
      else if (sortKey === 'earned') cmp = Number(BigInt(a.earned || '0') - BigInt(b.earned || '0'))
      else if (sortKey === 'lastSeen') cmp = a.lastSeenBlock - b.lastSeenBlock
      else if (sortKey === 'totalSTT') cmp = Number(BigInt(a.totalSTT || '0') - BigInt(b.totalSTT || '0'))
      return sortDir === 'asc' ? cmp : -cmp
    })
  }, [validators, search, sortKey, sortDir])

  const epochProgress = consensus
    ? ((consensus.currentBlock - consensus.lastEmissionBlock) / consensus.epochLength) * 100
    : 0

  // Find validator key from keyHash for stakes display
  const keyHashToKey = useMemo(() => {
    const m: Record<string, string> = {}
    validators.forEach(v => { m[v.keyHash] = v.key })
    return m
  }, [validators])

  return (
    <div className="min-h-screen bg-[#0a0a0f] text-[#e5e5e5] font-mono">
      <div className="relative z-10 p-4 md:p-6 max-w-5xl mx-auto space-y-5">

        {/* Header */}
        <div className="flex items-center justify-between border border-white/10 rounded-lg p-4 bg-white/[0.03]">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 flex items-center justify-center rounded-lg bg-white/5 border border-white/10">
              <CubeIcon className="w-5 h-5 text-white/70" />
            </div>
            <div>
              <h1 className="text-2xl font-bold tracking-wider uppercase">StakeTime</h1>
              <p className="text-xs text-white/40 uppercase tracking-wider">Delegated Staking + Yuma Consensus</p>
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
              onClick={() => { fetchAll(); if (account) fetchStakes() }}
              disabled={loading}
              className="p-2 rounded-lg border border-white/10 bg-white/5 text-white/40 hover:text-white/70 hover:bg-white/10 disabled:opacity-30 transition-colors"
            >
              <ArrowPathIcon className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
            </button>
          </div>
        </div>

        {/* Consensus State */}
        {consensus && (
          <div className="border border-white/10 rounded-lg bg-white/[0.02] overflow-hidden">
            <div className="px-5 pt-5 pb-3">
              <div className="flex items-center justify-between mb-2">
                <span className="text-[10px] font-bold uppercase tracking-wider text-white/40">Epoch progress</span>
                <span className="text-xs font-bold text-cyan-400">{Math.min(epochProgress, 100).toFixed(1)}%</span>
              </div>
              <div className="h-2.5 rounded-full bg-white/[0.06] overflow-hidden">
                <motion.div
                  className="h-full rounded-full bg-gradient-to-r from-cyan-500 to-blue-400"
                  initial={{ width: 0 }}
                  animate={{ width: `${Math.min(Math.max(epochProgress, 0.5), 100)}%` }}
                  transition={{ duration: 1.2, ease: 'easeOut' }}
                />
              </div>
              <div className="flex items-center justify-between mt-1.5">
                <span className="text-[10px] text-white/25">
                  Block {consensus.currentBlock - consensus.lastEmissionBlock} / {consensus.epochLength}
                </span>
                <span className="text-[10px] text-white/25">Block #{consensus.currentBlock}</span>
              </div>
            </div>

            <div className="grid grid-cols-2 md:grid-cols-5 border-t border-white/[0.06]">
              <div className="p-4 text-center border-r border-b md:border-b-0 border-white/[0.06]">
                <p className="text-lg font-bold text-white/90 tabular-nums">{consensus.currentBlock}</p>
                <p className="text-[10px] font-bold uppercase tracking-wider text-white/30 mt-1">Block</p>
              </div>
              <div className="p-4 text-center border-b md:border-b-0 md:border-r border-white/[0.06]">
                <p className="text-lg font-bold text-cyan-400 tabular-nums">{validators.length}</p>
                <p className="text-[10px] font-bold uppercase tracking-wider text-white/30 mt-1">Validators</p>
              </div>
              <div className="p-4 text-center border-r border-white/[0.06]">
                <p className="text-lg font-bold text-amber-400 tabular-nums">{consensus.totalBlocktime}</p>
                <p className="text-[10px] font-bold uppercase tracking-wider text-white/30 mt-1">Total Score</p>
              </div>
              <div className="p-4 text-center border-r border-white/[0.06]">
                <p className="text-lg font-bold text-emerald-400 tabular-nums">
                  {fmtEth(consensus.emissionRate)}
                </p>
                <p className="text-[10px] font-bold uppercase tracking-wider text-white/30 mt-1">Rate/Epoch</p>
              </div>
              <div className="p-4 text-center">
                <p className="text-lg font-bold text-violet-400 tabular-nums">{(consensus.decayBps / 100).toFixed(1)}%</p>
                <p className="text-[10px] font-bold uppercase tracking-wider text-white/30 mt-1">Decay</p>
              </div>
            </div>
          </div>
        )}

        {/* Actions Bar */}
        <div className="flex items-center gap-2 flex-wrap">
          <button
            onClick={handleProduceBlock}
            className="flex items-center gap-1.5 px-4 py-2.5 rounded-lg border border-cyan-500/30 bg-cyan-500/10 text-cyan-300 text-[10px] font-bold uppercase tracking-wider hover:bg-cyan-500/20 transition-colors"
          >
            <PlayIcon className="w-3.5 h-3.5" /> Produce Block
          </button>
          <button
            onClick={handleDistribute}
            className="flex items-center gap-1.5 px-4 py-2.5 rounded-lg border border-emerald-500/30 bg-emerald-500/10 text-emerald-300 text-[10px] font-bold uppercase tracking-wider hover:bg-emerald-500/20 transition-colors"
          >
            <BoltIcon className="w-3.5 h-3.5" /> Distribute
          </button>
        </div>

        {/* Tab Bar */}
        <div className="flex items-center gap-1 border border-white/10 rounded-lg p-1 bg-white/[0.02]">
          {(['subnets', 'validators', 'staking', 'rewards'] as Tab[]).map(t => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`flex-1 py-2 rounded-md text-[10px] font-bold uppercase tracking-wider transition-colors ${
                tab === t
                  ? 'bg-white/10 text-white/90'
                  : 'text-white/40 hover:text-white/60 hover:bg-white/5'
              }`}
            >
              {t === 'subnets' ? 'Subnets' : t === 'validators' ? 'Validators' : t === 'staking' ? 'Stake' : 'Rewards'}
            </button>
          ))}
        </div>

        {/* ── Subnets Tab ────────────────────────────────────────────── */}
        {tab === 'subnets' && (
          <>
            {/* LLM Subnet Creator */}
            <div className="border border-white/10 rounded-lg p-4 bg-white/[0.02]">
              <div className="flex items-center gap-2 mb-3">
                <SparklesIcon className="w-4 h-4 text-violet-400/70" />
                <span className="text-[10px] font-bold uppercase tracking-wider text-white/40">Create Subnet</span>
                <span className="text-[10px] text-white/20 ml-auto">Cost: {fmtEth(registrationCost)} GOV | {subnets.length} / 420</span>
              </div>

              {!llmParams ? (
                <div className="space-y-2">
                  <textarea
                    placeholder="Describe the subnet you want to create...&#10;&#10;e.g. &quot;An AI inference subnet with high throughput, rewarding consistent uptime. Fast 12-hour epochs, generous emissions to attract early validators.&quot;"
                    value={llmPrompt}
                    onChange={e => setLlmPrompt(e.target.value)}
                    rows={3}
                    className="w-full text-sm px-4 py-3 rounded-lg border border-white/10 bg-white/5 text-white/90 focus:outline-none focus:border-white/30 font-mono transition-colors placeholder:text-white/20 resize-none"
                  />
                  <div className="flex items-center justify-end">
                    <button
                      onClick={handleLlmGenerate}
                      disabled={llmGenerating || !llmPrompt.trim()}
                      className="flex items-center gap-1.5 px-4 py-2.5 rounded-lg border border-violet-500/40 bg-violet-500/15 text-violet-300 text-[10px] font-bold uppercase tracking-wider hover:bg-violet-500/25 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                    >
                      {llmGenerating ? (
                        <><ArrowPathIcon className="w-3.5 h-3.5 animate-spin" /> Generating...</>
                      ) : (
                        <><SparklesIcon className="w-3.5 h-3.5" /> Generate</>
                      )}
                    </button>
                  </div>
                </div>
              ) : (
                <div className="space-y-3">
                  {llmParams.description && (
                    <p className="text-xs text-white/50 italic">{llmParams.description}</p>
                  )}

                  <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
                    {[
                      { label: 'Name', key: 'name', type: 'text' },
                      { label: 'Symbol', key: 'symbol', type: 'text' },
                      { label: 'Initial Supply', key: 'initialSupply', type: 'text' },
                      { label: 'Emission Rate/Epoch', key: 'emissionRate', type: 'text' },
                      { label: 'Epoch Length (blocks)', key: 'epochLength', type: 'number' },
                      { label: 'Decay (bps)', key: 'decayBps', type: 'number' },
                      { label: 'Max Lock Blocks', key: 'maxLockBlocks', type: 'number' },
                      { label: 'Max Stakers/Val', key: 'maxStakersPerValidator', type: 'number' },
                      { label: 'Commission (bps)', key: 'defaultCommissionBps', type: 'number' },
                    ].map(f => (
                      <div key={f.key}>
                        <label className="text-[9px] text-white/30 uppercase tracking-wider mb-0.5 block">{f.label}</label>
                        <input
                          type={f.type}
                          value={llmParams[f.key] ?? ''}
                          disabled={!llmEditing}
                          onChange={e => setLlmParams({ ...llmParams, [f.key]: f.type === 'number' ? parseInt(e.target.value) || 0 : e.target.value })}
                          className={`w-full text-xs px-3 py-2 rounded-lg border font-mono transition-colors ${
                            llmEditing
                              ? 'border-white/20 bg-white/5 text-white/90 focus:outline-none focus:border-white/40'
                              : 'border-white/[0.06] bg-white/[0.02] text-white/60'
                          }`}
                        />
                      </div>
                    ))}
                  </div>

                  <div className="flex items-center justify-between pt-1">
                    <div className="flex items-center gap-2">
                      <button
                        onClick={() => { setLlmParams(null); setLlmPrompt('') }}
                        className="px-3 py-2 rounded-lg border border-white/10 bg-white/5 text-white/40 text-[10px] font-bold uppercase tracking-wider hover:bg-white/10 hover:text-white/60 transition-colors"
                      >
                        Back
                      </button>
                      <button
                        onClick={() => setLlmEditing(!llmEditing)}
                        className="flex items-center gap-1 px-3 py-2 rounded-lg border border-white/10 bg-white/5 text-white/40 text-[10px] font-bold uppercase tracking-wider hover:bg-white/10 hover:text-white/60 transition-colors"
                      >
                        <PencilIcon className="w-3 h-3" /> {llmEditing ? 'Lock' : 'Edit'}
                      </button>
                    </div>
                    <button
                      onClick={handleLlmDeploy}
                      disabled={llmDeploying}
                      className="flex items-center gap-1.5 px-5 py-2.5 rounded-lg border border-emerald-500/40 bg-emerald-500/15 text-emerald-300 text-[10px] font-bold uppercase tracking-wider hover:bg-emerald-500/25 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                    >
                      {llmDeploying ? (
                        <><ArrowPathIcon className="w-3.5 h-3.5 animate-spin" /> Deploying...</>
                      ) : (
                        <><CheckIcon className="w-3.5 h-3.5" /> Deploy Subnet</>
                      )}
                    </button>
                  </div>
                </div>
              )}
            </div>

            {/* Weakest Subnet Banner */}
            {weakest && weakest.found && (
              <div className="border border-red-500/20 rounded-lg p-3 bg-red-500/5 flex items-center gap-3">
                <XCircleIcon className="w-4 h-4 text-red-400/70 shrink-0" />
                <span className="text-[10px] text-red-300/70 uppercase tracking-wider">
                  Weakest subnet: <span className="font-bold text-red-300">#{weakest.id}</span> (score: {fmtEth(weakest.score)} NTV) — will be replaced when at capacity
                </span>
              </div>
            )}

            {/* Subnets Table */}
            <div className="border border-white/10 rounded-lg bg-white/[0.02] overflow-hidden">
              <div className="grid grid-cols-[50px_1fr_1fr_1fr_100px_60px_50px] gap-2 px-4 py-2.5 border-b border-white/5 bg-white/[0.01]">
                <div className="text-[10px] font-bold uppercase tracking-wider text-white/40">ID</div>
                <div className="text-[10px] font-bold uppercase tracking-wider text-white/40">Name</div>
                <div className="text-[10px] font-bold uppercase tracking-wider text-white/40">StakeTime</div>
                <div className="text-[10px] font-bold uppercase tracking-wider text-white/40">Owner</div>
                <div className="text-[10px] font-bold uppercase tracking-wider text-white/40 text-right">Score</div>
                <div className="text-[10px] font-bold uppercase tracking-wider text-white/40 text-center">Status</div>
                <div></div>
              </div>

              {subnets.length === 0 ? (
                <div className="text-center py-12">
                  <span className="text-xs text-white/30 uppercase tracking-wider">No subnets registered</span>
                </div>
              ) : (
                <div>
                  {subnets.map(s => {
                    const isWeakest = weakest?.found && weakest.id === s.id
                    return (
                      <div
                        key={s.id}
                        className={`grid grid-cols-[50px_1fr_1fr_1fr_100px_60px_50px] gap-2 px-4 py-2.5 border-b border-white/[0.03] hover:bg-white/[0.03] transition-colors items-center ${
                          isWeakest ? 'bg-red-500/[0.03]' : ''
                        }`}
                      >
                        <span className={`text-xs font-bold tabular-nums ${isWeakest ? 'text-red-400/80' : 'text-white/60'}`}>
                          #{s.id}
                        </span>
                        <span className="text-xs font-mono text-white/80 truncate">{s.name}</span>
                        <span
                          className="text-xs font-mono text-white/40 truncate cursor-pointer hover:text-white/70 transition-colors"
                          title={s.stakeTime}
                          onClick={() => { navigator.clipboard.writeText(s.stakeTime); toast.success('Address copied') }}
                        >
                          {fmtAddr(s.stakeTime)}
                        </span>
                        <span
                          className="text-xs font-mono text-white/40 truncate cursor-pointer hover:text-white/70 transition-colors"
                          title={s.owner}
                          onClick={() => { navigator.clipboard.writeText(s.owner); toast.success('Address copied') }}
                        >
                          {fmtAddr(s.owner)}
                        </span>
                        <span className={`text-xs font-bold text-right tabular-nums ${isWeakest ? 'text-red-400/80' : 'text-blue-400/80'}`}>
                          {fmtEth(s.stakeScore)}
                        </span>
                        <div className="flex justify-center">
                          {s.immune ? (
                            <span className="flex items-center gap-1 text-[9px] font-bold uppercase px-1.5 py-0.5 rounded border text-emerald-400 border-emerald-500/30 bg-emerald-500/10" title="Immune from deregistration">
                              <ShieldCheckIcon className="w-3 h-3" />
                            </span>
                          ) : (
                            <span className="text-[9px] font-bold uppercase px-1.5 py-0.5 rounded border text-white/40 border-white/10 bg-white/5">
                              Live
                            </span>
                          )}
                        </div>
                        <div className="flex justify-center">
                          <button
                            onClick={() => handleDeregisterSubnet(s.id)}
                            className="p-1.5 rounded-lg border border-red-500/30 bg-red-500/10 text-red-300 hover:bg-red-500/20 transition-colors"
                            title="Deregister subnet"
                          >
                            <TrashIcon className="w-3 h-3" />
                          </button>
                        </div>
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          </>
        )}

        {/* ── Validators Tab ───────────────────────────────────────────── */}
        {tab === 'validators' && (
          <>
            {/* Register Panel */}
            <div className="border border-white/10 rounded-lg p-4 bg-white/[0.02]">
              <div className="flex items-center gap-2 mb-3">
                <UserPlusIcon className="w-4 h-4 text-white/40" />
                <span className="text-[10px] font-bold uppercase tracking-wider text-white/40">Register Validator</span>
              </div>
              <div className="flex items-center gap-3 flex-wrap">
                <input
                  type="text"
                  placeholder="Key (address, pubkey, or identifier)..."
                  value={regKey}
                  onChange={e => setRegKey(e.target.value)}
                  className="flex-1 min-w-[200px] text-sm px-4 py-2.5 rounded-lg border border-white/10 bg-white/5 text-white/90 focus:outline-none focus:border-white/30 font-mono transition-colors placeholder:text-white/20"
                />
                <select
                  value={regKeyType}
                  onChange={e => setRegKeyType(Number(e.target.value))}
                  className="text-xs px-3 py-2.5 rounded-lg border border-white/10 bg-white/5 text-white/70 focus:outline-none focus:border-white/30 transition-colors"
                >
                  <option value={0}>ECDSA</option>
                  <option value={1}>Ed25519</option>
                  <option value={2}>Sr25519</option>
                </select>
                <div className="flex items-center gap-1">
                  <input
                    type="number"
                    placeholder="10"
                    value={regCommission}
                    onChange={e => setRegCommission(e.target.value)}
                    className="w-16 text-xs px-2 py-2.5 rounded-lg border border-white/10 bg-white/5 text-white/70 focus:outline-none focus:border-white/30 transition-colors text-center"
                  />
                  <span className="text-[10px] text-white/30">%</span>
                </div>
                <button
                  onClick={handleRegister}
                  disabled={registering || !regKey.trim()}
                  className="px-4 py-2.5 rounded-lg border border-violet-500/40 bg-violet-500/15 text-violet-300 text-[10px] font-bold uppercase tracking-wider hover:bg-violet-500/25 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                >
                  {registering ? <ArrowPathIcon className="w-3.5 h-3.5 animate-spin" /> : 'Register'}
                </button>
              </div>
            </div>

            {/* Validators Table */}
            <div className="border border-white/10 rounded-lg bg-white/[0.02] overflow-hidden">
              <div className="flex items-center gap-3 p-4 border-b border-white/5">
                <input
                  type="text"
                  placeholder="Search key..."
                  value={search}
                  onChange={e => setSearch(e.target.value)}
                  className="flex-1 text-xs px-3 py-2 rounded-lg border border-white/10 bg-white/5 text-white/90 focus:outline-none focus:border-white/30 font-mono transition-colors placeholder:text-white/20"
                />
                <span className="text-[10px] text-white/30 uppercase tracking-wider">
                  {processed.length} validator{processed.length !== 1 ? 's' : ''}
                </span>
              </div>

              <div className="grid grid-cols-[1fr_60px_80px_80px_80px_80px_50px] gap-2 px-4 py-2.5 border-b border-white/5 bg-white/[0.01]">
                <div className="text-[10px] font-bold uppercase tracking-wider text-white/40">Key</div>
                <div className="text-[10px] font-bold uppercase tracking-wider text-white/40 text-center">Type</div>
                <button onClick={() => toggleSort('score')} className="flex items-center gap-1 text-[10px] font-bold uppercase tracking-wider text-white/40 hover:text-white/60 transition-colors justify-end">
                  Score <SortIcon col="score" />
                </button>
                <button onClick={() => toggleSort('totalSTT')} className="flex items-center gap-1 text-[10px] font-bold uppercase tracking-wider text-white/40 hover:text-white/60 transition-colors justify-end">
                  STT <SortIcon col="totalSTT" />
                </button>
                <button onClick={() => toggleSort('earned')} className="flex items-center gap-1 text-[10px] font-bold uppercase tracking-wider text-white/40 hover:text-white/60 transition-colors justify-end">
                  Earned <SortIcon col="earned" />
                </button>
                <button onClick={() => toggleSort('lastSeen')} className="flex items-center gap-1 text-[10px] font-bold uppercase tracking-wider text-white/40 hover:text-white/60 transition-colors justify-end">
                  Seen <SortIcon col="lastSeen" />
                </button>
                <div className="text-[10px] font-bold uppercase tracking-wider text-white/40 text-center"></div>
              </div>

              {loading && validators.length === 0 ? (
                <div className="text-center py-12">
                  <ArrowPathIcon className="w-5 h-5 animate-spin text-white/20 mx-auto mb-2" />
                  <span className="text-xs text-white/30">Loading...</span>
                </div>
              ) : processed.length === 0 ? (
                <div className="text-center py-12">
                  <span className="text-xs text-white/30 uppercase tracking-wider">No validators registered</span>
                </div>
              ) : (
                <div>
                  {processed.map((v) => {
                    const keyTypeColor = v.keyType === 0
                      ? 'text-amber-400 border-amber-500/30 bg-amber-500/10'
                      : v.keyType === 1
                      ? 'text-cyan-400 border-cyan-500/30 bg-cyan-500/10'
                      : 'text-violet-400 border-violet-500/30 bg-violet-500/10'

                    return (
                      <div
                        key={v.keyHash}
                        className="grid grid-cols-[1fr_60px_80px_80px_80px_80px_50px] gap-2 px-4 py-2.5 border-b border-white/[0.03] hover:bg-white/[0.03] transition-colors items-center"
                      >
                        <span
                          className="text-xs font-mono text-white/60 truncate cursor-pointer hover:text-white/90 transition-colors"
                          title={v.key}
                          onClick={() => { navigator.clipboard.writeText(v.key); toast.success('Key copied') }}
                        >
                          {v.key}
                        </span>
                        <div className="flex justify-center">
                          <span className={`text-[9px] font-bold uppercase px-1.5 py-0.5 rounded border ${keyTypeColor}`}>
                            {KEY_TYPE_LABELS[v.keyType] || '?'}
                          </span>
                        </div>
                        <span className="text-xs font-bold text-amber-400/80 text-right tabular-nums">
                          {v.blocktimeScore}
                        </span>
                        <span className="text-xs font-bold text-blue-400/80 text-right tabular-nums">
                          {fmtEth(v.totalSTT)}
                        </span>
                        <span className="text-xs font-bold text-emerald-400/80 text-right tabular-nums">
                          {fmtEth(v.earned)}
                        </span>
                        <span className="text-xs text-white/40 text-right tabular-nums">
                          {v.lastSeenBlock > 0 ? `#${v.lastSeenBlock}` : '--'}
                        </span>
                        <div className="flex justify-center gap-1">
                          <button
                            onClick={() => handleCheckin(v.key)}
                            className="p-1.5 rounded-lg border border-cyan-500/30 bg-cyan-500/10 text-cyan-300 hover:bg-cyan-500/20 transition-colors"
                            title="Checkin"
                          >
                            <SignalIcon className="w-3 h-3" />
                          </button>
                        </div>
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          </>
        )}

        {/* ── Staking Tab ──────────────────────────────────────────────── */}
        {tab === 'staking' && (
          <>
            {/* Stake Form */}
            <div className="border border-white/10 rounded-lg p-4 bg-white/[0.02]">
              <div className="flex items-center gap-2 mb-3">
                <BanknotesIcon className="w-4 h-4 text-white/40" />
                <span className="text-[10px] font-bold uppercase tracking-wider text-white/40">Stake on Validator</span>
              </div>
              <div className="space-y-3">
                <div className="flex items-center gap-3 flex-wrap">
                  <select
                    value={stakeValidator}
                    onChange={e => setStakeValidator(e.target.value)}
                    className="flex-1 min-w-[200px] text-sm px-4 py-2.5 rounded-lg border border-white/10 bg-white/5 text-white/90 focus:outline-none focus:border-white/30 font-mono transition-colors"
                  >
                    <option value="">Select validator...</option>
                    {validators.filter(v => v.active).map(v => (
                      <option key={v.keyHash} value={v.key}>{v.key} ({(v.commissionBps / 100).toFixed(0)}% comm.)</option>
                    ))}
                  </select>
                </div>
                <div className="flex items-center gap-3 flex-wrap">
                  <div className="flex-1 min-w-[150px]">
                    <label className="text-[10px] text-white/30 uppercase tracking-wider mb-1 block">Amount (tokens)</label>
                    <input
                      type="text"
                      placeholder="100"
                      value={stakeAmount}
                      onChange={e => setStakeAmount(e.target.value)}
                      className="w-full text-sm px-4 py-2.5 rounded-lg border border-white/10 bg-white/5 text-white/90 focus:outline-none focus:border-white/30 font-mono transition-colors placeholder:text-white/20"
                    />
                  </div>
                  <div className="w-32">
                    <label className="text-[10px] text-white/30 uppercase tracking-wider mb-1 block">Lock blocks</label>
                    <input
                      type="number"
                      placeholder="0"
                      value={stakeLockBlocks}
                      onChange={e => setStakeLockBlocks(e.target.value)}
                      className="w-full text-sm px-4 py-2.5 rounded-lg border border-white/10 bg-white/5 text-white/90 focus:outline-none focus:border-white/30 font-mono transition-colors placeholder:text-white/20"
                    />
                  </div>
                  <div className="pt-4">
                    <button
                      onClick={handleStake}
                      disabled={staking || !stakeValidator || !stakeAmount}
                      className="px-6 py-2.5 rounded-lg border border-emerald-500/40 bg-emerald-500/15 text-emerald-300 text-[10px] font-bold uppercase tracking-wider hover:bg-emerald-500/25 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                    >
                      {staking ? <ArrowPathIcon className="w-3.5 h-3.5 animate-spin" /> : 'Stake'}
                    </button>
                  </div>
                </div>
                {parseInt(stakeLockBlocks) > 0 && (
                  <p className="text-[10px] text-white/30">
                    Lock: ~{(parseInt(stakeLockBlocks) / 43200).toFixed(1)} days ({stakeLockBlocks} blocks) — longer locks earn more STT via multiplier curve
                  </p>
                )}
              </div>
            </div>

            {/* My Stakes */}
            <div className="border border-white/10 rounded-lg bg-white/[0.02] overflow-hidden">
              <div className="flex items-center justify-between p-4 border-b border-white/5">
                <div className="flex items-center gap-2">
                  <LockClosedIcon className="w-4 h-4 text-white/40" />
                  <span className="text-[10px] font-bold uppercase tracking-wider text-white/40">My Stakes</span>
                </div>
                <span className="text-[10px] text-white/30">{stakes.length} position{stakes.length !== 1 ? 's' : ''}</span>
              </div>

              {!connected ? (
                <div className="text-center py-12">
                  <span className="text-xs text-white/30 uppercase tracking-wider">Connect wallet to view stakes</span>
                </div>
              ) : stakes.length === 0 ? (
                <div className="text-center py-12">
                  <span className="text-xs text-white/30 uppercase tracking-wider">No active stakes</span>
                </div>
              ) : (
                <div>
                  <div className="grid grid-cols-[60px_1fr_100px_100px_100px_60px] gap-2 px-4 py-2 border-b border-white/5 bg-white/[0.01]">
                    <div className="text-[10px] font-bold uppercase tracking-wider text-white/40">ID</div>
                    <div className="text-[10px] font-bold uppercase tracking-wider text-white/40">Validator</div>
                    <div className="text-[10px] font-bold uppercase tracking-wider text-white/40 text-right">Staked</div>
                    <div className="text-[10px] font-bold uppercase tracking-wider text-white/40 text-right">STT Earned</div>
                    <div className="text-[10px] font-bold uppercase tracking-wider text-white/40 text-right">Lock</div>
                    <div></div>
                  </div>
                  {stakes.map(s => {
                    const unlocked = s.blocksRemaining === 0
                    const valKey = keyHashToKey[s.validatorKeyHash] || fmtAddr('0x' + s.validatorKeyHash)
                    return (
                      <div key={s.stakeId} className="grid grid-cols-[60px_1fr_100px_100px_100px_60px] gap-2 px-4 py-2.5 border-b border-white/[0.03] hover:bg-white/[0.03] transition-colors items-center">
                        <span className="text-xs text-white/50 tabular-nums">#{s.stakeId}</span>
                        <span className="text-xs font-mono text-white/60 truncate" title={valKey}>{valKey}</span>
                        <span className="text-xs font-bold text-white/80 text-right tabular-nums">{fmtEth(s.amount)}</span>
                        <span className="text-xs font-bold text-blue-400/80 text-right tabular-nums">{fmtEth(s.stakeTimeBalance)}</span>
                        <span className={`text-xs text-right tabular-nums ${unlocked ? 'text-emerald-400/80' : 'text-amber-400/80'}`}>
                          {unlocked ? (
                            <span className="flex items-center gap-1 justify-end"><LockOpenIcon className="w-3 h-3" /> Unlocked</span>
                          ) : (
                            <span>{s.blocksRemaining} blocks</span>
                          )}
                        </span>
                        <div className="flex justify-center">
                          <button
                            onClick={() => handleUnstake(s.stakeId)}
                            disabled={!unlocked}
                            className={`p-1.5 rounded-lg border transition-colors ${
                              unlocked
                                ? 'border-red-500/30 bg-red-500/10 text-red-300 hover:bg-red-500/20'
                                : 'border-white/10 bg-white/5 text-white/20 cursor-not-allowed'
                            }`}
                            title={unlocked ? 'Unstake' : `Locked for ${s.blocksRemaining} blocks`}
                          >
                            <TrashIcon className="w-3 h-3" />
                          </button>
                        </div>
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          </>
        )}

        {/* ── Rewards Tab ──────────────────────────────────────────────── */}
        {tab === 'rewards' && (
          <div className="space-y-4">
            {/* Staker Rewards */}
            <div className="border border-white/10 rounded-lg p-5 bg-white/[0.02]">
              <div className="flex items-center gap-2 mb-4">
                <GiftIcon className="w-4 h-4 text-white/40" />
                <span className="text-[10px] font-bold uppercase tracking-wider text-white/40">Staker Rewards</span>
              </div>
              {!connected ? (
                <p className="text-xs text-white/30 uppercase tracking-wider text-center py-6">Connect wallet to view rewards</p>
              ) : (
                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-2xl font-bold text-emerald-400 tabular-nums">{fmtEth(stakerRewards)}</p>
                    <p className="text-[10px] text-white/30 uppercase mt-1">NTV pending</p>
                  </div>
                  <button
                    onClick={handleClaimStaker}
                    disabled={stakerRewards === '0'}
                    className="px-6 py-3 rounded-lg border border-emerald-500/40 bg-emerald-500/15 text-emerald-300 text-xs font-bold uppercase tracking-wider hover:bg-emerald-500/25 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                  >
                    Claim
                  </button>
                </div>
              )}
            </div>

            {/* Validator Earnings Summary */}
            <div className="border border-white/10 rounded-lg bg-white/[0.02] overflow-hidden">
              <div className="flex items-center gap-2 p-4 border-b border-white/5">
                <BanknotesIcon className="w-4 h-4 text-white/40" />
                <span className="text-[10px] font-bold uppercase tracking-wider text-white/40">Validator Earnings</span>
              </div>
              {validators.filter(v => v.balance !== '0').length === 0 ? (
                <div className="text-center py-8">
                  <span className="text-xs text-white/30 uppercase tracking-wider">No validator earnings yet</span>
                </div>
              ) : (
                <div>
                  {validators.filter(v => v.balance !== '0').map(v => (
                    <div key={v.keyHash} className="flex items-center justify-between px-4 py-3 border-b border-white/[0.03] hover:bg-white/[0.03] transition-colors">
                      <div>
                        <span className="text-xs font-mono text-white/60">{v.key}</span>
                        <span className="text-[10px] text-white/30 ml-2">({(v.commissionBps / 100).toFixed(0)}% comm.)</span>
                      </div>
                      <span className="text-xs font-bold text-amber-400 tabular-nums">{fmtEth(v.balance)} NTV</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}

        {/* Footer */}
        <div className="text-center text-xs text-white/15 uppercase tracking-wider py-4">
          StakeTime
        </div>
      </div>
    </div>
  )
}

export default dynamic(() => Promise.resolve(StakeTimeApp), { ssr: false })
