"use client";

import { useState, useEffect, useCallback } from 'react'
import { toast } from 'react-toastify'
import {
  ArrowsRightLeftIcon,
  ArrowPathIcon,
  CheckCircleIcon,
  XCircleIcon,
  LinkIcon,
  PencilIcon,
  ChevronUpIcon,
  ChevronDownIcon,
  ChevronUpDownIcon,
} from '@heroicons/react/24/outline'
import { motion, AnimatePresence } from 'framer-motion'

const API_URL = process.env.NEXT_PUBLIC_API_URL || (process.env.NODE_ENV === 'production' ? 'https://modc2.com/api/bridge' : 'http://localhost:8840')
const APP_URL = process.env.NEXT_PUBLIC_APP_URL || (process.env.NODE_ENV === 'production' ? 'https://modc2.com/bridge' : 'http://localhost:8841')

interface CommitResult {
  success?: boolean
  source_address?: string
  evm_address?: string
  source_type?: string
  chain?: { tx_hash?: string; status?: string; error?: string }
  error?: string
  previous_evm?: string
}

interface CommitmentEntry {
  source_address: string
  evm_address: string
  source_type: string
  timestamp: number
  chain?: { tx_hash?: string; status?: string; error?: string }
  previous_evm?: string
}

interface SnapshotEntry {
  address: string
  balance: number
  claimed: boolean
  evm_address?: string
  source_type?: string
}

interface SnapshotStats {
  total_addresses: number
  total_owed: number
  total_claimed: number
  total_committed: number
  total_unclaimed: number
  claim_count: number
  commitment_count: number
}

interface SnapshotAudit {
  cid: string
  updated_at: number
  bytes: number
  addresses: number
  algo: string
}

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

// Paged GET — used for the snapshot loader. Fetches /balances?page=&limit= and
// returns { balances, total }. Trailing slashes stripped to be reverse-proxy
// safe (Caddy strips its own prefix; we don't want a double slash).
async function apiGet(path: string): Promise<any> {
  const res = await fetch(`${API_URL.replace(/\/$/, '')}${path}`, { method: 'GET' })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || err.error || 'Request failed')
  }
  return res.json()
}

const PAGE_SIZE = 50
const SEARCH_DEBOUNCE_MS = 250

function isValidEvmAddress(addr: string): boolean {
  return /^0x[a-fA-F0-9]{40}$/.test(addr.trim())
}

type SourceType = 'substrate' | 'solana'
type SortKey = 'address' | 'balance' | 'claimed'
type SortDir = 'asc' | 'desc'
type ClaimFilter = 'all' | 'claimed' | 'unclaimed'

