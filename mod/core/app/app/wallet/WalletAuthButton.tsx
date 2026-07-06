"use client";
import { useState, useEffect, useMemo } from 'react'
import { userContext } from '@/context'
import { cryptoWaitReady } from '@polkadot/util-crypto'
// dynamic import: @polkadot/extension-dapp accesses window at load time
import { motion, AnimatePresence } from 'framer-motion'
import { Key } from '@/key'
import { XMarkIcon } from '@heroicons/react/24/outline'

type AuthMode = 'local' | 'subwallet' | 'metamask' | 'phantom'
type KeyTypeAbbrev = 'evm' | 'sol' | 'sub'

const KEY_TYPE_MAP: Record<KeyTypeAbbrev, { crypto: 'ecdsa' | 'sr25519' | 'solana'; label: string; color: string }> = {
  evm: { crypto: 'ecdsa', label: 'EVM', color: '#f6851b' },
  sol: { crypto: 'solana', label: 'SOL', color: '#9945ff' },
  sub: { crypto: 'sr25519', label: 'SUB', color: '#00e5cc' },
}

const WALLET_KEY_TYPES: Record<AuthMode, KeyTypeAbbrev[]> = {
  metamask: ['evm'],
  phantom: ['sol', 'evm'],
  subwallet: ['sub', 'evm'],
  local: ['evm', 'sol', 'sub'],
}

