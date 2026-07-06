"use client"

import { useState, useEffect, useCallback } from 'react'
import { ethers } from 'ethers'
import { getChainConfig } from '@/network/chainConfig'
import { userContext } from '@/context'
import {
  getSafeInfo,
  getPendingTransactions,
  getExecutedTransactions,
  confirmTransaction,
  executeTransaction,
  type SafeInfo,
  type PendingTransaction,
  type ExecutedTransaction,
} from '@/network/safe'
import { toast } from 'react-toastify'

import { OverviewTab } from './OverviewTab'
import { TransactionsTab } from './TransactionsTab'
import { ProposeTab } from './ProposeTab'
import { OwnersTab } from './OwnersTab'
import { CreateTab } from './CreateTab'

export const dynamic = 'force-dynamic'

type Tab = 'overview' | 'transactions' | 'propose' | 'owners' | 'create'

const TERM_FONT = "var(--font-digital), 'JetBrains Mono', 'Courier New', monospace"

const TABS: { key: Tab; label: string; icon: string }[] = [
  { key: 'overview', label: 'OVERVIEW', icon: '\u25C9' },
  { key: 'transactions', label: 'TRANSACTIONS', icon: '\u25A4' },
  { key: 'propose', label: 'PROPOSE', icon: '\u25B6' },
  { key: 'owners', label: 'OWNERS', icon: '\u2630' },
  { key: 'create', label: 'CREATE', icon: '+' },
]

