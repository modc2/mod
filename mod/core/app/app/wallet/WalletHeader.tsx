"use client";

import { useState, useEffect, useRef } from 'react'
import { userContext } from '@/context/UserContext'
import { ClipboardDocumentIcon, ClipboardDocumentCheckIcon, ChevronDownIcon, ArrowRightStartOnRectangleIcon, ArrowPathIcon } from '@heroicons/react/24/outline'
import { AnimatePresence, motion } from 'framer-motion'
import { WalletAuthButton } from './WalletAuthButton'
import { text2color } from '@/utils'
import { toast } from 'react-toastify'

import { Auth } from '@/client/auth'
import { useTokenExpiry } from './hooks/useTokenExpiry'
import { useBalances } from './hooks/useBalances'
import { useTransactions } from './hooks/useTransactions'
import { useTransfers } from './hooks/useTransfers'
import { useWalletAccounts } from './hooks/useWalletAccounts'
import { useNetwork } from './hooks/useNetwork'
import { useSidebarResize } from './hooks/useSidebarResize'
import { WalletSidebar } from './sidebar/WalletSidebar'

const WalletModeLogo = ({ mode, size = 16 }: { mode: string; size?: number }) => {
  const m = mode.toLowerCase()
  if (m.includes('metamask')) return (
    <svg width={size} height={size} viewBox="0 0 318.6 318.6" fill="none">
      <path d="M274.1 35.5l-99.5 73.9 18.4-43.6z" fill="#E17726" stroke="#E17726" strokeWidth="1.5"/>
      <path d="M44.4 35.5l98.7 74.6-17.5-44.3zm193.9 171.3l-26.5 40.6 56.7 15.6 16.3-55.3zm-204.4.9L50.1 263l56.7-15.6-26.5-40.6z" fill="#E27625" stroke="#E27625" strokeWidth="1.5"/>
      <path d="M103.6 138.2l-15.8 23.9 56.3 2.5-2-60.5zm111.3 0l-39-34.8-1.3 61.2 56.2-2.5z" fill="#E27625" stroke="#E27625" strokeWidth="1.5"/>
      <path d="M267.2 153.5l-52.3-15.3 15.8 23.9-23.7 46 31.2-.4h46.5zm-163.6-15.3l-52.3 15.3-17.4 54.2h46.4l31.1.4-23.6-46zm71 26.4l3.3-57.7 15.2-41.1h-67.5l15 41.1 3.5 57.7 1.2 18.2.1 44.8h27.7l.2-44.8z" fill="#F5841F" stroke="#F5841F" strokeWidth="1.5"/>
    </svg>
  )
  if (m.includes('phantom')) return (
    <svg width={size} height={size} viewBox="0 0 128 128" fill="none">
      <rect width="128" height="128" rx="24" fill="url(#pg)"/>
      <path d="M96 64c0 17.673-14.327 32-32 32s-32-14.327-32-32V32h64v32z" fill="white"/>
      <circle cx="52" cy="58" r="6" fill="#AB9FF2"/><circle cx="76" cy="58" r="6" fill="#AB9FF2"/>
      <defs><linearGradient id="pg" x1="0" y1="0" x2="128" y2="128"><stop stopColor="#AB9FF2"/><stop offset="1" stopColor="#7C66DC"/></linearGradient></defs>
    </svg>
  )
  if (m.includes('subwallet')) return (
    <svg width={size} height={size} viewBox="0 0 200 200" fill="none">
      <circle cx="100" cy="100" r="100" fill="url(#sg)"/>
      <path d="M100 40L60 80L100 120L140 80L100 40Z" fill="white"/>
      <path d="M100 120L60 160L100 200L140 160L100 120Z" fill="white" opacity="0.6"/>
      <defs><linearGradient id="sg" x1="0" y1="0" x2="200" y2="200"><stop stopColor="#00E5CC"/><stop offset="1" stopColor="#00B8D4"/></linearGradient></defs>
    </svg>
  )
  // local/default - key icon
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
      <circle cx="7" cy="7" r="4" fill="none"/><path d="M7 11V22M11 15H7M11 19H7"/>
    </svg>
  )
}

