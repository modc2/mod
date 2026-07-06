"use client";

import { useState, useEffect, useCallback, useRef } from 'react'
import { ethers } from 'ethers'
import { ArrowPathIcon, ArrowRightStartOnRectangleIcon, XMarkIcon, CheckIcon } from '@heroicons/react/24/outline'
import { motion, AnimatePresence } from 'framer-motion'
import { toast } from 'react-toastify'
import DebitABI from '@/contracts/market/debit/Debit.sol/Debit.json'
import { getChainConfig } from '@/network/chainConfig'
import { Auth } from '@/client/auth'

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
      <rect width="128" height="128" rx="24" fill="url(#pg2)"/>
      <path d="M96 64c0 17.673-14.327 32-32 32s-32-14.327-32-32V32h64v32z" fill="white"/>
      <circle cx="52" cy="58" r="6" fill="#AB9FF2"/><circle cx="76" cy="58" r="6" fill="#AB9FF2"/>
      <defs><linearGradient id="pg2" x1="0" y1="0" x2="128" y2="128"><stop stopColor="#AB9FF2"/><stop offset="1" stopColor="#7C66DC"/></linearGradient></defs>
    </svg>
  )
  if (m.includes('subwallet')) return (
    <svg width={size} height={size} viewBox="0 0 200 200" fill="none">
      <circle cx="100" cy="100" r="100" fill="url(#sg2)"/>
      <path d="M100 40L60 80L100 120L140 80L100 40Z" fill="white"/>
      <path d="M100 120L60 160L100 200L140 160L100 120Z" fill="white" opacity="0.6"/>
      <defs><linearGradient id="sg2" x1="0" y1="0" x2="200" y2="200"><stop stopColor="#00E5CC"/><stop offset="1" stopColor="#00B8D4"/></linearGradient></defs>
    </svg>
  )
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
      <circle cx="7" cy="7" r="4" fill="none"/><path d="M7 11V22M11 15H7M11 19H7"/>
    </svg>
  )
}

function getDebitAddress(): string {
  const chainConfig = getChainConfig()
  return chainConfig?.contracts?.Debit?.address || ''
}

interface SidebarHeaderProps {
  shortAddress: string
  walletMode: string
  copiedAddress: boolean
  copyAddress: () => void
  tokenExpiry: string | null
  getTokenExpiry: () => string
  isTokenExpired: boolean
  isRefreshing: boolean
  handleRefreshToken: () => void
  handleSignOut: () => void
  onClose: () => void
  marketCredit: number
  address: string
  walletHistory: { address: string; mode: string; type: string; lastUsed: number }[]
  isSwitchingWallet: boolean
  onSwitchWallet: (wallet: { address: string; mode: string; type: string }) => void
  onRemoveFromHistory: (addr: string) => void
}