export default function SafePage() {
  const { user } = userContext()
  const walletAddress = user?.key || ''

  const [activeTab, setActiveTab] = useState<Tab>('overview')
  const defaultSafe = getChainConfig()?.contracts?.Safe?.address || ''
  const [safeAddress, setSafeAddress] = useState(defaultSafe)
  const [safeInfo, setSafeInfo] = useState<SafeInfo | null>(null)
  const [ethBalance, setEthBalance] = useState('0')
  const [loading, setLoading] = useState(false)
  const [isOwner, setIsOwner] = useState(false)

  const [pendingTxs, setPendingTxs] = useState<PendingTransaction[]>([])
  const [txLoading, setTxLoading] = useState(false)
  const [actionLoading, setActionLoading] = useState<string | null>(null)

  const [executedTxs, setExecutedTxs] = useState<ExecutedTransaction[]>([])
  const [historyLoading, setHistoryLoading] = useState(false)

  const loadSafe = useCallback(async () => {
    if (!safeAddress || !ethers.isAddress(safeAddress)) {
      toast.error('Enter a valid Safe address')
      return
    }
    setLoading(true)
    try {
      if (!window.ethereum) throw new Error('No wallet connected')
      const provider = new ethers.BrowserProvider(window.ethereum)
      const info = await getSafeInfo(safeAddress, provider)
      setSafeInfo(info)
      setIsOwner(info.owners.some((o: string) => o.toLowerCase() === walletAddress.toLowerCase()))
      const bal = await provider.getBalance(safeAddress)
      setEthBalance(ethers.formatEther(bal))
    } catch (err: any) {
      console.error(err)
      toast.error(err?.message || 'Failed to load Safe')
    } finally {
      setLoading(false)
    }
  }, [safeAddress, walletAddress])

  useEffect(() => {
    if (safeAddress && walletAddress) loadSafe()
  }, [walletAddress]) // eslint-disable-line react-hooks/exhaustive-deps

  const fetchPendingTxs = useCallback(async () => {
    if (!safeInfo) return
    setTxLoading(true)
    try {
      if (!window.ethereum) throw new Error('No wallet connected')
      const provider = new ethers.BrowserProvider(window.ethereum)
      const network = await provider.getNetwork()
      const txs = await getPendingTransactions(safeInfo.address, network.chainId)
      setPendingTxs(txs)
    } catch (err: any) {
      console.error(err)
      toast.error('Failed to fetch transactions')
    } finally {
      setTxLoading(false)
    }
  }, [safeInfo])

  const fetchHistory = useCallback(async () => {
    if (!safeInfo) return
    setHistoryLoading(true)
    try {
      if (!window.ethereum) throw new Error('No wallet connected')
      const provider = new ethers.BrowserProvider(window.ethereum)
      const txs = await getExecutedTransactions(safeInfo.address, provider)
      setExecutedTxs(txs)
    } catch (err: any) {
      console.error(err)
      toast.error('Failed to fetch history')
    } finally {
      setHistoryLoading(false)
    }
  }, [safeInfo])

  useEffect(() => {
    if (activeTab === 'transactions' && safeInfo) {
      fetchPendingTxs()
      fetchHistory()
    }
  }, [activeTab, safeInfo, fetchPendingTxs, fetchHistory])

  async function handleConfirm(tx: PendingTransaction) {
    if (!walletAddress) { toast.error('Connect wallet first'); return }
    setActionLoading(tx.safeTxHash)
    try {
      if (!window.ethereum) throw new Error('No wallet connected')
      const provider = new ethers.BrowserProvider(window.ethereum)
      const signer = await provider.getSigner(walletAddress)
      const network = await provider.getNetwork()
      await confirmTransaction(safeInfo!.address, tx.safeTxHash, signer, network.chainId)
      toast.success('Transaction confirmed')
      fetchPendingTxs()
    } catch (err: any) {
      console.error(err)
      toast.error(err?.message || 'Confirmation failed')
    } finally {
      setActionLoading(null)
    }
  }

  async function handleExecute(tx: PendingTransaction) {
    if (!walletAddress) { toast.error('Connect wallet first'); return }
    setActionLoading(tx.safeTxHash)
    try {
      if (!window.ethereum) throw new Error('No wallet connected')
      const provider = new ethers.BrowserProvider(window.ethereum)
      const signer = await provider.getSigner(walletAddress)
      const hash = await executeTransaction(safeInfo!.address, tx, signer)
      toast.success(`Executed: ${hash.slice(0, 10)}...`)
      fetchPendingTxs()
      loadSafe()
    } catch (err: any) {
      console.error(err)
      toast.error(err?.message || 'Execution failed')
    } finally {
      setActionLoading(null)
    }
  }

  const handleSafeCreated = (address: string) => {
    setSafeAddress(address)
    setActiveTab('overview')
    setTimeout(() => loadSafe(), 100)
  }

  return (
    <div className="min-h-screen" style={{ fontFamily: TERM_FONT, backgroundColor: 'var(--bg-primary)', color: 'var(--text-primary)' }}>
      <div className="max-w-5xl mx-auto px-4 py-8">

        {/* Header */}
        <div className="mb-8">
          <div className="flex items-center gap-3 mb-2">
            <span style={{ color: 'var(--accent-primary, #10b981)', fontSize: '20px', fontFamily: TERM_FONT }}>$</span>
            <span style={{
              fontFamily: TERM_FONT,
              fontSize: '22px',
              letterSpacing: '0.08em',
              color: 'var(--accent-primary, #10b981)',
              textShadow: '0 0 12px var(--accent-primary, #10b981)',
            }}>
              safe
            </span>
            <span style={{ color: 'var(--text-tertiary)', fontSize: '14px', fontFamily: TERM_FONT }}>
              --multisig
            </span>
          </div>
          <p style={{ color: 'var(--text-tertiary)', fontSize: '14px', fontFamily: TERM_FONT }}>
            Manage multisig accounts and propose transactions
          </p>
          <div className="mt-3" style={{ height: '2px', background: 'var(--accent-primary, #10b981)', opacity: 0.3 }} />
        </div>

        {/* Safe address input */}
        <div
          className="mb-8"
          style={{
            border: '2px solid var(--border-color)',
            background: 'var(--bg-secondary)',
            padding: '16px 20px',
            boxShadow: '3px 3px 0px 0px rgba(255,255,255,0.06)',
          }}
        >
          <label style={{
            display: 'block',
            fontFamily: TERM_FONT,
            fontSize: '13px',
            letterSpacing: '0.15em',
            textTransform: 'uppercase',
            color: 'var(--accent-primary, #10b981)',
            opacity: 0.7,
            marginBottom: '8px',
          }}>SAFE ADDRESS</label>
          <div className="flex items-center gap-3">
            <input
              type="text"
              value={safeAddress}
              onChange={(e) => setSafeAddress(e.target.value)}
              placeholder="0x..."
              className="flex-1 bg-transparent outline-none"
              style={{
                fontFamily: TERM_FONT,
                fontSize: '15px',
                color: 'var(--text-primary)',
                border: 'none',
                padding: '4px 0',
              }}
              onKeyDown={(e) => e.key === 'Enter' && loadSafe()}
            />
            <button
              onClick={loadSafe}
              disabled={loading}
              className="transition-all"
              style={{
                fontFamily: TERM_FONT,
                fontSize: '14px',
                padding: '6px 16px',
                border: '2px solid var(--accent-primary, #10b981)',
                color: 'var(--accent-primary, #10b981)',
                background: 'transparent',
                boxShadow: '2px 2px 0px 0px var(--accent-primary, #10b981)',
                cursor: loading ? 'not-allowed' : 'pointer',
                opacity: loading ? 0.4 : 1,
              }}
            >
              {loading ? '...' : 'Load'}
            </button>
          </div>

          {/* Status line */}
          {safeInfo && (
            <div
              className="mt-4 flex flex-wrap gap-x-6 gap-y-2 pt-3"
              style={{
                borderTop: '1px solid var(--border-color)',
                fontFamily: TERM_FONT,
                fontSize: '13px',
                color: 'var(--text-tertiary)',
              }}
            >
              <span>owners: <span style={{ color: 'var(--accent-primary, #10b981)' }}>{safeInfo.owners.length}</span></span>
              <span>threshold: <span style={{ color: 'var(--accent-primary, #10b981)' }}>{safeInfo.threshold}/{safeInfo.owners.length}</span></span>
              <span>nonce: <span style={{ color: 'var(--accent-primary, #10b981)' }}>{safeInfo.nonce}</span></span>
              <span>eth: <span style={{ color: 'var(--accent-primary, #10b981)' }}>{parseFloat(ethBalance).toFixed(4)}</span></span>
              {isOwner && <span style={{ color: 'var(--accent-primary, #10b981)' }}>[signer]</span>}
            </div>
          )}
        </div>

        {/* Tab bar */}
        <div className="flex gap-2 mb-8" style={{ borderBottom: '2px solid var(--border-color)', paddingBottom: '8px' }}>
          {TABS.map((tab) => {
            const active = activeTab === tab.key
            return (
              <button
                key={tab.key}
                onClick={() => setActiveTab(tab.key)}
                className="transition-all"
                style={{
                  fontFamily: TERM_FONT,
                  fontSize: '13px',
                  letterSpacing: '0.1em',
                  padding: '8px 16px',
                  border: active ? '2px solid var(--accent-primary, #10b981)' : '2px solid transparent',
                  background: active ? 'rgba(16, 185, 129, 0.08)' : 'transparent',
                  color: active ? 'var(--accent-primary, #10b981)' : 'var(--text-tertiary)',
                  boxShadow: active ? '2px 2px 0px 0px var(--accent-primary, #10b981)' : 'none',
                  cursor: 'pointer',
                  textShadow: active ? '0 0 8px var(--accent-primary, #10b981)' : 'none',
                }}
              >
                <span style={{ marginRight: '6px' }}>{tab.icon}</span>
                {tab.label}
              </button>
            )
          })}
        </div>

        {/* Tab content */}
        {activeTab === 'overview' && (
          <OverviewTab safeInfo={safeInfo} walletAddress={walletAddress} ethBalance={ethBalance} loading={loading} />
        )}

        {activeTab === 'transactions' && (
          <TransactionsTab
            safeInfo={safeInfo}
            walletAddress={walletAddress}
            isOwner={isOwner}
            pendingTxs={pendingTxs}
            txLoading={txLoading}
            actionLoading={actionLoading}
            executedTxs={executedTxs}
            historyLoading={historyLoading}
            onConfirm={handleConfirm}
            onExecute={handleExecute}
            onRefresh={fetchPendingTxs}
          />
        )}

        {activeTab === 'propose' && (
          <ProposeTab safeInfo={safeInfo} walletAddress={walletAddress} isOwner={isOwner} />
        )}

        {activeTab === 'owners' && (
          <OwnersTab safeInfo={safeInfo} walletAddress={walletAddress} isOwner={isOwner} onReloadSafe={loadSafe} />
        )}

        {activeTab === 'create' && (
          <CreateTab walletAddress={walletAddress} onSafeCreated={handleSafeCreated} />
        )}
      </div>
    </div>
  )
}
