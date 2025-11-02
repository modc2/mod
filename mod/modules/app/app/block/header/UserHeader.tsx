'use client'

import { useState } from 'react'
import { useUserContext } from '@/app/block/context/UserContext'
import { UserIcon, ArrowRightOnRectangleIcon, KeyIcon, CurrencyDollarIcon, CubeIcon } from '@heroicons/react/24/outline'
import { CopyButton } from '@/app/block/CopyButton'
import { Modal } from 'react-responsive-modal'
import 'react-responsive-modal/styles.css'

const shorten = (str: string): string => {
  if (!str || str.length <= 12) return str
  return `${str.slice(0, 6)}...${str.slice(-4)}`
}

const text2color = (text: string): string => {
  if (!text) return '#00ff00'
  let hash = 0
  for (let i = 0; i < text.length; i++) hash = text.charCodeAt(i) + ((hash << 5) - hash)
  const golden_ratio = 0.618033988749895
  const hue = (hash * golden_ratio * 360) % 360
  const saturation = 65 + (Math.abs(hash >> 8) % 35)
  const lightness = 50 + (Math.abs(hash >> 16) % 20)
  return `hsl(${hue}, ${saturation}%, ${lightness}%)`
}

export function UserHeader() {
  const { keyInstance, setKeyInstance, user, authLoading } = useUserContext()
  const [showTooltip, setShowTooltip] = useState(false)
  const [showPasswordModal, setShowPasswordModal] = useState(false)
  const [password, setPassword] = useState('')
  const [isSigningIn, setIsSigningIn] = useState(false)

  const handleSignOut = () => {
    setKeyInstance(null)
    setShowPasswordModal(true)
  }

  const handleSignIn = async () => {
    if (!password.trim()) return
    setIsSigningIn(true)
    try {
      const { Key } = await import('@/app/block/key/key')
      const newKey = new Key(password)
      setKeyInstance(newKey)
      setShowPasswordModal(false)
      setPassword('')
    } catch (error) {
      console.error('Failed to create key:', error)
    } finally {
      setIsSigningIn(false)
    }
  }

  if (authLoading) {
    return (
      <div className="flex items-center gap-2 px-3 py-2 rounded-lg border border-white/20 bg-white/5">
        <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
        <span className="text-sm text-white/60">Loading...</span>
      </div>
    )
  }

  if (!keyInstance) {
    return (
      <>
        <button
          onClick={() => setShowPasswordModal(true)}
          className="flex items-center gap-2 px-4 py-2 rounded-lg border border-emerald-500/40 bg-emerald-500/10 hover:bg-emerald-500/20 transition-all text-emerald-400 font-semibold"
        >
          <KeyIcon className="w-5 h-5" />
          <span>Sign In</span>
        </button>

        <Modal
          open={showPasswordModal}
          onClose={() => {
            setShowPasswordModal(false)
            setPassword('')
          }}
          center
          classNames={{
            modal: 'bg-zinc-900 rounded-xl border border-white/10 p-6 max-w-md',
            overlay: 'bg-black/80 backdrop-blur-sm'
          }}
        >
          <div className="space-y-4">
            <h2 className="text-2xl font-bold text-white flex items-center gap-2">
              <KeyIcon className="w-6 h-6 text-emerald-400" />
              Sign In
            </h2>
            <p className="text-white/60 text-sm">Enter a password to generate your key</p>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleSignIn()}
              placeholder="Enter password"
              className="w-full px-4 py-3 bg-black/50 border border-white/20 rounded-lg text-white placeholder-white/40 focus:outline-none focus:border-emerald-500/50"
              autoFocus
            />
            <button
              onClick={handleSignIn}
              disabled={!password.trim() || isSigningIn}
              className="w-full px-4 py-3 bg-emerald-500 hover:bg-emerald-600 disabled:bg-white/10 disabled:text-white/40 text-black font-bold rounded-lg transition-all"
            >
              {isSigningIn ? 'Generating Key...' : 'Sign In'}
            </button>
          </div>
        </Modal>
      </>
    )
  }

  const userAddress = keyInstance.address
  const userColor = text2color(userAddress)
  const balance = user?.balance || 0
  const modsCount = user?.mods?.length || 0

  return (
    <div className="relative">
      <div
        className="flex items-center gap-2 px-3 py-2 rounded-lg border cursor-pointer transition-all hover:shadow-lg"
        style={{
          borderColor: `${userColor}40`,
          backgroundColor: `${userColor}10`,
        }}
        onMouseEnter={() => setShowTooltip(true)}
        onMouseLeave={() => setShowTooltip(false)}
      >
        <UserIcon className="w-5 h-5" style={{ color: userColor }} />
        <span className="font-mono font-semibold" style={{ color: userColor }}>
          {shorten(userAddress)}
        </span>
        <CopyButton content={userAddress} size="sm" />
        <button
          onClick={handleSignOut}
          className="ml-2 p-1.5 rounded-md hover:bg-white/10 transition-all"
          title="Sign Out"
          style={{ color: userColor }}
        >
          <ArrowRightOnRectangleIcon className="w-5 h-5" />
        </button>
      </div>

      {showTooltip && (
        <div
          className="absolute top-full right-0 mt-2 p-4 rounded-lg border shadow-xl z-50 min-w-[280px]"
          style={{
            borderColor: `${userColor}40`,
            backgroundColor: '#0a0a0a',
          }}
        >
          <div className="space-y-3">
            <div className="flex items-center gap-2 pb-2 border-b" style={{ borderColor: `${userColor}20` }}>
              <UserIcon className="w-5 h-5" style={{ color: userColor }} />
              <span className="font-bold" style={{ color: userColor }}>Account Details</span>
            </div>
            
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2 text-white/60">
                  <CurrencyDollarIcon className="w-4 h-4" />
                  <span className="text-sm">Balance</span>
                </div>
                <span className="font-bold" style={{ color: userColor }}>{balance}</span>
              </div>
              
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2 text-white/60">
                  <CubeIcon className="w-4 h-4" />
                  <span className="text-sm">Modules</span>
                </div>
                <span className="font-bold" style={{ color: userColor }}>{modsCount}</span>
              </div>
              
              <div className="pt-2 border-t" style={{ borderColor: `${userColor}20` }}>
                <div className="flex items-center gap-2 text-white/60 mb-1">
                  <KeyIcon className="w-4 h-4" />
                  <span className="text-sm">Address</span>
                </div>
                <div className="flex items-center gap-2">
                  <code className="text-xs font-mono" style={{ color: userColor }}>
                    {userAddress}
                  </code>
                  <CopyButton content={userAddress} size="sm" />
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      <Modal
        open={showPasswordModal}
        onClose={() => {
          setShowPasswordModal(false)
          setPassword('')
        }}
        center
        classNames={{
          modal: 'bg-zinc-900 rounded-xl border border-white/10 p-6 max-w-md',
          overlay: 'bg-black/80 backdrop-blur-sm'
        }}
      >
        <div className="space-y-4">
          <h2 className="text-2xl font-bold text-white flex items-center gap-2">
            <KeyIcon className="w-6 h-6 text-emerald-400" />
            Sign In Again
          </h2>
          <p className="text-white/60 text-sm">Enter a password to generate a new key</p>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleSignIn()}
            placeholder="Enter password"
            className="w-full px-4 py-3 bg-black/50 border border-white/20 rounded-lg text-white placeholder-white/40 focus:outline-none focus:border-emerald-500/50"
            autoFocus
          />
          <button
            onClick={handleSignIn}
            disabled={!password.trim() || isSigningIn}
            className="w-full px-4 py-3 bg-emerald-500 hover:bg-emerald-600 disabled:bg-white/10 disabled:text-white/40 text-black font-bold rounded-lg transition-all"
          >
            {isSigningIn ? 'Generating Key...' : 'Sign In'}
          </button>
        </div>
      </Modal>
    </div>
  )
}