export function SidebarHeader({
  shortAddress, walletMode, copiedAddress, copyAddress,
  tokenExpiry, getTokenExpiry, isTokenExpired, isRefreshing,
  handleRefreshToken, handleSignOut, onClose, marketCredit, address,
  walletHistory, isSwitchingWallet, onSwitchWallet, onRemoveFromHistory,
}: SidebarHeaderProps) {
  const [dailyLimit, setDailyLimit] = useState<number | null>(null)
  const [dailySpent, setDailySpent] = useState<number | null>(null)
  const [dailyRemaining, setDailyRemaining] = useState<number | null>(null)
  const [isEditing, setIsEditing] = useState(false)
  const [newLimit, setNewLimit] = useState('')
  const [isSaving, setIsSaving] = useState(false)

  const fetchDailyLimits = useCallback(async () => {
    if (!address || typeof window === 'undefined' || !window.ethereum) return
    try {
      const debitAddr = getDebitAddress()
      if (!debitAddr) return
      const provider = new ethers.BrowserProvider(window.ethereum)
      const debit = new ethers.Contract(debitAddr, DebitABI.abi, provider)
      const [limit, spent, remaining] = await Promise.all([
        debit.getEffectiveDailyLimit(address),
        debit.getDailySpent(address),
        debit.getDailyRemaining(address),
      ])
      setDailyLimit(parseFloat(ethers.formatUnits(limit, 8)))
      setDailySpent(parseFloat(ethers.formatUnits(spent, 8)))
      setDailyRemaining(parseFloat(ethers.formatUnits(remaining, 8)))
    } catch (err) {
      console.error('Failed to fetch daily limits:', err)
    }
  }, [address])

  useEffect(() => { fetchDailyLimits() }, [fetchDailyLimits])

  const handleSaveLimit = async () => {
    const val = parseFloat(newLimit)
    if (!val || val <= 0) { toast.error('Enter a valid limit'); return }
    setIsSaving(true)
    try {
      const debitAddr = getDebitAddress()
      if (!debitAddr || !window.ethereum) throw new Error('No Debit contract or wallet')
      const provider = new ethers.BrowserProvider(window.ethereum)
      const signer = await provider.getSigner()
      const debit = new ethers.Contract(debitAddr, DebitABI.abi, signer)
      const limitWei = ethers.parseUnits(val.toString(), 8)
      const tx = await debit.setDailyLimit(limitWei)
      toast.success(`TX sent: ${tx.hash.slice(0, 12)}...`)
      await tx.wait()
      toast.success('Daily limit updated')
      setIsEditing(false)
      setNewLimit('')
      fetchDailyLimits()
    } catch (err: any) {
      console.error(err)
      toast.error(err?.reason || err?.message || 'Failed to update limit')
    } finally {
      setIsSaving(false)
    }
  }

  const [showKeyTypeDropdown, setShowKeyTypeDropdown] = useState(false)
  const keyTypeDropdownRef = useRef<HTMLDivElement>(null)
  const [showWalletDropdown, setShowWalletDropdown] = useState(false)
  const walletDropdownRef = useRef<HTMLDivElement>(null)
  const [showTokenCustomization, setShowTokenCustomization] = useState(false)
  const [tokenTab, setTokenTab] = useState<'token' | 'verify'>('token')
  const [showDecoded, setShowDecoded] = useState(false)
  const [verifyResult, setVerifyResult] = useState<{ valid: boolean; message: string } | null>(null)
  const [isVerifying, setIsVerifying] = useState(false)
  const [copiedToken, setCopiedToken] = useState(false)

  const [walletType, setWalletType] = useState(() =>
    typeof window !== 'undefined' ? localStorage.getItem('wallet_type') || 'ecdsa' : 'ecdsa'
  )

  const KEY_TYPE_CYCLE: { type: string; mode: string; label: string }[] = [
    { type: 'ethereum', mode: 'metamask', label: 'ETH' },
    { type: 'solana', mode: 'phantom', label: 'SOL' },
    { type: 'ecdsa', mode: 'local', label: 'ECDSA' },
    { type: 'sr25519', mode: 'subwallet', label: 'SR25' },
  ]

  const [localWalletMode, setLocalWalletMode] = useState(walletMode)
  useEffect(() => { setLocalWalletMode(walletMode) }, [walletMode])

  const selectKeyType = (keyType: typeof KEY_TYPE_CYCLE[number]) => {
    localStorage.setItem('wallet_type', keyType.type)
    localStorage.setItem('wallet_mode', keyType.mode)
    setWalletType(keyType.type)
    setLocalWalletMode(keyType.mode)
    setShowKeyTypeDropdown(false)
    toast.success(`Switched to ${keyType.label}`)
  }

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (keyTypeDropdownRef.current && !keyTypeDropdownRef.current.contains(e.target as Node)) {
        setShowKeyTypeDropdown(false)
      }
      if (walletDropdownRef.current && !walletDropdownRef.current.contains(e.target as Node)) {
        setShowWalletDropdown(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  const getKeyTypeColor = (type: string) => {
    if (type === 'sr25519') return '#06b6d4'
    if (type === 'ecdsa') return '#f59e0b'
    if (type === 'ethereum') return '#627eea'
    if (type === 'solana') return '#9945ff'
    return '#10b981'
  }

  const spentRatio = dailyLimit && dailyLimit > 0 ? (dailySpent ?? 0) / dailyLimit : 0

  const getStoredToken = () => {
    if (typeof window !== 'undefined') {
      return localStorage.getItem('wallet_token') || ''
    }
    return ''
  }

  const copyToken = () => {
    const token = getStoredToken()
    if (token) {
      navigator.clipboard.writeText(token)
      setCopiedToken(true)
      setTimeout(() => setCopiedToken(false), 2000)
      toast.success('Token copied!')
    } else {
      toast.error('No token available')
    }
  }

  const decodeToken = () => {
    try {
      const token = getStoredToken()
      if (!token) return null
      const parts = token.split('.')
      if (parts.length === 3) {
        try {
          const payload = JSON.parse(atob(parts[1]))
          return { format: 'jwt', payload }
        } catch {}
      }
      try {
        const auth = new Auth()
        const authData = auth.token2data(token)
        return { format: 'auth', payload: authData }
      } catch {}
      return null
    } catch (err) {
      console.error('Failed to decode token:', err)
      return null
    }
  }

  const verifyToken = async () => {
    setIsVerifying(true)
    setVerifyResult(null)
    try {
      const token = getStoredToken()
      if (!token) {
        setVerifyResult({ valid: false, message: 'No token found' })
        return
      }
      const auth = new Auth()
      const authData = auth.token2data(token)
      const tokenTime = parseFloat(authData.time)
      const now = Date.now() / 1000
      const age = now - tokenTime
      if (age > 3600) {
        setVerifyResult({ valid: false, message: `Token expired (${Math.floor(age / 60)}m old)` })
        return
      }
      const valid = auth.verify({ token })
      if (valid) {
        setVerifyResult({ valid: true, message: `Valid signature from ${authData.key.slice(0, 8)}...${authData.key.slice(-4)}` })
      } else {
        setVerifyResult({ valid: false, message: 'Invalid signature' })
      }
    } catch (err: any) {
      setVerifyResult({ valid: false, message: err?.message || 'Verification failed' })
    } finally {
      setIsVerifying(false)
    }
  }

  const keyColor = getKeyTypeColor(walletType)
  const keyLabel = KEY_TYPE_CYCLE.find(k => k.type === walletType)?.label || walletType.toUpperCase()

  return (
    <>
      <motion.div
        className="sticky top-0 z-10 backdrop-blur-md"
        style={{ backgroundColor: 'var(--bg-sidebar)' }}
        initial={{ opacity: 0, y: -8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.08, duration: 0.3, ease: [0.25, 0.1, 0.25, 1] }}
      >
        <div className="px-3 pt-3 pb-2">
          {/* Unified rounded bar */}
          <div
            className="flex items-center"
            style={{
              height: '40px',
              fontFamily: 'var(--font-digital), monospace',
              backgroundColor: 'var(--bg-input)',
              border: `1px solid ${isTokenExpired ? 'rgba(234, 179, 8, 0.3)' : 'var(--border-color)'}`,
              borderRadius: '999px',
              overflow: 'hidden',
              position: 'relative',
              boxShadow: isTokenExpired ? '0 0 12px rgba(234,179,8,0.1)' : '0 2px 8px rgba(0,0,0,0.15)',
            }}
          >
            {/* Key type badge + Address + wallet switcher */}
            <div className="relative flex-shrink-0" ref={walletDropdownRef}>
              <div className="flex items-center h-full">
                {/* Crypto type badge */}
                <div
                  className="flex items-center gap-1.5 pl-3.5 pr-2 h-[40px]"
                  style={{ color: keyColor }}
                >
                  <div className="flex-shrink-0" style={{ filter: `drop-shadow(0 0 4px ${keyColor}60)` }}>
                    <WalletModeLogo mode={KEY_TYPE_CYCLE.find(k => k.type === walletType)?.mode || localWalletMode} size={14} />
                  </div>
                  <span className="text-[10px] font-bold tracking-wider">{keyLabel}</span>
                </div>

                {/* Divider */}
                <div className="h-5 w-px flex-shrink-0" style={{ background: `${keyColor}30` }} />

                {/* Address + copy */}
                <button
                  onClick={copyAddress}
                  className="flex items-center gap-1.5 px-2.5 h-[40px] transition-all hover:brightness-125"
                  title="Copy address"
                >
                  <span
                    className="tabular-nums font-bold uppercase"
                    style={{ fontSize: '13px', color: copiedAddress ? '#4ade80' : 'var(--text-tertiary)', fontFamily: 'var(--font-digital), monospace' }}
                  >
                    {copiedAddress ? 'COPIED' : shortAddress}
                  </span>
                  <svg className="w-3 h-3" style={{ color: copiedAddress ? '#4ade80' : 'var(--text-tertiary)', opacity: copiedAddress ? 1 : 0.35 }} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    {copiedAddress ? (
                      <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                    ) : (
                      <path strokeLinecap="round" strokeLinejoin="round" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
                    )}
                  </svg>
                </button>

                {/* Wallet switcher chevron */}
                <button
                  onClick={() => setShowWalletDropdown(!showWalletDropdown)}
                  className="flex items-center justify-center w-[24px] h-[40px] transition-all hover:brightness-125"
                  style={{ color: 'var(--text-tertiary)', opacity: showWalletDropdown ? 0.8 : 0.35 }}
                  title="Switch wallet"
                >
                  <svg className={`w-3 h-3 transition-transform ${showWalletDropdown ? 'rotate-180' : ''}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
                  </svg>
                </button>
              </div>
            </div>

            {/* Wallet switcher dropdown — rendered outside overflow:hidden via portal-like positioning */}
            {showWalletDropdown && (
              <div
                className="fixed z-[200]"
                style={{
                  top: walletDropdownRef.current ? walletDropdownRef.current.getBoundingClientRect().bottom + 8 : 0,
                  left: walletDropdownRef.current ? walletDropdownRef.current.getBoundingClientRect().left : 0,
                }}
              >
                <motion.div
                  initial={{ opacity: 0, y: -6, scale: 0.95 }}
                  animate={{ opacity: 1, y: 0, scale: 1 }}
                  transition={{ duration: 0.12 }}
                  style={{
                    minWidth: '240px',
                    background: 'var(--bg-secondary)',
                    border: '1px solid var(--border-color)',
                    borderRadius: '14px',
                    boxShadow: '0 12px 32px rgba(0,0,0,0.4)',
                    backdropFilter: 'blur(12px)',
                    fontFamily: 'var(--font-digital), monospace',
                  }}
                >
                  {/* Current wallet */}
                  <div className="px-3 pt-2.5 pb-1">
                    <div className="text-[9px] font-bold uppercase tracking-widest" style={{ color: 'var(--text-tertiary)', opacity: 0.5 }}>Current</div>
                  </div>
                  <div
                    className="flex items-center gap-2 px-3 py-2 mx-1.5 mb-1 rounded-lg"
                    style={{ backgroundColor: `${keyColor}12` }}
                  >
                    <div className="flex-shrink-0" style={{ color: keyColor, filter: `drop-shadow(0 0 4px ${keyColor}60)` }}>
                      <WalletModeLogo mode={KEY_TYPE_CYCLE.find(k => k.type === walletType)?.mode || localWalletMode} size={14} />
                    </div>
                    <span className="text-[10px] font-bold tracking-wider" style={{ color: keyColor }}>{keyLabel}</span>
                    <span className="text-[11px] font-bold tabular-nums" style={{ color: keyColor }}>
                      {shortAddress}
                    </span>
                    <span className="flex-1" />
                    <svg className="w-2.5 h-2.5" fill="none" viewBox="0 0 24 24" stroke={keyColor} strokeWidth={2.5}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                    </svg>
                  </div>

                  {/* Other wallets */}
                  {walletHistory.filter(w => w.address.toLowerCase() !== address.toLowerCase()).length > 0 && (
                    <>
                      <div className="px-3 pt-1.5 pb-1">
                        <div className="text-[9px] font-bold uppercase tracking-widest" style={{ color: 'var(--text-tertiary)', opacity: 0.5 }}>Wallets</div>
                      </div>
                      {walletHistory
                        .filter(w => w.address.toLowerCase() !== address.toLowerCase())
                        .map((wallet) => {
                          const wColor = getKeyTypeColor(wallet.type)
                          const wLabel = KEY_TYPE_CYCLE.find(k => k.type === wallet.type)?.label || wallet.type.toUpperCase()
                          const wShort = `${wallet.address.slice(0, 6)}...${wallet.address.slice(-4)}`
                          return (
                            <button
                              key={wallet.address}
                              onClick={() => { onSwitchWallet(wallet); setShowWalletDropdown(false) }}
                              className="flex items-center gap-2 w-full px-3 py-2 transition-all text-left group"
                              style={{ backgroundColor: 'transparent' }}
                              onMouseEnter={(e) => { e.currentTarget.style.backgroundColor = 'var(--hover-bg)' }}
                              onMouseLeave={(e) => { e.currentTarget.style.backgroundColor = 'transparent' }}
                              disabled={isSwitchingWallet}
                            >
                              <div className="flex-shrink-0" style={{ color: wColor, opacity: 0.6 }}>
                                <WalletModeLogo mode={wallet.mode} size={14} />
                              </div>
                              <span className="text-[10px] font-bold uppercase tracking-wider" style={{ color: wColor }}>
                                {wLabel}
                              </span>
                              <span className="text-[11px] font-bold tabular-nums" style={{ color: 'var(--text-secondary)' }}>
                                {wShort}
                              </span>
                              <span className="flex-1" />
                              <span
                                onClick={(e) => { e.stopPropagation(); onRemoveFromHistory(wallet.address) }}
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

                  {/* No other wallets message */}
                  {walletHistory.filter(w => w.address.toLowerCase() !== address.toLowerCase()).length === 0 && (
                    <div className="px-3 py-2.5 pb-3 text-[10px]" style={{ color: 'var(--text-tertiary)', opacity: 0.5 }}>
                      No other wallets cached
                    </div>
                  )}
                </motion.div>
              </div>
            )}

            {/* Spacer */}
            <div className="flex-1 min-w-0" />

            {/* Right actions */}
            <div className="flex items-center gap-0 flex-shrink-0">
              {/* Divider */}
              <div className="h-5 w-px flex-shrink-0" style={{ background: 'var(--border-color)', opacity: 0.4 }} />

              {/* Token: timer + copy + details — unified */}
              <button
                onClick={handleRefreshToken}
                disabled={isRefreshing}
                className="flex items-center gap-1.5 px-3 h-[40px] font-bold tracking-wide transition-all hover:brightness-125 disabled:opacity-40"
                style={{
                  fontSize: '12px',
                  color: copiedToken ? '#4ade80' : isTokenExpired ? '#eab308' : 'var(--text-tertiary)',
                  fontFamily: 'var(--font-digital), monospace',
                }}
                title="Click to refresh token"
              >
                <ArrowPathIcon className={`w-3.5 h-3.5 ${isRefreshing ? 'animate-spin' : ''}`} style={{
                  opacity: isTokenExpired ? 1 : 0.5,
                  filter: isTokenExpired ? 'drop-shadow(0 0 4px rgba(234,179,8,0.5))' : 'none',
                }} />
                <span className="tabular-nums">{tokenExpiry || getTokenExpiry()}</span>
                <span
                  onClick={(e) => { e.stopPropagation(); copyToken() }}
                  className="flex items-center justify-center transition-all hover:scale-110"
                  style={{ opacity: copiedToken ? 1 : 0.35 }}
                  title="Copy token"
                >
                  <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    {copiedToken ? (
                      <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                    ) : (
                      <path strokeLinecap="round" strokeLinejoin="round" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
                    )}
                  </svg>
                </span>
                <span
                  onClick={(e) => { e.stopPropagation(); setShowTokenCustomization(!showTokenCustomization) }}
                  className="flex items-center justify-center transition-all hover:scale-110"
                  style={{ opacity: showTokenCustomization ? 0.8 : 0.35 }}
                  title="Token details"
                >
                  <svg className={`w-3 h-3 transition-transform ${showTokenCustomization ? 'rotate-180' : ''}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
                  </svg>
                </span>
              </button>

              {/* Divider */}
              <div className="h-5 w-px flex-shrink-0" style={{ background: 'var(--border-color)', opacity: 0.4 }} />

              {/* Sign out */}
              <button
                onClick={handleSignOut}
                className="flex items-center justify-center w-[36px] h-[40px] transition-all hover:brightness-125"
                style={{ color: 'var(--text-tertiary)', opacity: 0.4 }}
                onMouseEnter={(e) => { e.currentTarget.style.color = '#ef4444'; e.currentTarget.style.opacity = '1' }}
                onMouseLeave={(e) => { e.currentTarget.style.color = 'var(--text-tertiary)'; e.currentTarget.style.opacity = '0.4' }}
                title="Sign out"
              >
                <ArrowRightStartOnRectangleIcon className="w-3.5 h-3.5" />
              </button>

              {/* Divider */}
              <div className="h-5 w-px flex-shrink-0" style={{ background: 'var(--border-color)', opacity: 0.4 }} />

              {/* Close */}
              <button
                onClick={onClose}
                className="flex items-center justify-center w-[36px] h-[40px] pr-1 transition-all hover:brightness-150"
                style={{ color: 'var(--text-tertiary)', opacity: 0.4 }}
                title="Close"
              >
                <XMarkIcon className="w-3.5 h-3.5" />
              </button>
            </div>
          </div>

          {/* Daily limit row */}
          {dailyLimit !== null && !isEditing && (
            <div className="flex items-center gap-2 mt-1.5 px-1">
              <div className="flex-1" />
              <button
                onClick={() => { setIsEditing(true); setNewLimit(dailyLimit.toFixed(2)) }}
                className="flex items-center gap-1 px-2 py-0.5 text-[10px] font-bold tracking-wide transition-all hover:opacity-70"
                style={{
                  color: spentRatio > 0.9 ? '#ef4444' : spentRatio > 0.7 ? '#eab308' : '#4ade80',
                  backgroundColor: spentRatio > 0.9 ? 'rgba(239, 68, 68, 0.06)' : spentRatio > 0.7 ? 'rgba(234, 179, 8, 0.06)' : 'rgba(74, 222, 128, 0.06)',
                  borderRadius: '6px',
                  fontFamily: 'var(--font-digital), monospace',
                }}
                title="Click to edit daily limit"
              >
                <span className="tabular-nums">
                  ${dailyRemaining !== null ? dailyRemaining.toFixed(0) : '—'}<span style={{ opacity: 0.35 }}>/{dailyLimit.toFixed(0)}</span>
                </span>
              </button>
            </div>
          )}

          {/* Token details panel */}
          <AnimatePresence>
            {showTokenCustomization && (
              <motion.div
                initial={{ height: 0, opacity: 0 }}
                animate={{ height: 'auto', opacity: 1 }}
                exit={{ height: 0, opacity: 0 }}
                transition={{ duration: 0.2 }}
                className="mt-1.5 overflow-hidden z-50"
                style={{ backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border-color)', borderRadius: '10px' }}
              >
                <div className="flex border-b" style={{ borderColor: 'var(--border-color)' }}>
                  <button
                    onClick={() => setTokenTab('token')}
                    className="flex-1 px-3 py-2 font-digital text-[10px] uppercase font-bold transition-all"
                    style={{
                      backgroundColor: tokenTab === 'token' ? 'var(--bg-secondary)' : 'transparent',
                      borderRight: '1px solid var(--border-color)',
                      color: tokenTab === 'token' ? 'var(--text-primary)' : 'var(--text-tertiary)',
                    }}
                  >
                    TOKEN
                  </button>
                  <button
                    onClick={() => { setTokenTab('verify'); setVerifyResult(null) }}
                    className="flex-1 px-3 py-2 font-digital text-[10px] uppercase font-bold transition-all"
                    style={{
                      backgroundColor: tokenTab === 'verify' ? 'var(--bg-secondary)' : 'transparent',
                      color: tokenTab === 'verify' ? 'var(--text-primary)' : 'var(--text-tertiary)',
                    }}
                  >
                    VERIFY
                  </button>
                </div>

                <div className="p-3">
                  {tokenTab === 'token' && (() => {
                    const decoded = decodeToken()
                    const currentToken = getStoredToken()
                    return (
                      <div>
                        <div
                          className="px-3 py-2 text-xs font-mono break-all cursor-pointer transition-all hover:border-opacity-100"
                          style={{
                            fontFamily: 'IBM Plex Mono, monospace',
                            backgroundColor: 'var(--bg-input)',
                            border: `1px solid ${copiedToken ? '#4ade80' : 'var(--border-color)'}`,
                            borderRadius: '8px',
                            color: copiedToken ? '#4ade80' : 'var(--text-primary)',
                            maxHeight: showDecoded ? 'none' : '100px',
                            overflowY: showDecoded ? 'visible' : 'auto',
                          }}
                          onClick={copyToken}
                          title="Click to copy token"
                        >
                          {copiedToken ? 'COPIED!' : (
                            showDecoded && decoded ? (
                              <pre className="whitespace-pre-wrap text-xs" style={{ fontFamily: 'IBM Plex Mono, monospace' }}>
                                {JSON.stringify(decoded.payload, null, 2)}
                              </pre>
                            ) : (
                              currentToken || 'No token available'
                            )
                          )}
                        </div>

                        <div className="flex items-center gap-2 mt-2">
                          <button
                            onClick={() => setShowDecoded(!showDecoded)}
                            className="flex items-center gap-1.5 px-2 py-1 font-digital text-[10px] uppercase font-bold transition-all hover:opacity-80"
                            style={{
                              backgroundColor: showDecoded ? 'var(--bg-secondary)' : 'var(--bg-primary)',
                              border: `1px solid ${showDecoded ? 'var(--text-primary)' : 'var(--border-color)'}`,
                              borderRadius: '6px',
                              color: showDecoded ? 'var(--text-primary)' : 'var(--text-tertiary)',
                            }}
                          >
                            {showDecoded ? '{ }' : 'RAW'}
                            <span style={{ fontSize: '8px', opacity: 0.7 }}>{showDecoded ? 'JSON' : 'BASE64'}</span>
                          </button>

                          <div className="flex-1" />

                          <button
                            onClick={handleRefreshToken}
                            disabled={isRefreshing}
                            className="flex items-center gap-1.5 px-2 py-1 font-digital text-[10px] uppercase font-bold transition-all hover:opacity-80 disabled:opacity-50"
                            style={{
                              backgroundColor: 'rgba(34, 197, 94, 0.1)',
                              border: '1px solid rgba(34, 197, 94, 0.3)',
                              borderRadius: '6px',
                              color: '#4ade80',
                            }}
                          >
                            <ArrowPathIcon className={`w-3 h-3 ${isRefreshing ? 'animate-spin' : ''}`} />
                            GENERATE
                          </button>
                        </div>
                      </div>
                    )
                  })()}

                  {tokenTab === 'verify' && (() => {
                    const decoded = decodeToken()
                    return (
                      <div>
                        <button
                          onClick={verifyToken}
                          disabled={isVerifying || !getStoredToken()}
                          className="w-full flex items-center justify-center gap-2 px-3 py-2 font-digital text-xs uppercase font-bold tracking-wide transition-all hover:opacity-80 disabled:opacity-50 mb-3"
                          style={{
                            backgroundColor: verifyResult
                              ? verifyResult.valid ? 'rgba(34, 197, 94, 0.12)' : 'rgba(239, 68, 68, 0.12)'
                              : 'var(--bg-secondary)',
                            border: `1px solid ${verifyResult
                              ? verifyResult.valid ? 'rgba(34, 197, 94, 0.3)' : 'rgba(239, 68, 68, 0.3)'
                              : 'var(--border-color)'}`,
                            borderRadius: '8px',
                            color: verifyResult
                              ? verifyResult.valid ? '#4ade80' : '#f87171'
                              : 'var(--text-primary)',
                          }}
                        >
                          {isVerifying ? (
                            <><ArrowPathIcon className="w-3.5 h-3.5 animate-spin" /> VERIFYING...</>
                          ) : verifyResult ? (
                            <>{verifyResult.valid ? <CheckIcon className="w-3.5 h-3.5" /> : <XMarkIcon className="w-3.5 h-3.5" />} {verifyResult.message}</>
                          ) : (
                            <>VERIFY SIGNATURE</>
                          )}
                        </button>

                        {decoded && (
                          <div className="space-y-1.5">
                            {Object.entries(decoded.payload).map(([key, value]) => (
                              <div key={key} className="flex items-start gap-2">
                                <span className="text-[10px] font-digital uppercase font-bold flex-shrink-0 pt-0.5" style={{ color: 'var(--text-tertiary)', minWidth: '60px' }}>
                                  {key}
                                </span>
                                <div
                                  className="flex-1 px-2 py-0.5 text-[11px] font-mono break-all cursor-pointer hover:opacity-80 transition-opacity"
                                  style={{
                                    fontFamily: 'IBM Plex Mono, monospace',
                                    backgroundColor: 'var(--bg-input)',
                                    border: '1px solid var(--border-color)',
                                    borderRadius: '5px',
                                    color: key === 'key' || key === 'address' ? '#4ade80'
                                      : key === 'signature' ? '#f59e0b'
                                      : 'var(--text-primary)',
                                  }}
                                  onClick={() => {
                                    const text = typeof value === 'string' ? value : JSON.stringify(value)
                                    navigator.clipboard.writeText(text)
                                    toast.success(`${key} copied!`)
                                  }}
                                  title={`Click to copy ${key}`}
                                >
                                  {typeof value === 'string'
                                    ? (key === 'time' ? `${value} (${new Date(parseFloat(value) * 1000).toLocaleString()})` : value)
                                    : JSON.stringify(value)}
                                </div>
                              </div>
                            ))}
                            <div className="text-[9px] font-digital uppercase pt-1" style={{ color: 'var(--text-tertiary)', opacity: 0.5 }}>
                              Format: {decoded.format === 'jwt' ? 'JWT' : 'Auth (base64url)'}
                            </div>
                          </div>
                        )}

                        {!decoded && getStoredToken() && (
                          <div className="px-3 py-3 text-center text-xs font-digital" style={{ backgroundColor: 'var(--bg-input)', border: '1px solid var(--border-color)', borderRadius: '8px', color: 'var(--text-tertiary)' }}>
                            Could not decode token
                          </div>
                        )}

                        {!getStoredToken() && (
                          <div className="px-3 py-3 text-center text-xs font-digital" style={{ backgroundColor: 'var(--bg-input)', border: '1px solid var(--border-color)', borderRadius: '8px', color: 'var(--text-tertiary)' }}>
                            No token available
                          </div>
                        )}
                      </div>
                    )
                  })()}
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </div>

        {/* Edit daily limit inline */}
        {isEditing && dailyLimit !== null && (
          <div className="px-3 pb-2">
            <div className="flex items-center gap-2 px-3 py-1.5" style={{ backgroundColor: 'var(--bg-input)', border: '1px solid var(--border-color)', borderRadius: '8px' }}>
              <span className="text-[10px] font-digital uppercase tracking-wider flex-shrink-0" style={{ color: 'var(--text-tertiary)' }}>Limit</span>
              <span className="text-amber-400 text-sm font-bold">$</span>
              <input
                type="number"
                value={newLimit}
                onChange={(e) => setNewLimit(e.target.value)}
                className="flex-1 border border-amber-500/30 px-2 py-1 text-sm font-mono focus:outline-none focus:border-amber-400/60"
                style={{ backgroundColor: 'var(--bg-primary)', color: 'var(--text-primary)', borderRadius: '0px' }}
                placeholder="1000"
                autoFocus
                onKeyDown={(e) => { if (e.key === 'Enter') handleSaveLimit(); if (e.key === 'Escape') setIsEditing(false) }}
              />
              <button onClick={handleSaveLimit} disabled={isSaving} className="p-1 text-amber-400 hover:text-amber-300 transition-all disabled:opacity-40">
                {isSaving ? <ArrowPathIcon className="w-3.5 h-3.5 animate-spin" /> : <CheckIcon className="w-3.5 h-3.5" />}
              </button>
              <button onClick={() => setIsEditing(false)} className="p-1 transition-colors" style={{ color: 'var(--text-secondary)' }}>
                <XMarkIcon className="w-3.5 h-3.5" />
              </button>
            </div>
          </div>
        )}

        <div className="mx-3" style={{ borderBottom: '1px solid var(--border-color)', opacity: 0.4 }} />
      </motion.div>
    </>
  )
}