const MetamaskLogo = ({ size = 24 }: { size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 318.6 318.6" fill="none">
    <path d="M274.1 35.5l-99.5 73.9 18.4-43.6z" fill="#E17726" stroke="#E17726" strokeWidth="1.5"/>
    <path d="M44.4 35.5l98.7 74.6-17.5-44.3zm193.9 171.3l-26.5 40.6 56.7 15.6 16.3-55.3zm-204.4.9L50.1 263l56.7-15.6-26.5-40.6z" fill="#E27625" stroke="#E27625" strokeWidth="1.5"/>
    <path d="M103.6 138.2l-15.8 23.9 56.3 2.5-2-60.5zm111.3 0l-39-34.8-1.3 61.2 56.2-2.5z" fill="#E27625" stroke="#E27625" strokeWidth="1.5"/>
    <path d="M267.2 153.5l-52.3-15.3 15.8 23.9-23.7 46 31.2-.4h46.5zm-163.6-15.3l-52.3 15.3-17.4 54.2h46.4l31.1.4-23.6-46zm71 26.4l3.3-57.7 15.2-41.1h-67.5l15 41.1 3.5 57.7 1.2 18.2.1 44.8h27.7l.2-44.8z" fill="#F5841F" stroke="#F5841F" strokeWidth="1.5"/>
  </svg>
)

const PhantomLogo = ({ size = 24 }: { size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 128 128" fill="none">
    <rect width="128" height="128" rx="24" fill="url(#phantom-gradient)"/>
    <path d="M96 64c0 17.673-14.327 32-32 32s-32-14.327-32-32V32h64v32z" fill="white"/>
    <circle cx="52" cy="58" r="6" fill="#AB9FF2"/><circle cx="76" cy="58" r="6" fill="#AB9FF2"/>
    <defs><linearGradient id="phantom-gradient" x1="0" y1="0" x2="128" y2="128"><stop stopColor="#AB9FF2"/><stop offset="1" stopColor="#7C66DC"/></linearGradient></defs>
  </svg>
)

const SubwalletLogo = ({ size = 24 }: { size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 200 200" fill="none">
    <circle cx="100" cy="100" r="100" fill="url(#subwallet-gradient)"/>
    <path d="M100 40L60 80L100 120L140 80L100 40Z" fill="white"/>
    <path d="M100 120L60 160L100 200L140 160L100 120Z" fill="white" opacity="0.6"/>
    <defs><linearGradient id="subwallet-gradient" x1="0" y1="0" x2="200" y2="200"><stop stopColor="#00E5CC"/><stop offset="1" stopColor="#00B8D4"/></linearGradient></defs>
  </svg>
)

const LocalKeyLogo = ({ size = 24 }: { size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
    <circle cx="7" cy="7" r="4" fill="none"/><path d="M7 11V22M11 15H7M11 19H7"/>
    <path d="M16 8L22 8M19 5L19 11"/>
  </svg>
)

interface WalletAuthButtonProps {
  showAuthSidebar: boolean
  setShowAuthSidebar: (v: boolean) => void
}

export function WalletAuthButton({ showAuthSidebar, setShowAuthSidebar }: WalletAuthButtonProps) {
  const { user, signIn, authLoading } = userContext()
  const [authMode, setAuthMode] = useState<AuthMode>(() => {
    if (typeof window !== 'undefined') {
      const saved = localStorage.getItem('last_auth_mode') as AuthMode | null
      if (saved && ['local', 'subwallet', 'metamask', 'phantom'].includes(saved)) return saved
    }
    return 'metamask'
  })
  const [password, setPassword] = useState('')
  const [keyType, setKeyType] = useState<KeyTypeAbbrev>(() => {
    if (typeof window !== 'undefined') {
      const saved = localStorage.getItem('wallet_key_type') as KeyTypeAbbrev | null
      if (saved && ['evm', 'sol', 'sub'].includes(saved)) return saved
    }
    return WALLET_KEY_TYPES[authMode][0]
  })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [accounts, setAccounts] = useState<any[]>([])
  const [selectedAccount, setSelectedAccount] = useState<string>('')
  const [metamaskAccounts, setMetamaskAccounts] = useState<string[]>([])
  const [selectedMetamaskAccount, setSelectedMetamaskAccount] = useState<string>('')

  useEffect(() => {
    const checkWallets = async () => {
      const { web3Enable, web3Accounts } = await import('@polkadot/extension-dapp')
      const extensions = await web3Enable('MOD')
      if (extensions.length > 0) {
        const allAccounts = await web3Accounts()
        setAccounts(allAccounts)
      }
    }
    checkWallets()
  }, [])

  useEffect(() => {
    localStorage.setItem('last_auth_mode', authMode)
  }, [authMode])

  useEffect(() => {
    if (showAuthSidebar && authMode === 'metamask' && metamaskAccounts.length === 0) {
      handleMetamaskConnect()
    }
  }, [showAuthSidebar])

  const cryptoType = KEY_TYPE_MAP[keyType].crypto

  const derivedKey = useMemo(() => {
    if (authMode !== 'local' || !password || password.length < 1) return null
    try {
      const key = new Key(password, cryptoType)
      return { address: key.address, publicKey: key.public_key }
    } catch {
      return null
    }
  }, [password, authMode, cryptoType])

  const handleLocalSignIn = async () => {
    if (!password) { setError('Password is required'); return }
    setLoading(true); setError('')
    try {
      localStorage.setItem('wallet_mode', 'local')
      localStorage.setItem('wallet_password', password)
      localStorage.setItem('wallet_type', cryptoType)
      localStorage.setItem('wallet_key_type', keyType)
      const key = new Key(password, cryptoType)
      localStorage.setItem('wallet_address', key.address)
      await signIn()
      setShowAuthSidebar(false)
      setPassword('')
    } catch (err: any) {
      setError(err.message || 'Failed to sign in')
    } finally { setLoading(false) }
  }

  const handleSubwalletSignIn = async () => {
    if (!selectedAccount) { setError('Please select an account'); return }
    setLoading(true); setError('')
    try {
      await cryptoWaitReady()
      const account = accounts.find(acc => acc.address === selectedAccount)
      if (!account) throw new Error('Account not found')
      const { web3Enable: enable } = await import('@polkadot/extension-dapp')
      const extensions = await enable('MOD')
      if (extensions.length === 0) throw new Error('No extension found')
      localStorage.setItem('wallet_mode', 'subwallet')
      localStorage.setItem('wallet_address', selectedAccount)
      localStorage.setItem('wallet_type', cryptoType)
      localStorage.setItem('wallet_key_type', keyType)
      await signIn()
      setShowAuthSidebar(false)
    } catch (err: any) {
      setError(err.message || 'Failed to connect wallet')
    } finally { setLoading(false) }
  }

  const handleMetamaskConnect = async () => {
    setLoading(true); setError('')
    try {
      if (typeof window.ethereum === 'undefined') throw new Error('MetaMask is not installed')
      const accts = await window.ethereum.request({ method: 'eth_requestAccounts' })
      if (!accts || accts.length === 0) throw new Error('No MetaMask accounts found')
      setMetamaskAccounts(accts)
      if (accts.length === 1) setSelectedMetamaskAccount(accts[0])
    } catch (err: any) {
      setError(err.message || 'Failed to connect MetaMask')
    } finally { setLoading(false) }
  }

  const handleMetamaskSignIn = async () => {
    if (!selectedMetamaskAccount) { setError('Please select a MetaMask account'); return }
    setLoading(true); setError('')
    try {
      localStorage.setItem('wallet_mode', 'metamask')
      localStorage.setItem('wallet_address', selectedMetamaskAccount)
      localStorage.setItem('wallet_type', 'ecdsa')
      localStorage.setItem('wallet_key_type', 'evm')
      await signIn()
      setShowAuthSidebar(false)
    } catch (err: any) {
      setError(err.message || 'Failed to sign in with MetaMask')
    } finally { setLoading(false) }
  }

  const handlePhantomSignIn = async () => {
    setLoading(true); setError('')
    try {
      if (typeof window.solana === 'undefined' || !window.solana.isPhantom) throw new Error('Phantom wallet is not installed')
      const response = await window.solana.connect()
      if (!response.publicKey) throw new Error('Failed to connect to Phantom')
      localStorage.setItem('wallet_mode', 'phantom')
      localStorage.setItem('wallet_address', response.publicKey.toString())
      localStorage.setItem('wallet_type', cryptoType)
      localStorage.setItem('wallet_key_type', keyType)
      await signIn()
      setShowAuthSidebar(false)
    } catch (err: any) {
      setError(err.message || 'Failed to connect Phantom')
    } finally { setLoading(false) }
  }

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (authMode === 'local') handleLocalSignIn()
    else if (authMode === 'subwallet') handleSubwalletSignIn()
    else if (authMode === 'metamask') {
      if (metamaskAccounts.length === 0) handleMetamaskConnect()
      else handleMetamaskSignIn()
    } else if (authMode === 'phantom') handlePhantomSignIn()
  }

  const handleClose = () => {
    setShowAuthSidebar(false)
    setPassword('')
    setError('')
    setMetamaskAccounts([])
    setSelectedMetamaskAccount('')
    setSelectedAccount('')
  }

  if (authLoading) {
    return (
      <div className="flex items-center justify-center px-4 font-digital uppercase" style={{ backgroundColor: 'var(--bg-input)', border: '1px solid var(--border-color)', borderRadius: '8px', color: 'var(--text-primary)', height: '36px' }}>
        <div className="flex items-center gap-2">
          <div className="w-3.5 h-3.5 border-2 border-t-[#78a9ff] animate-spin rounded-full" style={{ borderColor: 'var(--border-input)', borderTopColor: '#78a9ff' }} />
          <span className="text-xs font-bold tracking-wide" style={{ color: 'var(--text-tertiary)' }}>LOADING</span>
        </div>
      </div>
    )
  }

  if (user) return null

  const walletColors: Record<AuthMode, string> = {
    local: '#78a9ff',
    subwallet: '#00e5cc',
    metamask: '#f6851b',
    phantom: '#ab9ff2',
  }

  const wallets: { mode: AuthMode; Logo: React.FC<{ size?: number }>; disabled: boolean; onClick: () => void }[] = [
    { mode: 'metamask', Logo: MetamaskLogo, disabled: false, onClick: () => { setAuthMode('metamask'); setKeyType(WALLET_KEY_TYPES.metamask[0]); setError(''); if (metamaskAccounts.length === 0) handleMetamaskConnect(); } },
    { mode: 'phantom', Logo: PhantomLogo, disabled: false, onClick: () => { setAuthMode('phantom'); setKeyType(WALLET_KEY_TYPES.phantom[0]); setError(''); } },
    { mode: 'subwallet', Logo: SubwalletLogo, disabled: accounts.length === 0, onClick: () => { setAuthMode('subwallet'); setKeyType(WALLET_KEY_TYPES.subwallet[0]); setError(''); } },
    { mode: 'local', Logo: LocalKeyLogo, disabled: false, onClick: () => { setAuthMode('local'); setKeyType(WALLET_KEY_TYPES.local[0]); setError(''); } },
  ]

  const activeColor = walletColors[authMode]

  return (
    <div className="relative font-mono">
      {/* Trigger Button */}
      <button
        onClick={() => setShowAuthSidebar(!showAuthSidebar)}
        className="flex items-center px-4 transition-all duration-200 gap-2 uppercase"
        style={{
          height: '36px',
          backgroundColor: showAuthSidebar ? 'var(--text-primary)' : 'var(--bg-input)',
          border: `1px solid ${showAuthSidebar ? 'var(--text-primary)' : 'var(--border-color)'}`,
          borderRadius: '8px',
          color: showAuthSidebar ? 'var(--bg-primary)' : 'var(--text-primary)',
          fontFamily: 'var(--font-digital), monospace',
        }}
      >
        <svg className="w-4 h-4" viewBox="0 0 16 16" fill="currentColor"><path d="M8 1a2 2 0 0 1 2 2v3H6V3a2 2 0 0 1 2-2zm3 5V3a3 3 0 0 0-6 0v3H2v7a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2V6H11zm-1 1v6H4V7h8z"/></svg>
        <span className="text-xs font-digital tracking-wide font-bold">SIGN IN</span>
      </button>

      {/* Sidebar Auth Panel */}
      <AnimatePresence>
        {showAuthSidebar && (
          <>
            {/* Backdrop */}
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.2 }}
              className="fixed inset-0 bg-black/60 backdrop-blur-sm z-[80]"
              onClick={handleClose}
            />

            {/* Side Panel */}
            <motion.div
              initial={{ x: 400, opacity: 0 }}
              animate={{ x: 0, opacity: 1 }}
              exit={{ x: 400, opacity: 0 }}
              transition={{ duration: 0.3, ease: [0.4, 0, 0.2, 1] }}
              className="fixed top-0 right-0 h-screen shadow-2xl z-[90] overflow-y-auto custom-scrollbar font-mono"
              style={{
                width: '380px',
                borderLeft: `1px solid ${activeColor}20`,
                boxShadow: `-20px 0 60px rgba(0, 0, 0, 0.4), 0 0 80px ${activeColor}08`,
                backgroundColor: 'var(--bg-secondary)',
              }}
            >
              {/* Header */}
              <div className="sticky top-0 z-10 backdrop-blur-md" style={{ backgroundColor: 'var(--bg-sidebar)' }}>
                <div className="px-5 pt-4 pb-3 flex items-center justify-between">
                  <span className="text-lg font-digital uppercase tracking-[0.2em] font-bold" style={{ color: 'var(--text-primary)' }}>
                    CONNECT
                  </span>
                  <button
                    onClick={handleClose}
                    className="flex items-center justify-center w-8 h-8 rounded-lg hover:bg-white/10 transition-all"
                    style={{ backgroundColor: 'var(--bg-input)', border: '1px solid var(--border-color)' }}
                  >
                    <XMarkIcon className="w-4 h-4" style={{ color: 'var(--text-tertiary)' }} />
                  </button>
                </div>

                {/* Provider Selection - Icon row */}
                <div className="px-5 pb-4">
                  <div className="flex gap-1.5">
                    {wallets.map(({ mode, Logo, disabled, onClick }) => {
                      const isSelected = authMode === mode
                      const color = walletColors[mode]
                      return (
                        <button
                          key={mode}
                          onClick={onClick}
                          disabled={disabled}
                          className={`relative flex-1 flex items-center justify-center py-3 transition-all duration-200 rounded-lg ${disabled ? 'opacity-20 cursor-not-allowed' : 'cursor-pointer hover:opacity-80'}`}
                          style={{
                            backgroundColor: isSelected ? `${color}12` : 'transparent',
                            border: isSelected ? `1px solid ${color}60` : '1px solid var(--border-color)',
                          }}
                        >
                          {isSelected && (
                            <motion.div
                              className="absolute bottom-0 left-2 right-2 h-0.5 rounded-full"
                              style={{ backgroundColor: color }}
                              layoutId="auth-indicator"
                              transition={{ type: 'spring', stiffness: 400, damping: 30 }}
                            />
                          )}
                          <div className={`transition-opacity ${isSelected ? 'opacity-100' : 'opacity-30'}`}>
                            <Logo size={26} />
                          </div>
                        </button>
                      )
                    })}
                  </div>
                </div>

                <div className="mx-5" style={{ borderBottom: '1px solid var(--border-color)', opacity: 0.5 }} />
              </div>

              {/* Form Content */}
              <div className="px-5 py-4">
                <form onSubmit={handleSubmit} className="space-y-4">
                  {/* Key Type Selector - shown for all wallets */}
                  {(() => {
                    const types = WALLET_KEY_TYPES[authMode]
                    return (
                      <div>
                        <label className="block text-[10px] font-digital uppercase tracking-[0.3em] mb-2 font-bold" style={{ color: 'var(--text-tertiary)' }}>
                          KEY TYPE
                        </label>
                        <div className="flex gap-1.5">
                          {types.map((kt) => {
                            const info = KEY_TYPE_MAP[kt]
                            const isActive = keyType === kt
                            const isSingle = types.length === 1
                            return (
                              <button
                                key={kt}
                                type="button"
                                onClick={() => !isSingle && setKeyType(kt)}
                                className={`flex-1 flex items-center justify-center gap-2 py-2 transition-all duration-150 ${isSingle ? 'cursor-default' : 'cursor-pointer hover:opacity-80'}`}
                                style={{
                                  backgroundColor: isActive ? `${info.color}12` : 'transparent',
                                  border: isActive ? `1px solid ${info.color}60` : '1px solid var(--border-color)',
                                  borderRadius: '6px',
                                }}
                              >
                                <span style={{
                                  width: '5px',
                                  height: '5px',
                                  borderRadius: '50%',
                                  backgroundColor: isActive ? info.color : 'var(--border-color)',
                                  boxShadow: isActive ? `0 0 6px ${info.color}80` : 'none',
                                  display: 'inline-block',
                                  transition: 'all 0.15s',
                                }} />
                                <span
                                  className="text-[11px] font-digital font-bold uppercase tracking-widest"
                                  style={{ color: isActive ? info.color : 'var(--text-tertiary)', transition: 'color 0.15s' }}
                                >
                                  {info.label}
                                </span>
                              </button>
                            )
                          })}
                        </div>
                      </div>
                    )
                  })()}

                  <AnimatePresence mode="wait">
                    {authMode === 'local' ? (
                      <motion.div
                        key="local"
                        initial={{ opacity: 0, y: 6 }}
                        animate={{ opacity: 1, y: 0 }}
                        exit={{ opacity: 0, y: -6 }}
                        transition={{ duration: 0.12 }}
                        className="space-y-4"
                      >
                        <div>
                          <label className="block text-[10px] font-digital uppercase tracking-[0.3em] mb-2 font-bold" style={{ color: 'var(--text-tertiary)' }}>
                            PASSPHRASE
                          </label>
                          <div className="relative group">
                            <div className="absolute left-3.5 top-1/2 -translate-y-1/2 group-focus-within:text-[#78a9ff] transition-colors" style={{ color: 'var(--text-tertiary)' }}>
                              <svg className="w-3.5 h-3.5" viewBox="0 0 16 16" fill="currentColor"><path d="M8 1a2 2 0 0 1 2 2v3H6V3a2 2 0 0 1 2-2zm3 5V3a3 3 0 0 0-6 0v3H2v7a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2V6H11zm-1 1v6H4V7h8z"/></svg>
                            </div>
                            <input
                              type="password"
                              value={password}
                              onChange={(e) => setPassword(e.target.value)}
                              className="w-full h-11 pl-10 pr-4 text-sm font-digital focus:outline-none transition-all duration-200 font-bold uppercase tracking-wide"
                              style={{
                                backgroundColor: 'var(--bg-input)',
                                border: `1px solid ${password ? `${activeColor}60` : 'var(--border-color)'}`,
                                borderRadius: '6px',
                                color: 'var(--text-primary)',
                              }}
                              placeholder="ENTER PASSPHRASE"
                              autoFocus
                            />
                          </div>
                          <p className="text-[9px] mt-1.5 flex items-center gap-1 font-digital uppercase tracking-wider" style={{ color: 'var(--text-tertiary)' }}>
                            <svg className="w-2.5 h-2.5 shrink-0" fill="currentColor" viewBox="0 0 16 16"><path d="M8 1a7 7 0 110 14A7 7 0 018 1zm0 1a6 6 0 100 12A6 6 0 008 2zm-.5 3h1v5h-1V5zm0 6h1v1h-1v-1z"/></svg>
                            KEY DERIVED FROM PASSPHRASE
                          </p>
                        </div>

                        {/* Key Preview */}
                        <AnimatePresence>
                          {derivedKey && (
                            <motion.div
                              initial={{ opacity: 0, height: 0 }}
                              animate={{ opacity: 1, height: 'auto' }}
                              exit={{ opacity: 0, height: 0 }}
                              transition={{ duration: 0.15 }}
                            >
                              <div className="px-3 py-2.5" style={{ backgroundColor: 'var(--bg-input)', border: `1px solid ${activeColor}40`, borderRadius: '6px' }}>
                                <div className="flex items-center gap-1.5 mb-1.5">
                                  <div className="w-1.5 h-1.5 rounded-full bg-[#42be65]" />
                                  <span className="text-[9px] font-digital uppercase tracking-widest font-bold" style={{ color: 'var(--text-tertiary)' }}>DERIVED KEY</span>
                                </div>
                                <p className="text-xs font-mono break-all leading-relaxed" style={{ color: 'var(--text-primary)' }}>
                                  {derivedKey.address.slice(0, 20)}<span style={{ color: 'var(--text-tertiary)' }}>...</span>{derivedKey.address.slice(-12)}
                                </p>
                              </div>
                            </motion.div>
                          )}
                        </AnimatePresence>
                      </motion.div>
                    ) : authMode === 'subwallet' ? (
                      <motion.div
                        key="subwallet"
                        initial={{ opacity: 0, y: 6 }}
                        animate={{ opacity: 1, y: 0 }}
                        exit={{ opacity: 0, y: -6 }}
                        transition={{ duration: 0.12 }}
                        className="space-y-3"
                      >
                        <label className="block text-[10px] font-digital uppercase tracking-[0.3em] mb-2 font-bold" style={{ color: 'var(--text-tertiary)' }}>
                          ACCOUNT
                        </label>
                        {accounts.length === 0 ? (
                          <div className="flex items-center gap-3 p-3" style={{ backgroundColor: 'var(--bg-input)', border: '1px solid var(--border-color)', borderRadius: '6px' }}>
                            <svg className="w-3.5 h-3.5 text-[#f1c21b] shrink-0" viewBox="0 0 16 16" fill="currentColor"><path d="M8 1L0 15h16L8 1zm0 3.2L13.4 14H2.6L8 4.2zM7.25 7v3.5h1.5V7h-1.5zm0 4.5v1.5h1.5v-1.5h-1.5z"/></svg>
                            <span className="text-xs text-[#f1c21b] font-digital uppercase font-bold">NO EXTENSION FOUND</span>
                          </div>
                        ) : (
                          <div className="overflow-hidden" style={{ border: '1px solid var(--border-color)', borderRadius: '6px' }}>
                            {accounts.map((account) => {
                              const selected = selectedAccount === account.address
                              return (
                                <button
                                  key={account.address}
                                  type="button"
                                  onClick={() => setSelectedAccount(account.address)}
                                  className="w-full text-left px-3 py-2.5 text-sm font-digital transition-all last:border-b-0"
                                  style={{
                                    backgroundColor: selected ? 'rgba(0,229,204,0.08)' : 'transparent',
                                    borderBottom: '1px solid var(--border-color)',
                                  }}
                                >
                                  <div className="flex items-center gap-3">
                                    <div className="w-2.5 h-2.5 rounded-full shrink-0 transition-all" style={{
                                      backgroundColor: selected ? '#00e5cc' : 'transparent',
                                      border: selected ? '2px solid #00e5cc' : '2px solid var(--border-color)',
                                      boxShadow: selected ? '0 0 6px rgba(0,229,204,0.5)' : 'none',
                                    }} />
                                    <div className="flex-1 min-w-0">
                                      <div className="font-bold text-xs truncate uppercase" style={{ color: selected ? 'var(--text-primary)' : 'var(--text-secondary)' }}>{account.meta.name}</div>
                                      <div className="text-[10px] truncate mt-0.5 font-mono" style={{ color: 'var(--text-tertiary)' }}>
                                        {account.address.slice(0, 14)}...{account.address.slice(-10)}
                                      </div>
                                    </div>
                                  </div>
                                </button>
                              )
                            })}
                          </div>
                        )}
                      </motion.div>
                    ) : authMode === 'metamask' ? (
                      <motion.div
                        key="metamask"
                        initial={{ opacity: 0, y: 6 }}
                        animate={{ opacity: 1, y: 0 }}
                        exit={{ opacity: 0, y: -6 }}
                        transition={{ duration: 0.12 }}
                        className="space-y-3"
                      >
                        {metamaskAccounts.length === 0 ? (
                          <div className="flex items-center gap-3 p-3" style={{ backgroundColor: 'var(--bg-input)', border: '1px solid var(--border-color)', borderRadius: '6px' }}>
                            <MetamaskLogo size={28} />
                            <div>
                              <p className="text-xs text-[#f6851b] font-bold font-digital uppercase">METAMASK</p>
                              <p className="text-[10px] mt-0.5 font-digital uppercase" style={{ color: 'var(--text-tertiary)' }}>CLICK SIGN IN TO CONNECT</p>
                            </div>
                          </div>
                        ) : (
                          <div>
                            <label className="block text-[10px] font-digital uppercase tracking-[0.3em] mb-2 font-bold" style={{ color: 'var(--text-tertiary)' }}>
                              ACCOUNT
                            </label>
                            <div className="overflow-hidden" style={{ border: '1px solid var(--border-color)', borderRadius: '6px' }}>
                              {metamaskAccounts.map((addr, index) => {
                                const selected = selectedMetamaskAccount === addr
                                return (
                                  <button
                                    key={addr}
                                    type="button"
                                    onClick={() => setSelectedMetamaskAccount(addr)}
                                    className="w-full text-left px-3 py-2.5 text-sm font-digital transition-all last:border-b-0"
                                    style={{
                                      backgroundColor: selected ? 'rgba(246,133,27,0.08)' : 'transparent',
                                      borderBottom: '1px solid var(--border-color)',
                                    }}
                                  >
                                    <div className="flex items-center gap-3">
                                      <div className="w-2.5 h-2.5 rounded-full shrink-0 transition-all" style={{
                                        backgroundColor: selected ? '#f6851b' : 'transparent',
                                        border: selected ? '2px solid #f6851b' : '2px solid var(--border-color)',
                                        boxShadow: selected ? '0 0 6px rgba(246,133,27,0.5)' : 'none',
                                      }} />
                                      <div className="flex-1 min-w-0">
                                        <div className="font-bold text-xs uppercase" style={{ color: selected ? 'var(--text-primary)' : 'var(--text-secondary)' }}>ACCOUNT {index + 1}</div>
                                        <div className="text-[10px] truncate mt-0.5 font-mono" style={{ color: 'var(--text-tertiary)' }}>
                                          {addr.slice(0, 14)}...{addr.slice(-10)}
                                        </div>
                                      </div>
                                    </div>
                                  </button>
                                )
                              })}
                            </div>
                          </div>
                        )}
                      </motion.div>
                    ) : (
                      <motion.div
                        key="phantom"
                        initial={{ opacity: 0, y: 6 }}
                        animate={{ opacity: 1, y: 0 }}
                        exit={{ opacity: 0, y: -6 }}
                        transition={{ duration: 0.12 }}
                      >
                        <div className="flex items-center gap-3 p-3" style={{ backgroundColor: 'var(--bg-input)', border: '1px solid var(--border-color)', borderRadius: '6px' }}>
                          <PhantomLogo size={28} />
                          <div>
                            <p className="text-xs text-[#ab9ff2] font-bold font-digital uppercase">PHANTOM</p>
                            <p className="text-[10px] mt-0.5 font-digital uppercase" style={{ color: 'var(--text-tertiary)' }}>CLICK SIGN IN TO CONNECT</p>
                          </div>
                        </div>
                      </motion.div>
                    )}
                  </AnimatePresence>

                  {/* Error */}
                  <AnimatePresence>
                    {error && (
                      <motion.div
                        initial={{ opacity: 0, height: 0 }}
                        animate={{ opacity: 1, height: 'auto' }}
                        exit={{ opacity: 0, height: 0 }}
                        className="flex items-center gap-2 px-3 py-2"
                        style={{ backgroundColor: 'rgba(218,30,40,0.08)', border: '1px solid rgba(218,30,40,0.3)', borderRadius: '6px' }}
                      >
                        <svg className="w-3 h-3 text-[#fa4d56] shrink-0" viewBox="0 0 16 16" fill="currentColor"><path d="M8 1a7 7 0 1 0 0 14A7 7 0 0 0 8 1zm-.75 3.5h1.5v5h-1.5v-5zm0 6h1.5v1.5h-1.5v-1.5z"/></svg>
                        <span className="text-[10px] text-[#fa4d56] font-digital uppercase font-bold tracking-wide">{error}</span>
                      </motion.div>
                    )}
                  </AnimatePresence>

                  {/* Sign In Button */}
                  <button
                    type="submit"
                    disabled={loading || (authMode === 'subwallet' && accounts.length === 0)}
                    className="w-full h-10 text-xs font-bold uppercase tracking-[0.2em] transition-all duration-200 disabled:opacity-30 disabled:cursor-not-allowed relative overflow-hidden group font-digital"
                    style={{
                      backgroundColor: activeColor,
                      opacity: loading || (authMode === 'subwallet' && accounts.length === 0) ? 0.3 : 1,
                      color: authMode === 'phantom' || authMode === 'subwallet' ? '#0d0d0d' : '#fff',
                      borderRadius: '6px',
                    }}
                  >
                    <div className="absolute inset-0 bg-white/0 group-hover:bg-white/10 transition-all duration-200" />
                    {loading ? (
                      <span className="relative flex items-center justify-center gap-2">
                        <div className="w-3.5 h-3.5 border-2 border-current/20 border-t-current animate-spin rounded-full" />
                        CONNECTING...
                      </span>
                    ) : authMode === 'metamask' && metamaskAccounts.length === 0 ? (
                      <span className="relative">CONNECT WALLET</span>
                    ) : (
                      <span className="relative">SIGN IN</span>
                    )}
                  </button>
                </form>

                {/* Footer */}
                <div className="mt-5 pt-3 flex items-center gap-2" style={{ borderTop: '1px solid var(--border-color)', opacity: 0.6 }}>
                  <div className="w-1 h-1 rounded-full animate-pulse" style={{ backgroundColor: activeColor }} />
                  <span className="text-[9px] font-digital tracking-[0.2em] uppercase font-bold" style={{ color: 'var(--text-tertiary)' }}>MOD AUTH</span>
                  <div className="flex-1" />
                  <div className="flex items-center gap-1">
                    <div className="w-1 h-1 rounded-full bg-[#42be65]" />
                    <span className="text-[9px] font-digital tracking-[0.2em] uppercase font-bold" style={{ color: 'var(--text-tertiary)' }}>ENCRYPTED</span>
                  </div>
                </div>
              </div>
            </motion.div>
          </>
        )}
      </AnimatePresence>
    </div>
  )
}

export default WalletAuthButton
