'use client'

import { useState } from 'react'
import { useUserContext } from '@/app/block/context/UserContext'
import { UserIcon, ArrowRightOnRectangleIcon, KeyIcon, WalletIcon } from '@heroicons/react/24/outline'
import { CopyButton } from '@/app/block/CopyButton'
import 'react-responsive-modal/styles.css'
import {text2color, shorten} from "@/app/utils";
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import WalletAuthButton from './WalletAuthButton'

export function UserHeader() {
  const { keyInstance, user, authLoading, signOut } = useUserContext()
  const [isExpanded, setIsExpanded] = useState(false)
  const router = useRouter()

  const handleSignOut = () => {
    signOut()
    setIsExpanded(false)
  }

  const handleUserClick = () => {
    if (user?.address) {
      router.push(`/user/${user.address}`)
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
      <div className="flex items-center gap-4">
        <WalletAuthButton />
      </div>
    )
  }

  const userColor = text2color(user.address)
  const walletMode = localStorage.getItem('wallet_mode')
  const walletType = localStorage.getItem('wallet_type') || user.crypto_type
  const displayAddress = walletMode === 'subwallet' 
    ? localStorage.getItem('wallet_address') || user.address
    : keyInstance.address

  return (
    <div 
      className="relative"
      onMouseEnter={() => setIsExpanded(true)}
      onMouseLeave={() => setIsExpanded(false)}
    >
      <div
        onClick={handleUserClick}
        className="flex items-center gap-3 transition-all duration-300 backdrop-blur-xl rounded-2xl border-2 overflow-hidden cursor-pointer hover:shadow-2xl"
        style={{
          borderColor: `${userColor}80`,
          backgroundColor: `${userColor}15`,
          boxShadow: `0 0 20px ${userColor}30`,
          height: '60px',
          width: isExpanded ? 'auto' : '60px',
          paddingRight: isExpanded ? '16px' : '0',
        }}
      >
        <div 
          className="p-4 transition-all hover:scale-110 active:scale-95 flex-shrink-0"
          style={{
            height: '60px',
            width: '60px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center'
          }}
        >
          {walletMode === 'subwallet' ? (
            <WalletIcon className="w-7 h-7" style={{ color: userColor }} />
          ) : (
            <UserIcon className="w-7 h-7" style={{ color: userColor }} />
          )}
        </div>

        <div 
          className="flex items-center gap-4 transition-all duration-300"
          style={{
            opacity: isExpanded ? 1 : 0,
            width: isExpanded ? 'auto' : '0',
            overflow: 'hidden',
            whiteSpace: 'nowrap',
          }}
        >

          {user?.balance !== undefined && (
            <div className="flex flex-col">
              <div className="text-xs text-white/60 font-bold uppercase tracking-wider">Balance</div>
              <div className="font-black text-lg" style={{ color: userColor }}>
                {user.balance.toFixed(2)}
              </div>
            </div>
          )}

          {user?.mods && user.mods.length > 0 && (
            <div className="flex flex-col">
              <div className="text-xs text-white/60 font-bold uppercase tracking-wider">Mods</div>
              <div className="font-black text-lg" style={{ color: userColor }}>
                {user.mods.length}
              </div>
            </div>
          )}

          <div className="flex flex-col min-w-[200px]">
            <div className="flex items-center gap-2">
              <KeyIcon className="w-4 h-4" style={{ color: userColor }} />
              <div className="font-mono font-black text-sm truncate max-w-[180px]" style={{ color: userColor }}>
                {shorten(displayAddress, 8, 8)}
              </div>
              <span className="text-white/40 font-mono text-xs">({walletType})</span>
              <CopyButton content={displayAddress} size="sm" style={{color: userColor}} />
            </div>
          </div>

          <button
            onClick={(e) => {
              e.preventDefault()
              e.stopPropagation()
              handleSignOut()
            }}
            className="p-3 rounded-xl hover:bg-red-500/20 transition-all group border-2 border-transparent hover:border-red-500/40 hover:scale-110 active:scale-95 flex-shrink-0"
            style={{height: '48px', width: '48px'}}
            title="Sign Out"
          >
            <ArrowRightOnRectangleIcon className="w-6 h-6 text-red-500 font-black group-hover:translate-x-0.5 transition-transform" />
          </button>
        </div>
      </div>
    </div>
  )
}