function BridgePageInner() {
  // ── Data state ──────────────────────────────────────────────────
  const [snapshotStats, setSnapshotStats] = useState<SnapshotStats | null>(null)
  const [snapshotEntries, setSnapshotEntries] = useState<SnapshotEntry[]>([])
  const [loading, setLoading] = useState(false)
  const [commitments, setCommitments] = useState<Record<string, CommitmentEntry>>({})
  const [audit, setAudit] = useState<SnapshotAudit | null>(null)

  // ── Table state ─────────────────────────────────────────────────
  const [search, setSearch] = useState('')
  const [debouncedSearch, setDebouncedSearch] = useState('')
  const [sortKey, setSortKey] = useState<SortKey>('balance')
  const [sortDir, setSortDir] = useState<SortDir>('desc')
  const [claimFilter, setClaimFilter] = useState<ClaimFilter>('all')
  const [page, setPage] = useState(0)
  const [filteredTotal, setFilteredTotal] = useState(0)

  // ── Commit state ────────────────────────────────────────────────
  const [evmAddress, setEvmAddress] = useState('')
  const evmValid = evmAddress.trim() !== '' && isValidEvmAddress(evmAddress.trim())
  const evmTouched = evmAddress.trim() !== ''

  const [subWalletAddress, setSubWalletAddress] = useState<string | null>(null)
  const [phantomAddress, setPhantomAddress] = useState<string | null>(null)

  const [commitProcessing, setCommitProcessing] = useState(false)
  const [commitResult, setCommitResult] = useState<CommitResult | null>(null)
  const [showCommit, setShowCommit] = useState(false)

  const [editingAddress, setEditingAddress] = useState<string | null>(null)
  const [editEvmAddress, setEditEvmAddress] = useState('')

  // ── Network state ─────────────────────────────────────────────
  const [network, setNetwork] = useState<'testnet' | 'mainnet'>('testnet')

  // ── Helpers ─────────────────────────────────────────────────────
  const formatAddress = (addr: string) =>
    addr ? `${addr.slice(0, 6)}...${addr.slice(-4)}` : ''

  const formatBalance = (bal: number) =>
    bal % 1 === 0 ? bal.toLocaleString() : bal.toLocaleString(undefined, { maximumFractionDigits: 4 })

  const copyToClipboard = (text: string, label: string = 'Address') => {
    navigator.clipboard.writeText(text)
      .then(() => toast.success(`${label} copied`))
      .catch(() => toast.error('Failed to copy'))
  }

  // ── Data fetching ───────────────────────────────────────────────
  // Server-driven: every search/sort/filter/page change re-queries the
  // bridge API for just the visible page. Nothing is loaded client-side
  // up front — the snapshot has ~100k entries and they don't belong in
  // the browser. Stats + commitments are loaded once and refreshed only
  // when the user takes an action (commit / update).

  // One-time loaders: stats, commitments, audit.
  const refreshStats = useCallback(async () => {
    const [statsData, commitmentsData] = await Promise.all([
      api('status').catch(() => null),
      api('get_commitments').catch(() => ({})),
    ])
    if (statsData) setSnapshotStats(statsData)
    setCommitments(commitmentsData || {})
  }, [])

  // Debounce search so we don't hammer the API per keystroke.
  useEffect(() => {
    const id = setTimeout(() => setDebouncedSearch(search), SEARCH_DEBOUNCE_MS)
    return () => clearTimeout(id)
  }, [search])

  // Reset to page 0 whenever the query / filter / sort changes.
  useEffect(() => { setPage(0) }, [debouncedSearch, claimFilter, sortKey, sortDir])

  const fetchPage = useCallback(async () => {
    setLoading(true)
    try {
      const params = new URLSearchParams({
        page: String(page),
        limit: String(PAGE_SIZE),
        sort_by: sortKey,
        dir: sortDir,
        claim_filter: claimFilter,
      })
      if (debouncedSearch.trim()) params.set('q', debouncedSearch.trim())

      const res = await apiGet(`/balances?${params.toString()}`)
      const entries: Array<{ address: string; balance: number; claimed: boolean }> =
        res?.entries || []
      const commsMap = commitments
      setSnapshotEntries(entries.map(e => {
        const comm = commsMap[e.address]
        return {
          address: e.address,
          balance: Number(e.balance),
          claimed: e.claimed,
          evm_address: comm?.evm_address,
          source_type: comm?.source_type,
        } as SnapshotEntry
      }))
      setFilteredTotal(Number(res?.total || 0))
    } catch { /* ignore — keep prior page on error */ }
    setLoading(false)
  }, [page, sortKey, sortDir, claimFilter, debouncedSearch, commitments])

  // Initial load.
  useEffect(() => {
    refreshStats()
    apiGet('/audit')
      .then(a => a?.snapshot && setAudit(a.snapshot))
      .catch(() => {})
  }, [refreshStats])

  useEffect(() => { fetchPage() }, [fetchPage])

  // Pull fresh totals + the current page (used after a commit / update / manual refresh).
  const refreshAfterAction = useCallback(() => {
    refreshStats()
    fetchPage()
  }, [refreshStats, fetchPage])

  // ── Sorting ─────────────────────────────────────────────────────
  const toggleSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    } else {
      setSortKey(key)
      setSortDir(key === 'address' ? 'asc' : 'desc')
    }
    setPage(0)
  }

  const SortIcon = ({ col }: { col: SortKey }) => {
    if (sortKey !== col) return <ChevronUpDownIcon className="w-3 h-3 text-white/20" />
    return sortDir === 'asc'
      ? <ChevronUpIcon className="w-3 h-3 text-white/60" />
      : <ChevronDownIcon className="w-3 h-3 text-white/60" />
  }

  // Server returns the visible page already filtered + sorted.
  const totalPages = Math.max(1, Math.ceil(filteredTotal / PAGE_SIZE))
  const pageEntries = snapshotEntries

  // ── Wallet connectors ──────────────────────────────────────────
  const connectSubWallet = useCallback(async () => {
    if (typeof window === 'undefined') return
    const injected = (window as any).injectedWeb3
    const ext = injected?.['subwallet-js'] || injected?.['polkadot-js']
    if (!ext) { toast.error('Install SubWallet or Polkadot.js extension'); return }
    try {
      const extension = await ext.enable('Bridge')
      const accounts = await extension.accounts.get()
      if (accounts?.length > 0) {
        setSubWalletAddress(accounts[0].address)
        toast.success(`Connected: ${accounts[0].address.slice(0, 12)}...`)
      } else { toast.error('No accounts found in SubWallet') }
    } catch (err: any) { toast.error(err?.message || 'Failed to connect SubWallet') }
  }, [])

  const connectPhantom = useCallback(async () => {
    if (typeof window === 'undefined') return
    const phantom = (window as any).phantom?.solana || (window as any).solana
    if (!phantom?.isPhantom) { toast.error('Install Phantom wallet'); return }
    try {
      const resp = await phantom.connect()
      const addr = resp.publicKey.toString()
      setPhantomAddress(addr)
      toast.success(`Connected: ${addr.slice(0, 12)}...`)
    } catch (err: any) { toast.error(err?.message || 'Failed to connect Phantom') }
  }, [])

  // ── Sign helper ────────────────────────────────────────────────
  const signMessage = async (sourceAddress: string, sourceType: SourceType, message: string): Promise<string> => {
    if (sourceType === 'substrate') {
      const injected = (window as any).injectedWeb3
      const ext = injected?.['subwallet-js'] || injected?.['polkadot-js']
      const extension = await ext.enable('Bridge')
      const { signature } = await extension.signer.signRaw({
        address: sourceAddress, data: message, type: 'bytes',
      })
      return signature.startsWith('0x') ? signature.slice(2) : signature
    } else {
      const phantom = (window as any).phantom?.solana || (window as any).solana
      const encoded = new TextEncoder().encode(message)
      const { signature } = await phantom.signMessage(encoded, 'utf8')
      return Array.from(signature as Uint8Array)
        .map((b: number) => b.toString(16).padStart(2, '0'))
        .join('')
    }
  }

  // ── Commit ─────────────────────────────────────────────────────
  const handleCommit = useCallback(async (sourceAddress: string, sourceType: SourceType) => {
    const target = evmAddress.trim()
    if (!target || !isValidEvmAddress(target)) { toast.error('Enter a valid EVM address first'); return }
    setCommitProcessing(true)
    setCommitResult(null)
    try {
      const sigHex = await signMessage(sourceAddress, sourceType, `commit ${target}`)
      const result = await api('commit', {
        source_address: sourceAddress, evm_address: target, signature: sigHex, source_type: sourceType,
      })
      setCommitResult(result)
      if (result?.success) { toast.success('Commitment submitted!'); refreshAfterAction() }
      else if (result?.error) toast.error(result.error)
    } catch (err: any) {
      const errResult = { error: err?.message || 'Commitment failed' }
      setCommitResult(errResult)
      toast.error(errResult.error)
    } finally { setCommitProcessing(false) }
  }, [evmAddress, refreshAfterAction])

  const handleUpdate = useCallback(async (sourceAddress: string, sourceType: SourceType, newEvmAddress: string) => {
    if (!newEvmAddress || !isValidEvmAddress(newEvmAddress)) { toast.error('Enter a valid new EVM address'); return }
    setCommitProcessing(true)
    setCommitResult(null)
    try {
      const sigHex = await signMessage(sourceAddress, sourceType, `commit ${newEvmAddress}`)
      const result = await api('update_commitment', {
        source_address: sourceAddress, evm_address: newEvmAddress, signature: sigHex, source_type: sourceType,
      })
      setCommitResult(result)
      if (result?.success) {
        toast.success(`Updated: ${formatAddress(result.previous_evm || '')} -> ${formatAddress(newEvmAddress)}`)
        setEditingAddress(null)
        setEditEvmAddress('')
        refreshAfterAction()
      } else if (result?.error) toast.error(result.error)
    } catch (err: any) {
      const errResult = { error: err?.message || 'Update failed' }
      setCommitResult(errResult)
      toast.error(errResult.error)
    } finally { setCommitProcessing(false) }
  }, [refreshAfterAction])

  const canEdit = (entry: SnapshotEntry): boolean => {
    if (!entry.evm_address) return false
    if (entry.source_type === 'substrate') return subWalletAddress === entry.address
    if (entry.source_type === 'solana') return phantomAddress === entry.address
    return false
  }

  const hasSource = subWalletAddress || phantomAddress
  const claimFilters: { key: ClaimFilter; label: string }[] = [
    { key: 'all', label: 'All' },
    { key: 'claimed', label: 'Claimed' },
    { key: 'unclaimed', label: 'Unclaimed' },
  ]

  return (
    <div className="min-h-screen bg-[#0a0a0f] text-[#e5e5e5] font-mono">
      <div className="relative z-10 p-4 md:p-6 max-w-4xl mx-auto space-y-5">

        {/* Header */}
        <div className="flex items-center justify-between border border-white/10 rounded-lg p-4 bg-white/[0.03]">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 flex items-center justify-center rounded-lg bg-white/5 border border-white/10">
              <ArrowsRightLeftIcon className="w-5 h-5 text-white/70" />
            </div>
            <div>
              <h1 className="text-2xl font-bold tracking-wider uppercase">BRIDGE</h1>
              <p className="text-xs text-white/40 uppercase tracking-wider">Link your identity to EVM</p>
            </div>
          </div>
          <button
            onClick={() => setShowCommit(v => !v)}
            className={`px-4 py-2 rounded-lg border text-xs font-bold uppercase tracking-wider transition-colors ${
              showCommit
                ? 'border-white/30 bg-white/10 text-white/80'
                : 'border-white/10 bg-white/5 text-white/50 hover:bg-white/10 hover:text-white/70'
            }`}
          >
            <LinkIcon className="w-4 h-4 inline mr-1.5 -mt-0.5" />
            Commit
          </button>
        </div>

        {/* Network Selector */}
        <div className="flex items-center gap-2">
          <button
            className="px-4 py-2 rounded-lg border text-xs font-bold uppercase tracking-wider transition-colors border-emerald-500/40 bg-emerald-500/15 text-emerald-400"
          >
            Testnet
          </button>
          <button
            disabled
            className="relative px-4 py-2 rounded-lg border text-xs font-bold uppercase tracking-wider border-white/5 bg-white/[0.02] text-white/20 cursor-not-allowed"
          >
            Mainnet
            <span className="absolute -top-2 -right-2 px-1.5 py-0.5 rounded text-[8px] font-bold uppercase tracking-wider bg-amber-500/20 text-amber-400/80 border border-amber-500/30">
              Soon
            </span>
          </button>
        </div>

        {/* Result Banner */}
        <AnimatePresence>
          {commitResult && (
            <motion.div
              initial={{ opacity: 0, y: -10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -10 }}
              className={`border rounded-lg p-4 ${
                commitResult.error ? 'border-red-500/30 bg-red-500/5' : 'border-emerald-500/30 bg-emerald-500/5'
              }`}
            >
              <div className="flex items-center justify-between gap-3">
                <div className="flex items-center gap-3 min-w-0">
                  {commitResult.error
                    ? <XCircleIcon className="w-5 h-5 text-red-400 flex-shrink-0" />
                    : <CheckCircleIcon className="w-5 h-5 text-emerald-400 flex-shrink-0" />
                  }
                  <div className="text-sm min-w-0">
                    {commitResult.error ? (
                      <span className="text-red-400">{commitResult.error}</span>
                    ) : (
                      <div className="flex items-center gap-4 flex-wrap">
                        <span className="text-emerald-400 font-bold uppercase text-xs">
                          {commitResult.previous_evm ? 'UPDATED' : 'COMMITTED'}
                        </span>
                        {commitResult.chain?.tx_hash && (
                          <button
                            onClick={() => copyToClipboard(commitResult.chain!.tx_hash!, 'Tx Hash')}
                            className="text-emerald-400 hover:opacity-70 text-xs transition-opacity"
                          >
                            TX: {commitResult.chain.tx_hash.substring(0, 16)}...
                          </button>
                        )}
                      </div>
                    )}
                  </div>
                </div>
                <button onClick={() => setCommitResult(null)} className="text-white/40 hover:text-white/70 text-lg leading-none px-1 flex-shrink-0">x</button>
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        {/* ── Commit Panel (collapsible) ────────────────────────────── */}
        <AnimatePresence>
          {showCommit && (
            <motion.div
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: 'auto' }}
              exit={{ opacity: 0, height: 0 }}
              className="overflow-hidden"
            >
              <div className="border border-white/10 rounded-lg p-4 bg-white/[0.02]">
                <div className="flex items-center gap-3">
                  {/* EVM input */}
                  <input
                    type="text"
                    placeholder="0x..."
                    value={evmAddress}
                    onChange={e => setEvmAddress(e.target.value)}
                    className={`flex-1 text-sm px-4 py-2.5 rounded-lg border bg-white/5 text-white/90 focus:outline-none font-mono transition-colors placeholder:text-white/20 ${
                      evmTouched && !evmValid
                        ? 'border-red-500/40 focus:border-red-500/60'
                        : evmValid
                        ? 'border-emerald-500/30 focus:border-emerald-500/50'
                        : 'border-white/10 focus:border-white/30'
                    }`}
                  />

                  {/* Substrate */}
                  {subWalletAddress ? (
                    <div className="flex items-center gap-2">
                      <button
                        onClick={() => copyToClipboard(subWalletAddress, 'Address')}
                        className="text-xs font-mono text-violet-400/70 hover:text-violet-300 transition-colors"
                        title={subWalletAddress}
                      >
                        {formatAddress(subWalletAddress)}
                      </button>
                      <button
                        onClick={() => handleCommit(subWalletAddress, 'substrate')}
                        disabled={commitProcessing || !evmValid}
                        className="px-3 py-2.5 rounded-lg border border-violet-500/40 bg-violet-500/15 text-violet-300 text-[10px] font-bold uppercase tracking-wider hover:bg-violet-500/25 disabled:opacity-30 disabled:cursor-not-allowed transition-colors whitespace-nowrap"
                      >
                        {commitProcessing ? <ArrowPathIcon className="w-3.5 h-3.5 animate-spin" /> : 'Commit'}
                      </button>
                      <button onClick={() => setSubWalletAddress(null)} className="text-[10px] text-white/20 hover:text-white/50 transition-colors">x</button>
                    </div>
                  ) : (
                    <button onClick={connectSubWallet} className="px-3 py-2.5 rounded-lg border border-white/10 bg-white/5 text-[10px] font-bold uppercase tracking-wider text-white/40 hover:bg-white/10 hover:text-white/60 transition-colors whitespace-nowrap">
                      SubWallet
                    </button>
                  )}

                  {/* Phantom */}
                  {phantomAddress ? (
                    <div className="flex items-center gap-2">
                      <button
                        onClick={() => copyToClipboard(phantomAddress, 'Address')}
                        className="text-xs font-mono text-cyan-400/70 hover:text-cyan-300 transition-colors"
                        title={phantomAddress}
                      >
                        {formatAddress(phantomAddress)}
                      </button>
                      <button
                        onClick={() => handleCommit(phantomAddress, 'solana')}
                        disabled={commitProcessing || !evmValid}
                        className="px-3 py-2.5 rounded-lg border border-cyan-500/40 bg-cyan-500/15 text-cyan-300 text-[10px] font-bold uppercase tracking-wider hover:bg-cyan-500/25 disabled:opacity-30 disabled:cursor-not-allowed transition-colors whitespace-nowrap"
                      >
                        {commitProcessing ? <ArrowPathIcon className="w-3.5 h-3.5 animate-spin" /> : 'Commit'}
                      </button>
                      <button onClick={() => setPhantomAddress(null)} className="text-[10px] text-white/20 hover:text-white/50 transition-colors">x</button>
                    </div>
                  ) : (
                    <button onClick={connectPhantom} className="px-3 py-2.5 rounded-lg border border-white/10 bg-white/5 text-[10px] font-bold uppercase tracking-wider text-white/40 hover:bg-white/10 hover:text-white/60 transition-colors whitespace-nowrap">
                      Phantom
                    </button>
                  )}
                </div>
                {evmTouched && !evmValid && (
                  <p className="text-[10px] text-red-400/70 mt-2">Must be 0x followed by 40 hex characters</p>
                )}
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        {/* ── Stats ─────────────────────────────────────────────────── */}
        {snapshotStats && (() => {
          // "Claimed" in the UI means: the source address has committed to an
          // EVM address (the on-chain claim follows from the commitment).
          const claimedAmount = snapshotStats.total_committed + snapshotStats.total_claimed
          const claimedCount = snapshotStats.commitment_count + snapshotStats.claim_count
          const claimPct = snapshotStats.total_owed > 0
            ? (claimedAmount / snapshotStats.total_owed) * 100
            : 0
          const walletPct = snapshotStats.total_addresses > 0
            ? (claimedCount / snapshotStats.total_addresses) * 100
            : 0

          return (
            <div className="border border-white/10 rounded-lg bg-white/[0.02] overflow-hidden">
              {/* Progress bar */}
              <div className="px-5 pt-5 pb-3">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-[10px] font-bold uppercase tracking-wider text-white/40">Claim progress</span>
                  <span className="text-xs font-bold text-emerald-400">{claimPct.toFixed(1)}%</span>
                </div>
                <div className="h-2.5 rounded-full bg-white/[0.06] overflow-hidden">
                  <motion.div
                    className="h-full rounded-full bg-gradient-to-r from-emerald-500 to-emerald-400"
                    initial={{ width: 0 }}
                    animate={{ width: `${Math.max(claimPct, 0.5)}%` }}
                    transition={{ duration: 1.2, ease: 'easeOut' }}
                  />
                </div>
                <div className="flex items-center justify-between mt-1.5">
                  <span className="text-[10px] text-white/25">{formatBalance(claimedAmount)} claimed</span>
                  <span className="text-[10px] text-white/25">{formatBalance(snapshotStats.total_owed)} total</span>
                </div>
              </div>

              {/* Stats grid */}
              <div className="grid grid-cols-2 md:grid-cols-4 border-t border-white/[0.06]">
                <div className="p-4 text-center border-r border-b md:border-b-0 border-white/[0.06]">
                  <p className="text-lg font-bold text-white/90 tabular-nums">{snapshotStats.total_addresses.toLocaleString()}</p>
                  <p className="text-[10px] font-bold uppercase tracking-wider text-white/30 mt-1">Wallets</p>
                </div>
                <div className="p-4 text-center border-b md:border-b-0 md:border-r border-white/[0.06]">
                  <p className="text-lg font-bold text-amber-400 tabular-nums">{formatBalance(snapshotStats.total_owed)}</p>
                  <p className="text-[10px] font-bold uppercase tracking-wider text-white/30 mt-1">Total</p>
                </div>
                <div className="p-4 text-center border-r border-white/[0.06]">
                  <p className="text-lg font-bold text-emerald-400 tabular-nums">{formatBalance(claimedAmount)}</p>
                  <p className="text-[10px] font-bold uppercase tracking-wider text-white/30 mt-1">Claimed ({claimedCount.toLocaleString()})</p>
                </div>
                <div className="p-4 text-center">
                  <p className="text-lg font-bold text-white/40 tabular-nums">{formatBalance(Math.max(snapshotStats.total_owed - claimedAmount, 0))}</p>
                  <p className="text-[10px] font-bold uppercase tracking-wider text-white/30 mt-1">Unclaimed</p>
                </div>
              </div>
            </div>
          )
        })()}

        {/* ── Snapshot Table ────────────────────────────────────────── */}
        <div className="border border-white/10 rounded-lg bg-white/[0.02] overflow-hidden">

          {/* Toolbar */}
          <div className="flex items-center gap-3 p-4 border-b border-white/5">
            <input
              type="text"
              placeholder="Search address..."
              value={search}
              onChange={e => { setSearch(e.target.value); setPage(0) }}
              className="flex-1 text-xs px-3 py-2 rounded-lg border border-white/10 bg-white/5 text-white/90 focus:outline-none focus:border-white/30 font-mono transition-colors placeholder:text-white/20"
            />

            <div className="flex rounded-lg border border-white/10 overflow-hidden">
              {claimFilters.map(f => (
                <button
                  key={f.key}
                  onClick={() => { setClaimFilter(f.key); setPage(0) }}
                  className={`px-3 py-1.5 text-[10px] font-bold uppercase tracking-wider transition-colors ${
                    claimFilter === f.key
                      ? 'bg-white/10 text-white/80'
                      : 'bg-white/[0.02] text-white/30 hover:text-white/50'
                  }`}
                >
                  {f.label}
                </button>
              ))}
            </div>

            <button
              onClick={refreshAfterAction}
              disabled={loading}
              className="p-2 rounded-lg border border-white/10 bg-white/5 text-white/40 hover:text-white/70 hover:bg-white/10 disabled:opacity-30 transition-colors"
            >
              <ArrowPathIcon className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
            </button>
          </div>

          {/* Table Header */}
          <div className="grid grid-cols-[1fr_140px_100px_140px] gap-2 px-4 py-2.5 border-b border-white/5 bg-white/[0.01]">
            <button onClick={() => toggleSort('address')} className="flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-wider text-white/40 hover:text-white/60 transition-colors text-left">
              Address <SortIcon col="address" />
            </button>
            <button onClick={() => toggleSort('balance')} className="flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-wider text-white/40 hover:text-white/60 transition-colors text-right justify-end">
              Balance <SortIcon col="balance" />
            </button>
            <button onClick={() => toggleSort('claimed')} className="flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-wider text-white/40 hover:text-white/60 transition-colors text-center justify-center">
              Status <SortIcon col="claimed" />
            </button>
            <div className="text-[10px] font-bold uppercase tracking-wider text-white/40 text-right">
              EVM
            </div>
          </div>

          {/* Table Body */}
          {loading && snapshotEntries.length === 0 ? (
            <div className="text-center py-12">
              <ArrowPathIcon className="w-5 h-5 animate-spin text-white/20 mx-auto mb-2" />
              <span className="text-xs text-white/30">Loading snapshot...</span>
            </div>
          ) : filteredTotal === 0 ? (
            <div className="text-center py-12">
              <span className="text-xs text-white/30 uppercase tracking-wider">No entries found</span>
            </div>
          ) : (
            <div>
              {pageEntries.map((entry) => {
                const isEditing = editingAddress === entry.address
                const userCanEdit = canEdit(entry)

                return (
                  <div key={entry.address}>
                    <div
                      className={`grid grid-cols-[1fr_140px_100px_140px] gap-2 px-4 py-2.5 border-b border-white/[0.03] hover:bg-white/[0.03] transition-colors items-center ${
                        isEditing ? 'bg-white/[0.04]' : ''
                      }`}
                    >
                      {/* Address */}
                      <button
                        onClick={() => copyToClipboard(entry.address, 'Address')}
                        className="text-xs font-mono text-white/60 hover:text-white/90 transition-colors truncate text-left"
                        title={entry.address}
                      >
                        {entry.address}
                      </button>

                      {/* Balance */}
                      <span className="text-xs font-bold text-amber-400/80 text-right">
                        {formatBalance(entry.balance)} MOD
                      </span>

                      {/* Status */}
                      <div className="flex justify-center">
                        {entry.claimed ? (
                          <span className="text-[10px] font-bold uppercase px-1.5 py-0.5 rounded border text-emerald-400 border-emerald-500/30 bg-emerald-500/10">
                            claimed
                          </span>
                        ) : (
                          <span className="text-[10px] font-bold uppercase px-1.5 py-0.5 rounded border text-white/25 border-white/10 bg-white/[0.02]">
                            pending
                          </span>
                        )}
                      </div>

                      {/* EVM / Edit */}
                      <div className="flex items-center justify-end gap-1.5">
                        {entry.evm_address ? (
                          <>
                            <button
                              onClick={() => copyToClipboard(entry.evm_address!, 'EVM')}
                              className="text-xs font-mono text-white/40 hover:text-white/70 transition-colors"
                              title={entry.evm_address}
                            >
                              {formatAddress(entry.evm_address)}
                            </button>
                            {userCanEdit && (
                              <button
                                onClick={() => { setEditingAddress(isEditing ? null : entry.address); setEditEvmAddress(entry.evm_address || '') }}
                                className="p-0.5 rounded hover:bg-white/10 transition-colors"
                                title="Update EVM address"
                              >
                                <PencilIcon className="w-3 h-3 text-white/20 hover:text-white/50" />
                              </button>
                            )}
                          </>
                        ) : (
                          <span className="text-[10px] text-white/15">--</span>
                        )}
                      </div>
                    </div>

                    {/* Inline edit row */}
                    {isEditing && (
                      <div className="px-4 py-3 border-b border-white/[0.03] bg-white/[0.02]">
                        <div className="flex items-center gap-2 max-w-md">
                          <input
                            type="text"
                            placeholder="New 0x address..."
                            value={editEvmAddress}
                            onChange={e => setEditEvmAddress(e.target.value)}
                            className={`flex-1 text-xs px-3 py-2 rounded-lg border bg-white/5 text-white/90 focus:outline-none font-mono transition-colors placeholder:text-white/20 ${
                              editEvmAddress.trim() && !isValidEvmAddress(editEvmAddress.trim())
                                ? 'border-red-500/40'
                                : editEvmAddress.trim() && isValidEvmAddress(editEvmAddress.trim())
                                ? 'border-emerald-500/30'
                                : 'border-white/10'
                            }`}
                          />
                          <button
                            onClick={() => handleUpdate(entry.address, entry.source_type as SourceType, editEvmAddress.trim())}
                            disabled={commitProcessing || !editEvmAddress.trim() || !isValidEvmAddress(editEvmAddress.trim()) || editEvmAddress.trim().toLowerCase() === entry.evm_address?.toLowerCase()}
                            className="px-3 py-2 rounded-lg border border-emerald-500/40 bg-emerald-500/15 text-emerald-300 text-[10px] font-bold uppercase tracking-wider hover:bg-emerald-500/25 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                          >
                            {commitProcessing ? <ArrowPathIcon className="w-3.5 h-3.5 animate-spin" /> : 'Update'}
                          </button>
                          <button
                            onClick={() => { setEditingAddress(null); setEditEvmAddress('') }}
                            className="px-2 py-2 text-xs text-white/30 hover:text-white/60 transition-colors"
                          >
                            cancel
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          )}

          {/* Pagination */}
          {totalPages > 1 && (
            <div className="flex items-center justify-between px-4 py-3 border-t border-white/5">
              <button
                onClick={() => setPage(p => Math.max(0, p - 1))}
                disabled={page === 0}
                className="text-xs text-white/40 hover:text-white/70 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
              >
                Previous
              </button>
              <span className="text-xs text-white/30">
                {page + 1} / {totalPages}
                <span className="ml-3 text-white/20">{filteredTotal} entries</span>
              </span>
              <button
                onClick={() => setPage(p => Math.min(totalPages - 1, p + 1))}
                disabled={page >= totalPages - 1}
                className="text-xs text-white/40 hover:text-white/70 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
              >
                Next
              </button>
            </div>
          )}
        </div>

        {/* Footer — public audit surface. Anyone can re-hash the snapshot
            file on disk and verify the CID matches. */}
        <div className="text-center text-xs text-white/15 uppercase tracking-wider py-4 space-y-1">
          <div>Bridge Module</div>
          {audit && (
            <div className="flex items-center justify-center gap-3 normal-case tracking-normal">
              <button
                onClick={() => copyToClipboard(audit.cid, 'CID')}
                className="font-mono text-white/30 hover:text-white/60 transition-colors"
                title={`${audit.algo}:${audit.cid}`}
              >
                cid {audit.cid.slice(0, 10)}…{audit.cid.slice(-6)}
              </button>
              <span className="text-white/15">·</span>
              <span className="font-mono text-white/30" title={new Date(audit.updated_at * 1000).toISOString()}>
                updated {new Date(audit.updated_at * 1000).toLocaleString()}
              </span>
              <span className="text-white/15">·</span>
              <span className="font-mono text-white/30">{audit.addresses.toLocaleString()} addrs</span>
            </div>
          )}
        </div>

        {/* API URL debug — dev only */}
        {process.env.NODE_ENV === 'development' && (
          <div className="fixed bottom-3 right-3 text-[10px] font-mono text-white/20 bg-white/5 border border-white/10 rounded px-2 py-1">
            API: {API_URL} | APP: {APP_URL}
          </div>
        )}
      </div>
    </div>
  )
}

export default BridgePageInner