export function WalletHeader() {
  const { user, signOut, switchWallet, client } = userContext()
  const [isOpen, setIsOpen] = useState(false)
  const [copiedAddress, setCopiedAddress] = useState(false)
  const [activeTab, setActiveTab] = useState<string | null>('portfolio')
  const [showAccountsDropdown, setShowAccountsDropdown] = useState(false)
  const [showTokenDropdown, setShowTokenDropdown] = useState(false)
  const [copiedToken, setCopiedToken] = useState(false)
  const [tokenView, setTokenView] = useState<'raw' | 'json' | 'create'>('raw')
  const [createTokenData, setCreateTokenData] = useState('')
  const [showAuthSidebar, setShowAuthSidebar] = useState(false)
  const [barExpanded, setBarExpanded] = useState(true)
  const walletRef = useRef<HTMLDivElement>(null)
  const dropdownRef = useRef<HTMLDivElement>(null)
  const tokenDropdownRef = useRef<HTMLDivElement>(null)

  const address = user?.key || ''
  const walletMode = user?.wallet_mode || ''
  const shortAddress = address ? `${address.slice(0, 6)}...${address.slice(-4)}` : ''
  const userColor = user ? text2color(user.key || '') : '#10b981'

  // Hooks
  const tokenExpiry = useTokenExpiry(!!user)
  const balances = useBalances(user?.key, client)
  const transactions = useTransactions(user?.key, client)
  const transfers = useTransfers({
    userKey: user?.key,
    marketCredit: balances.marketCredit,
    tokenBalances: balances.tokenBalances,
    fetchMarketCredit: balances.fetchMarketCredit,
    fetchCustomTokenBalances: balances.fetchCustomTokenBalances,
  })
  const accounts = useWalletAccounts(user?.key, address, switchWallet)
  useNetwork(user?.key, client, balances.fetchMarketCredit)
  const sidebar = useSidebarResize()

  const [walletTypeState, setWalletTypeState] = useState(() =>
    typeof window !== 'undefined' ? localStorage.getItem('wallet_type') || 'ecdsa' : 'ecdsa'
  )
  const [localWalletMode, setLocalWalletMode] = useState(walletMode)
  useEffect(() => { setLocalWalletMode(walletMode) }, [walletMode])

  const copyAddress = () => {
    if (address) {
      navigator.clipboard.writeText(address)
      setCopiedAddress(true)
      setTimeout(() => setCopiedAddress(false), 2000)
    }
  }

  const handleSignOut = () => {
    signOut()
    setIsOpen(false)
    setShowTokenDropdown(false)
  }

  const copyToken = () => {
    const token = localStorage.getItem('wallet_token')
    if (token) {
      navigator.clipboard.writeText(token)
      setCopiedToken(true)
      setTimeout(() => setCopiedToken(false), 2000)
    }
  }

  const openTab = (tab: string) => {
    setActiveTab(activeTab === tab ? null : tab)
    transfers.setSendFromPortfolio(null)
    if (tab === 'txs' && activeTab !== 'txs') transactions.fetchUserTransactions()
    if (tab === 'wallets') accounts.refreshHistory()
  }

  // Fetch txs when txs tab opens
  useEffect(() => {
    if (activeTab === 'txs' && user?.key) transactions.fetchUserTransactions()
  }, [activeTab])

  // Close wallet sidebar and expanded bar when clicking outside
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (walletRef.current && !walletRef.current.contains(event.target as Node)) {
        setIsOpen(false)
      }
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setShowAccountsDropdown(false)
      }
      if (tokenDropdownRef.current && !tokenDropdownRef.current.contains(event.target as Node)) {
        setShowTokenDropdown(false)
      }
    }

    if (isOpen || showAccountsDropdown || showTokenDropdown) {
      document.addEventListener('mousedown', handleClickOutside)
    }

    return () => {
      document.removeEventListener('mousedown', handleClickOutside)
    }
  }, [isOpen, showAccountsDropdown, showTokenDropdown])

  const getWalletModeColor = (mode: string) => {
    const m = mode.toLowerCase()
    if (m.includes('metamask')) return '#f6851b'
    if (m.includes('phantom')) return '#ab9ff2'
    if (m.includes('subwallet')) return '#004bff'
    if (m.includes('coinbase')) return '#0052ff'
    return '#10b981'
  }

  if (!user) {
    return (
      <div className="flex items-center gap-3">
        <WalletAuthButton
          showAuthSidebar={showAuthSidebar}
          setShowAuthSidebar={setShowAuthSidebar}
        />
      </div>
    )
  }

  const KEY_TYPE_CYCLE: { type: string; mode: string; label: string }[] = [
    { type: 'ethereum', mode: 'metamask', label: 'ETH' },
    { type: 'solana', mode: 'phantom', label: 'SOL' },
    { type: 'ecdsa', mode: 'local', label: 'ECDSA' },
    { type: 'sr25519', mode: 'subwallet', label: 'SR25' },
  ]
  const getKeyTypeColor = (type: string) => {
    if (type === 'sr25519') return '#06b6d4'
    if (type === 'ecdsa') return '#f59e0b'
    if (type === 'ethereum') return '#627eea'
    if (type === 'solana') return '#9945ff'
    return '#10b981'
  }
  const keyColor = getKeyTypeColor(walletTypeState)
  const keyMode = KEY_TYPE_CYCLE.find(k => k.type === walletTypeState)?.mode || localWalletMode

  return (
    <div ref={walletRef} className="flex items-center gap-2">
      {/* Wallet switcher button — always visible */}
      <div className="relative" ref={dropdownRef}>
        <motion.button
          onClick={() => { accounts.refreshHistory(); setShowAccountsDropdown(!showAccountsDropdown) }}
          initial={{ scale: 0.8, opacity: 0 }}
          animate={{ scale: 1, opacity: 1 }}
          whileHover={{ scale: 1.08 }}
          whileTap={{ scale: 0.95 }}
          transition={{ duration: 0.2, ease: [0.23, 1, 0.32, 1] }}
          className="flex items-center justify-center transition-all"
          style={{
            width: '32px',
            height: '32px',
            backgroundColor: showAccountsDropdown ? `${userColor}18` : 'var(--bg-input)',
            border: `1px solid ${showAccountsDropdown ? `${userColor}40` : 'var(--border-color)'}`,
            borderRadius: '999px',
            color: userColor,
            cursor: 'pointer',
            boxShadow: `0 0 10px ${userColor}20, 0 2px 6px rgba(0,0,0,0.15)`,
          }}
          title="Switch wallet"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke={userColor} strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
            <path d="M21 12V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2h14a2 2 0 002-2v-5z" />
            <circle cx="17" cy="12" r="1.5" fill={userColor} />
          </svg>
        </motion.button>

        {/* Wallet accounts dropdown */}
        <AnimatePresence>
          {showAccountsDropdown && (
            <motion.div
              initial={{ opacity: 0, y: -6, scale: 0.95 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: -6, scale: 0.95 }}
              transition={{ duration: 0.15, ease: [0.23, 1, 0.32, 1] }}
              className="absolute right-0 top-full mt-2 z-[200] overflow-hidden"
              style={{
                minWidth: '220px',
                background: 'var(--bg-secondary)',
                border: '1px solid var(--border-color)',
                borderRadius: '14px',
                boxShadow: '0 12px 32px rgba(0,0,0,0.4)',
                backdropFilter: 'blur(12px)',
                fontFamily: 'var(--font-digital), monospace',
              }}
            >
              {/* Current wallet */}
              <div className="px-3 pt-2.5 pb-1.5">
                <div className="text-[9px] font-bold uppercase tracking-widest" style={{ color: 'var(--text-tertiary)', opacity: 0.5 }}>Current</div>
              </div>
              <div
                className="flex items-center gap-2 px-3 py-2 mx-1.5 mb-1 rounded-lg"
                style={{ backgroundColor: `${userColor}12` }}
              >
                <div className="flex-shrink-0" style={{ color: userColor, filter: `drop-shadow(0 0 4px ${userColor}60)` }}>
                  <WalletModeLogo mode={walletMode || keyMode} size={14} />
                </div>
                <span className="text-[11px] font-bold tabular-nums" style={{ color: userColor }}>
                  {shortAddress}
                </span>
                <span className="flex-1" />
                <svg className="w-2.5 h-2.5" fill="none" viewBox="0 0 24 24" stroke={userColor} strokeWidth={2.5}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                </svg>
              </div>

              {/* Other wallets */}
              {accounts.walletHistory.filter(w => w.address.toLowerCase() !== address.toLowerCase()).length > 0 && (
                <>
                  <div className="px-3 pt-2 pb-1.5">
                    <div className="text-[9px] font-bold uppercase tracking-widest" style={{ color: 'var(--text-tertiary)', opacity: 0.5 }}>Wallets</div>
                  </div>
                  {accounts.walletHistory
                    .filter(w => w.address.toLowerCase() !== address.toLowerCase())
                    .map((w) => {
                      const wColor = text2color(w.address)
                      const wShort = `${w.address.slice(0, 6)}...${w.address.slice(-4)}`
                      return (
                        <button
                          key={w.address}
                          onClick={() => { accounts.handleSwitchWallet(w); setShowAccountsDropdown(false) }}
                          className="flex items-center gap-2 w-full px-3 py-2 mx-0 transition-all text-left group"
                          style={{ backgroundColor: 'transparent' }}
                          onMouseEnter={(e) => { e.currentTarget.style.backgroundColor = 'var(--hover-bg)' }}
                          onMouseLeave={(e) => { e.currentTarget.style.backgroundColor = 'transparent' }}
                          disabled={accounts.isSwitchingWallet}
                        >
                          <div className="flex-shrink-0" style={{ color: wColor, opacity: 0.6 }}>
                            <WalletModeLogo mode={w.mode} size={14} />
                          </div>
                          <span className="text-[11px] font-bold tabular-nums" style={{ color: 'var(--text-secondary)' }}>
                            {wShort}
                          </span>
                          <span className="flex-1" />
                          <span
                            onClick={(e) => { e.stopPropagation(); accounts.handleRemoveFromHistory(w.address) }}
                            className="opacity-0 group-hover:opacity-40 hover:!opacity-100 transition-opacity"
                            style={{ color: 'var(--text-tertiary)' }}
                            title="Remove"
                          >
                            <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.5} strokeLinecap="round">
                              <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
                            </svg>
                          </span>
                        </button>
                      )
                    })}
                </>
              )}

              {/* Sign out */}
              <div style={{ height: '1px', background: 'var(--border-color)', opacity: 0.3, margin: '4px 12px' }} />
              <button
                onClick={() => { handleSignOut(); setShowAccountsDropdown(false) }}
                className="flex items-center gap-2 w-full px-3 py-2 pb-2.5 transition-all text-left"
                style={{ color: '#ef4444', backgroundColor: 'transparent' }}
                onMouseEnter={(e) => { e.currentTarget.style.backgroundColor = 'rgba(239,68,68,0.06)' }}
                onMouseLeave={(e) => { e.currentTarget.style.backgroundColor = 'transparent' }}
              >
                <ArrowRightStartOnRectangleIcon className="w-3.5 h-3.5" />
                <span className="text-[10px] font-bold uppercase tracking-wider">Sign Out</span>
              </button>
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      {/* Expanded pill bar — address hidden for privacy, only shown inside sidebar */}
      {barExpanded && (
      <div className="relative" style={{ fontFamily: 'var(--font-digital), monospace' }}>
        <motion.div
          initial={{ opacity: 0, scale: 0.92, x: 10 }}
          animate={{ opacity: 1, scale: 1, x: 0 }}
          transition={{ duration: 0.2, ease: [0.23, 1, 0.32, 1] }}
          className="flex items-center"
          style={{
            height: '32px',
            overflow: 'hidden',
            borderRadius: '999px',
            background: 'var(--bg-input)',
            border: '1px solid var(--border-color)',
            boxShadow: `0 0 16px ${userColor}18, 0 2px 8px rgba(0,0,0,0.25)`,
          }}
        >
          {/* Wallet icon — click to open sidebar with address */}
          <button
            onClick={() => setIsOpen(!isOpen)}
            className="flex items-center gap-1.5 h-full pl-3 pr-2 transition-all hover:brightness-125"
            title="Open wallet"
          >
            <div className="flex-shrink-0" style={{ color: userColor, filter: `drop-shadow(0 0 4px ${userColor}60)` }}>
              <WalletModeLogo mode={walletMode || keyMode} size={14} />
            </div>
            <svg className={`w-2.5 h-2.5 transition-transform ${isOpen ? 'rotate-180' : ''}`} fill="none" viewBox="0 0 24 24" stroke={userColor} strokeWidth={2.5} style={{ opacity: 0.5 }}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
            </svg>
          </button>

          {/* Divider */}
          <div className="h-4 w-px flex-shrink-0" style={{ background: 'var(--border-color)', opacity: 0.5 }} />

          {/* Token timer */}
          <button
            onClick={() => tokenExpiry.handleRefreshToken()}
            className="flex items-center gap-1.5 h-full px-2.5 font-bold transition-all hover:brightness-125"
            style={{
              fontSize: '10px',
              color: tokenExpiry.isTokenExpired ? '#eab308' : 'var(--text-tertiary)',
              background: tokenExpiry.isTokenExpired ? 'rgba(234,179,8,0.06)' : 'transparent',
            }}
            title={tokenExpiry.isTokenExpired ? 'Token expired — click to refresh' : 'Click to refresh token'}
          >
            <ArrowPathIcon className={`w-3 h-3 ${tokenExpiry.isRefreshing ? 'animate-spin' : ''}`} style={{
              opacity: tokenExpiry.isTokenExpired ? 1 : 0.5,
              filter: tokenExpiry.isTokenExpired ? 'drop-shadow(0 0 4px rgba(234,179,8,0.5))' : 'none',
            }} />
            <span className="tabular-nums">{tokenExpiry.tokenExpiry || tokenExpiry.getTokenExpiry()}</span>
          </button>

          {/* Divider */}
          <div className="h-4 w-px flex-shrink-0" style={{ background: 'var(--border-color)', opacity: 0.5 }} />

          {/* Close */}
          <button
            onClick={() => setBarExpanded(false)}
            className="flex items-center justify-center h-full w-[32px] transition-all hover:brightness-150 flex-shrink-0"
            style={{ color: 'var(--text-tertiary)', opacity: 0.4 }}
            title="Collapse"
          >
            <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.5} strokeLinecap="round" strokeLinejoin="round">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </motion.div>
      </div>
      )}

      <AnimatePresence>
        {isOpen && (
          <WalletSidebar
            // Header
            shortAddress={shortAddress}
            walletMode={walletMode}
            copiedAddress={copiedAddress}
            copyAddress={copyAddress}
            tokenExpiry={tokenExpiry.tokenExpiry}
            getTokenExpiry={tokenExpiry.getTokenExpiry}
            isTokenExpired={tokenExpiry.isTokenExpired}
            isRefreshingToken={tokenExpiry.isRefreshing}
            handleRefreshToken={tokenExpiry.handleRefreshToken}
            handleSignOut={handleSignOut}
            onClose={() => setIsOpen(false)}
            marketCredit={balances.marketCredit}
            userColor={userColor}
            // Resize
            sidebarWidth={sidebar.sidebarWidth}
            isResizing={sidebar.isResizing}
            setIsResizing={sidebar.setIsResizing}
            // Tabs
            activeTab={activeTab}
            openTab={openTab}
            // Portfolio
            tokenBalances={balances.tokenBalances}
            customTokens={balances.customTokens}
            customTokenBalances={balances.customTokenBalances}
            sendFromPortfolio={transfers.sendFromPortfolio}
            setSendFromPortfolio={transfers.setSendFromPortfolio}
            transferRecipient={transfers.transferRecipient}
            setTransferRecipient={transfers.setTransferRecipient}
            transferAmount={transfers.transferAmount}
            setTransferAmount={transfers.setTransferAmount}
            isTransferring={transfers.isTransferring}
            setTransferTokenType={transfers.setTransferTokenType}
            topUpError={transfers.topUpError}
            setTopUpError={transfers.setTopUpError}
            topUpSuccess={transfers.topUpSuccess}
            setTopUpSuccess={transfers.setTopUpSuccess}
            showAddToken={balances.showAddToken}
            setShowAddToken={balances.setShowAddToken}
            newTokenAddress={balances.newTokenAddress}
            setNewTokenAddress={balances.setNewTokenAddress}
            isAddingToken={balances.isAddingToken}
            isRefreshingBalances={balances.isRefreshing}
            onRefreshBalances={balances.refreshAll}
            onTransfer={transfers.handleTransfer}
            onSendETH={transfers.handleSendETH}
            onSendCustomToken={transfers.handleSendCustomToken}
            onRemoveCustomToken={balances.handleRemoveCustomToken}
            onAddCustomToken={balances.handleAddCustomToken}
            // Top-up
            selectedToken={transfers.selectedToken}
            setSelectedToken={transfers.setSelectedToken}
            topUpAmount={transfers.topUpAmount}
            setTopUpAmount={transfers.setTopUpAmount}
            isProcessing={transfers.isProcessing}
            onTopUp={transfers.handleTopUpTransaction}
            // Withdraw
            withdrawTokenType={transfers.withdrawTokenType}
            setWithdrawTokenType={transfers.setWithdrawTokenType}
            withdrawAmount={transfers.withdrawAmount}
            setWithdrawAmount={transfers.setWithdrawAmount}
            isWithdrawing={transfers.isWithdrawing}
            onWithdraw={transfers.handleWithdraw}
            // Accounts
            address={address}
            walletHistory={accounts.walletHistory}
            isSwitchingWallet={accounts.isSwitchingWallet}
            onSwitchWallet={accounts.handleSwitchWallet}
            onRemoveFromHistory={accounts.handleRemoveFromHistory}
            // Transactions
            userTransactions={transactions.userTransactions}
            isLoadingTxs={transactions.isLoadingTxs}
            txsStatusFilter={transactions.txsStatusFilter}
            setTxsStatusFilter={transactions.setTxsStatusFilter}
            expandedTxIdx={transactions.expandedTxIdx}
            setExpandedTxIdx={transactions.setExpandedTxIdx}
            totalCost24h={transactions.totalCost24h}
          />
        )}
      </AnimatePresence>
    </div>
  )
}

export default WalletHeader
