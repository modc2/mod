'use client'

import { useUserContext } from '@/app/context'
import { ArrowRightOnRectangleIcon, KeyIcon } from '@heroicons/react/24/outline'
import { CopyButton } from '@/app/block/ui/CopyButton'
import 'react-responsive-modal/styles.css'
import {text2color, shorten} from "@/app/utils";
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

  return (
    <div 
      className="relative flex-shrink-0"
    >
      <div
        onClick={handleUserClick}
        className={`flex items-center gap-3 transition-all duration-300 backdrop-blur-xl rounded-2xl border-2 overflow-hidden cursor-pointer hover:shadow-2xl`}
        style={{
          borderColor:`${userColor}80`,
          backgroundColor: `${userColor}15`,
          boxShadow: `0 0 30px ${userColor}40`,
          height: '60px',
          minWidth: '60px',
          width:  'auto' ,
          paddingRight: '16px',
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
          onClick={(e) => {
          }}
        >
          <KeyIcon className="w-9 h-9" style={{ color:  userColor }} />
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