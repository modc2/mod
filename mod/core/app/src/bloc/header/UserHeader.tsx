'use client'

import { useUserContext } from '@/bloc/context'
import { ArrowRightOnRectangleIcon, KeyIcon } from '@heroicons/react/24/outline'
import { CopyButton } from '@/bloc/ui/CopyButton'
import 'react-responsive-modal/styles.css'
import {text2color, shorten} from "@/bloc/utils";
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
        <div className="flex items-center gap-3 px-6 py-3 rounded-xl border-2 backdrop-blur-xl shadow-2xl" style={{height: '60px', minWidth: '60px', borderColor: '#00ff0080', backgroundColor: '#00ff0015', boxShadow: '0 0 30px #00ff0040'}}>
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

    const hexToRgb = (hex: string) => {
      const result = /^#?([a-f\\d]{2})([a-f\\d]{2})([a-f\\d]{2})$/i.exec(hex)
      return result ? {
        r: parseInt(result[1], 16),
        g: parseInt(result[2], 16),
        b: parseInt(result[3], 16)
      } : { r: 220, g: 38, b: 38 }
    }
    
      const userRgb = hexToRgb(userColor)
      const borderColor = `rgba(${userRgb.r}, ${userRgb.g}, ${userRgb.b}, 0.4)`
      const glowColor = `rgba(${userRgb.r}, ${userRgb.g}, ${userRgb.b}, 0.2)`
      const bgColor = `rgba(${userRgb.r}, ${userRgb.g}, ${userRgb.b}, 0.08)`

      return (

            <div
              onClick={handleUserClick}
              className={`flex items-center gap-3 transition-all duration-300 backdrop-blur-xl rounded-xl border-2 overflow-hidden hover:shadow-2xl cursor-pointer`}
                style={{
                  borderColor: borderColor,
                  backgroundColor: bgColor,
                  boxShadow: `0 0 12px ${glowColor}`,
                height: '60px',
                minWidth: '60px',
                width:  'auto' ,
                paddingRight: '12px',
                paddingTop: '12px',
                paddingBottom: '12px'
              }}
            >
          
                <div 
                  className="px-3 py-2 rounded-md border transition-all hover:scale-110 active:scale-95 flex-shrink-0 group relative"
                  style={{
                    height: '60px',
                    width: '60px',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    backgroundColor: bgColor,
                    borderColor: borderColor,
                    boxShadow: `0 0 12px rgba(${userRgb.r}, ${userRgb.g}, ${userRgb.b}, 0.25)`
                  }}
                  title={user.key}
                >
                    <KeyIcon className="w-10 h-10" style={{ color: userColor }} />
                </div>
                <CopyButton text={user.key} size="md" />
                    <button
                      onClick={(e) => { e.stopPropagation(); handleSignOut(); }}
                      className="px-4 py-2 rounded-md border transition-all hover:scale-110 active:scale-95 flex items-center gap-2"
                      style={{
                        backgroundColor: 'rgba(220, 38, 38, 0.08)',
                        borderColor: 'rgba(220, 38, 38, 0.4)',
                        color: '#dc2626'
                      }}
                      title="Sign Out"
                    >
                      <ArrowRightOnRectangleIcon className="w-5 h-5" />
                    </button>
              </div>


      )
  }
