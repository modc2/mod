'use client'

import { useUserContext } from '@/block/context'
import { ArrowRightOnRectangleIcon, KeyIcon } from '@heroicons/react/24/outline'
import { CopyButton } from '@/block/ui/CopyButton'
import 'react-responsive-modal/styles.css'
import {text2color, shorten} from "@/block/utils";
import { useRouter } from 'next/navigation'
import WalletAuthButton from './WalletAuthButton'


export function UserHeader() {
  const {  user, authLoading, signOut} = useUserContext()
  const router = useRouter()

  const handleSignOut = () => {
    signOut()
  }

  const handleUserClick = () => {
    if (user?.key) {
      router.push(`/user/${user.key}`)
    }
  }

  if (authLoading) {
    return (
      <div className="flex items-center gap-3 px-6 py-4 rounded-2xl border-2 backdrop-blur-xl shadow-2xl" style={{height: '60px', minWidth: '60px', borderColor: '#00ff0080', backgroundColor: '#00ff0015', boxShadow: '0 0 30px #00ff0040'}}>
        <div className="w-6 h-6 border-2 border-white/30 border-t-white rounded-full animate-spin" />
        <span className="text-xl text-white/70 font-bold hidden sm:inline">Loading...</span>
      </div>
    )
  }

  if (!user) {
    return (
      <div className="flex items-center gap-4">
        <WalletAuthButton />
      </div>
    )
  }

  const userColor = text2color(user.key)
  const walletMode = localStorage.getItem('wallet_mode')
  const displayAddress = walletMode === 'subwallet' 
    ? localStorage.getItem('wallet_address') || user.key 
    : user.key

  const hexToRgb = (hex: string) => {
    const result = /^#?([a-f\\d]{2})([a-f\\d]{2})([a-f\\d]{2})$/i.exec(hex)
    return result ? {
      r: parseInt(result[1], 16),
      g: parseInt(result[2], 16),
      b: parseInt(result[3], 16)
    } : { r: 139, g: 92, b: 246 }
  }
  
  const userRgb = hexToRgb(userColor)
  const borderColor = `rgba(${userRgb.r}, ${userRgb.g}, ${userRgb.b}, 0.4)`
  const glowColor = `rgba(${userRgb.r}, ${userRgb.g}, ${userRgb.b}, 0.2)`

  return (
    <div 
      className="relative flex-shrink-0"
    >
      <div
        onClick={handleUserClick}
        className={`flex items-center gap-3 transition-all duration-300 backdrop-blur-xl rounded-xl border-2 overflow-hidden cursor-pointer hover:shadow-2xl`}
        style={{
          borderColor: borderColor,
          backgroundColor: `rgba(${userRgb.r}, ${userRgb.g}, ${userRgb.b}, 0.1)`,
          boxShadow: `0 0 12px ${glowColor}`,
          height: '60px',
          minWidth: '60px',
          width:  'auto' ,
          paddingRight: '16px',
        }}
      >
        <div 
          className="px-3 py-2 rounded-md border transition-all hover:scale-110 active:scale-95 flex-shrink-0"
          style={{
            height: '60px',
            width: '60px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            backgroundColor: `rgba(${userRgb.r}, ${userRgb.g}, ${userRgb.b}, 0.1)`,
            borderColor: `rgba(${userRgb.r}, ${userRgb.g}, ${userRgb.b}, 0.4)`
          }}
          onClick={(e) => {
          }}
        >
          <KeyIcon className="w-10 h-10" style={{ color:  userColor }} />
        </div>

        <div 
          className="flex items-center gap-4 transition-all duration-300"
          style={{
            opacity:  1 ,
            width:  'auto' ,
            overflow: 'hidden',
            whiteSpace: 'nowrap',
          }}
        >



          {displayAddress && (
          <div className="flex flex-col ">
            <div className="flex items-center gap-2">
              <div className="font-mono font-black text-lg truncate" style={{ color: userColor }}>
                {shorten(displayAddress, 4, 4)}
              </div>
            </div>
          </div>
          )}

              <CopyButton content={displayAddress} size="lg"  />

          <button
            onClick={(e) => {
              e.preventDefault()
              e.stopPropagation()
              handleSignOut()
            }}
            className="p-3 rounded-xl hover:bg-red-500/20 transition-all group border-2 border-transparent hover:border-red-500/40 hover:scale-90 active:scale-95 flex-shrink-0"
            style={{height: '60spx', width: '60px'}}
            title="Sign Out"
          >
            <ArrowRightOnRectangleIcon className="w-7 h-6 text-red-500 font-black group-hover:translate-x-0.5 transition-transform" />
          </button>
        </div>
      </div>
    </div>
  )
}