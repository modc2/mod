'use client'
import { text2color, shorten } from '@/bloc/utils'
import { CopyButton } from '@/bloc/ui/CopyButton'
import { KeyIcon, CubeIcon } from '@heroicons/react/24/outline'
import { UserType } from '@/bloc/types'
import Link from 'next/link'
import { useState } from 'react'
import { Coins } from 'lucide-react'
import { useUserContext } from '@/bloc/context'

interface UserCardProps {
  user: UserType
  mode?: 'explore' | 'page'
}

export const UserCard = ({ user, mode  = 'explore' }: UserCardProps) => {
  const { client, network } = useUserContext()
  const [balance, setBalance] = useState(user.balance)
  const [isLoading, setIsLoading] = useState(true)
  const userColor = text2color(user.key)
  const { user: currentUser } = useUserContext()
  const myMod = currentUser && currentUser.key === user.key
  
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

  useState(() => {
    const timer = setTimeout(() => setIsLoading(false), 800)
    return () => clearTimeout(timer)
  })

  const CardContent = () => (
    <div className="group relative border-2 rounded-xl px-4 hover:shadow-xl transition-all duration-300 backdrop-blur-sm hover:scale-[1.01] bg-black" style={{ borderColor: borderColor, boxShadow: `0 0 12px ${glowColor}`, paddingTop: '12px', paddingBottom: '12px', minHeight: '120px' }}>
      <div className="absolute -inset-1 bg-gradient-to-r opacity-5 group-hover:opacity-10 blur-lg transition-all duration-500 rounded-xl" style={{ background: `linear-gradient(45deg, ${userColor}, transparent, ${userColor})` }} />
      
      <div className="relative z-10">
        <div className="flex flex-col gap-3">
          <div className="flex items-center gap-2 px-3 py-2 rounded-md border" style={{ backgroundColor: `rgba(${userRgb.r}, ${userRgb.g}, ${userRgb.b}, 0.1)`, borderColor: `rgba(${userRgb.r}, ${userRgb.g}, ${userRgb.b}, 0.4)` }}>
            <Link href={`/user/${user.key}`} onClick={(e) => e.stopPropagation()} className="hover:scale-110 transition-transform">
              <KeyIcon className="w-10 h-10" style={{ color: userColor }} />
            </Link>
            <Link href={`/user/${user.key}`} onClick={(e) => e.stopPropagation()} className="hover:underline">
                <code className="text-lg font-mono font-bold" style={{ color: userColor, fontFamily: "'Courier New', 'Consolas', 'Monaco', monospace", minWidth: '200px', display: 'inline-block' }} title={user.key}>
                  {user.key.substring(0, 6)}...{user.key.substring(user.key.length - 6)}
                </code>
            </Link>
            <CopyButton text={user.key} size="sm" />
          </div>
          
          <div className="flex flex-wrap items-center gap-2">
            {balance !== undefined && (
              <div className="flex items-center gap-2 px-3 py-2 rounded-md border" style={{ backgroundColor: `rgba(${userRgb.r}, ${userRgb.g}, ${userRgb.b}, 0.08)`, borderColor: `rgba(${userRgb.r}, ${userRgb.g}, ${userRgb.b}, 0.3)` }}>
                <Coins className="w-5 h-5" style={{ color: userColor }} />
                <code className="text-xl font-mono font-bold" style={{ color: userColor, fontFamily: "'Courier New', 'Consolas', 'Monaco', monospace", minWidth: '90px', display: 'inline-block', textAlign: 'right' }}>
                  {Number(balance) === 0 ? '0' : Number(balance).toFixed(2).replace(/^0+(?=\d)/, '')}
                </code>
                <CopyButton text={String(balance)} size="sm" />
              </div>
            )}
            
            {user.mods && user.mods.length > 0 && (
              <div className="flex items-center gap-2 px-3 py-2 rounded-md border" style={{ backgroundColor: `rgba(${userRgb.r}, ${userRgb.g}, ${userRgb.b}, 0.08)`, borderColor: `rgba(${userRgb.r}, ${userRgb.g}, ${userRgb.b}, 0.3)` }}>
                <CubeIcon className="w-5 h-5" style={{ color: userColor }} />
                <code className="text-xl font-mono font-bold" style={{ color: userColor, fontFamily: "'Courier New', 'Consolas', 'Monaco', monospace", minWidth: '90px', display: 'inline-block', textAlign: 'right' }}>
                  {user.mods.length}
                </code>
                <CopyButton text={String(user.mods.length)} size="sm" />
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )

  if (mode === 'explore') {
    return (
      <Link href={`/user/${user.key}`} className="block">
        <CardContent />
      </Link>
    )
  }

  return <CardContent />
}

export default UserCard
