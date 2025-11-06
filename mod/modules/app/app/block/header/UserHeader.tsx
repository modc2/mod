'use client'

import { useState } from 'react'
import { useUserContext } from '@/app/block/context/UserContext'
import { UserIcon, ArrowRightOnRectangleIcon, KeyIcon, CurrencyDollarIcon, CubeIcon } from '@heroicons/react/24/outline'
import { CopyButton } from '@/app/block/CopyButton'
import 'react-responsive-modal/styles.css'
import {text2color, shorten} from "@/app/utils";
import Link from 'next/link'

export function UserHeader() {
  const { keyInstance, setKeyInstance, user, authLoading, signIn, signOut } = useUserContext()
  const [showPasswordInput, setShowPasswordInput] = useState(false)
  const [password, setPassword] = useState('')
  const [isSigningIn, setIsSigningIn] = useState(false)
  const [showTooltip, setShowTooltip] = useState(false)

  const handleSignOut = () => {
    signOut()
    setShowPasswordInput(true)
  }

  const handleSignIn = async () => {
    if (!password.trim()) return
    setIsSigningIn(true)
    try {
      await signIn(password)
      setShowPasswordInput(false)
      setPassword('')
    } catch (error) {
      console.error('Failed to sign in:', error)
    } finally {
      setIsSigningIn(false)
    }
  }

  const handleCopy = () => {
    if (keyInstance?.address) {
      navigator.clipboard.writeText(keyInstance.address)
    }
  }

  if (authLoading) {
    return (
      <div className="flex items-center gap-3 px-6 py-4 rounded-2xl border-2 backdrop-blur-xl" style={{height: '60px', borderColor: '#00ff0080', backgroundColor: '#00ff0015', boxShadow: '0 0 20px #00ff0030'}}>
        <div className="w-6 h-6 border-2 border-white/30 border-t-white rounded-full animate-spin" />
        <span className="text-xl text-white/70 font-bold">Loading...</span>
      </div>
    )
  }

  if (!keyInstance) {
    return (
      <div className="flex items-center gap-3">
        {showPasswordInput ? (
          <div className="flex items-center gap-3 animate-in fade-in slide-in-from-right-2 duration-300">
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleSignIn()}
              placeholder="Enter password"
              className="px-5 py-3 text-xl bg-black/60 border-2 border-white/20 rounded-xl text-white placeholder-white/40 focus:outline-none focus:border-emerald-400/60 focus:ring-2 focus:ring-emerald-400/20 w-64 backdrop-blur-xl transition-all font-bold"
              style={{height: '60px'}}
              autoFocus
            />
            <button
              onClick={handleSignIn}
              disabled={!password.trim() || isSigningIn}
              className="p-4 bg-gradient-to-r from-emerald-500 to-emerald-600 hover:from-emerald-600 hover:to-emerald-700 disabled:from-white/10 disabled:to-white/10 disabled:text-white/40 text-white rounded-xl transition-all shadow-lg shadow-emerald-500/20 hover:shadow-emerald-500/40 disabled:shadow-none hover:scale-110 active:scale-95"
              style={{height: '60px', width: '60px'}}
              title="Sign In"
            >
              {isSigningIn ? (
                <div className="w-6 h-6 border-2 border-white/30 border-t-white rounded-full animate-spin" />
              ) : (
                <ArrowRightOnRectangleIcon className="w-7 h-7" />
              )}
            </button>
            <button
              onClick={() => {
                setShowPasswordInput(false)
                setPassword('')
              }}
              className="px-5 py-3 text-xl text-white/60 hover:text-white hover:bg-white/10 rounded-xl transition-all font-bold"
              style={{height: '60px'}}
            >
              Cancel
            </button>
          </div>
        ) : (
          <button
            onClick={() => setShowPasswordInput(true)}
            className="group p-4 rounded-xl border-2 border-emerald-400/30 bg-gradient-to-r from-emerald-500/10 to-emerald-600/10 hover:from-emerald-500/20 hover:to-emerald-600/20 transition-all text-emerald-400 shadow-lg shadow-emerald-500/10 hover:shadow-emerald-500/30 hover:scale-110 active:scale-95"
            style={{height: '60px', width: '60px'}}
            title="Sign In"
          >
            <KeyIcon className="w-7 h-7 group-hover:rotate-12 transition-transform" />
          </button>
        )}
      </div>
    )
  }

  const userAddress = keyInstance.address
  const userColor = text2color(userAddress)
  const balance = user?.balance || 0
  const modsCount = user?.mods?.length || 0
  const displayAddress = userAddress.length > 12 ? `${userAddress.slice(0, 6)}...${userAddress.slice(-4)}` : userAddress

  return (
    <div className="relative">
      <div
        className="flex items-center gap-3 px-6 py-4 rounded-2xl border-2 transition-all hover:shadow-2xl backdrop-blur-xl"
        style={{
          borderColor: `${userColor}80`,
          backgroundColor: `${userColor}15`,
          boxShadow: `0 0 20px ${userColor}30`,
          height: '60px'
        }}
        onMouseEnter={() => setShowTooltip(true)}
        onMouseLeave={() => setShowTooltip(false)}
      >
        <Link href={`/user/${userAddress}`} className="flex items-center gap-3 cursor-pointer hover:scale-105 active:scale-95 transition-all">
          <div className="p-2 rounded-lg" style={{ backgroundColor: `${userColor}30` }}>
            <UserIcon className="w-6 h-6" style={{ color: userColor }} />
          </div>
          <span 
            className="font-mono font-black text-lg cursor-pointer select-all uppercase tracking-wide drop-shadow-lg"
            style={{ color: userColor }}
            onClick={(e) => {
              e.preventDefault()
              handleCopy()
            }}
            title="Click to copy full address"
          >
            {displayAddress}
          </span>
        </Link>
        
        <button
          onClick={handleCopy}
          className="p-2 rounded-lg hover:bg-white/10 transition-all"
          title="Copy address"
        >
          <CopyButton content={userAddress} size="sm" style={{color: 'white'}} />
        </button>

        <div className="flex items-center gap-2 px-3 py-2 rounded-lg" style={{ backgroundColor: `${userColor}20` }}>
          <KeyIcon className="w-5 h-5" style={{ color: userColor }} />
        </div>
        
        <button
          onClick={(e) => {
            e.preventDefault()
            handleSignOut()
          }}
          className="p-3 rounded-lg hover:bg-red-500/20 transition-all group border-2 border-transparent hover:border-red-500/40"
          title="Sign Out"
        >
          <ArrowRightOnRectangleIcon className="w-6 h-6 text-red-500 font-black group-hover:translate-x-0.5 group-hover:scale-110 transition-transform" />
        </button>
      </div>
      
      {showTooltip && (
        <div 
          className="absolute top-full left-0 mt-2 px-5 py-4 rounded-xl border-2 backdrop-blur-xl z-50 animate-in fade-in slide-in-from-top-2 duration-200"
          style={{
            borderColor: `${userColor}80`,
            backgroundColor: `${userColor}20`,
            boxShadow: `0 8px 24px ${userColor}40`,
            minWidth: '220px'
          }}
        >
          <div className="flex items-center gap-3 mb-3">
            <div className="p-2 rounded-lg" style={{ backgroundColor: `${userColor}40` }}>
              <CurrencyDollarIcon className="w-6 h-6" style={{ color: userColor }} />
            </div>
            <div>
              <div className="text-xs text-white/70 font-bold uppercase tracking-wider">Balance</div>
              <div className="font-black text-xl" style={{ color: userColor }}>{balance}</div>
            </div>
          </div>
          
          <div className="flex items-center gap-3">
            <div className="p-2 rounded-lg" style={{ backgroundColor: `${userColor}40` }}>
              <CubeIcon className="w-6 h-6" style={{ color: userColor }} />
            </div>
            <div>
              <div className="text-xs text-white/70 font-bold uppercase tracking-wider">Mods</div>
              <div className="font-black text-xl" style={{ color: userColor }}>{modsCount}</div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}