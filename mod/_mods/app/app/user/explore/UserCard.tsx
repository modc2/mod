'use client'
import { text2color, shorten } from '@/app/utils'
import { CopyButton } from '@/app/block/ui/CopyButton'
import { KeyIcon } from '@heroicons/react/24/outline'
import { UserType } from '@/app/types'
import Link from 'next/link'
import { useState } from 'react'
import { RotateCcw } from 'lucide-react'
import { useUserContext } from '@/app/context'

interface UserCardProps {
  user: UserType
  mode?: 'explore' | 'page'
}

export const UserCard = ({ user, mode  = 'explore' }: UserCardProps) => {
  const { client, network } = useUserContext()
  const [balance, setBalance] = useState(user.balance)
  const [refreshing, setRefreshing] = useState(false)
  const [isLoading, setIsLoading] = useState(true)
  const userColor = text2color(user.key)
  
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

  // Simulate loading complete after mount
  useState(() => {
    const timer = setTimeout(() => setIsLoading(false), 800)
    return () => clearTimeout(timer)
  })

  const handleRefreshBalance = async (e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setRefreshing(true)
    try {
      if (!network) throw new Error('Client not initialized')
      if (!client) throw new Error('Client not initialized')
      const userData = await client.call('user', { address: user.key , update: true})
      console.log(userData, 'REFRESHED USER DATA')
      setBalance(userData.balance)
      setIsLoading(false)
    
    } catch (error) {
      console.error('Failed to refresh balance:', error)
    } finally {
      setRefreshing(false)
    }
  }

  const CardContent = () => (
    <div className="group relative border rounded-lg overflow-hidden hover:shadow-lg transition-all duration-200 backdrop-blur-sm bg-black" style={{ borderColor: borderColor, boxShadow: `0 0 8px ${glowColor}` }}>
      <div className="absolute -inset-1 bg-gradient-to-r opacity-5 group-hover:opacity-10 blur transition-all duration-300 rounded-lg" style={{ background: `linear-gradient(45deg, ${userColor}, transparent, ${userColor})` }} />
      
      {/* TOP LEVEL - Address with Key Icon and Refresh Button */}
      <div className="relative z-10 px-4 py-3 border-b" style={{ borderColor: `rgba(${userRgb.r}, ${userRgb.g}, ${userRgb.b}, 0.2)`, backgroundColor: `rgba(${userRgb.r}, ${userRgb.g}, ${userRgb.b}, 0.05)` }}>
        <div className="flex items-center gap-3 justify-between">
            <div className="flex items-center gap-2 px-3 py-2 rounded-md border flex-shrink-0" style={{ backgroundColor: `rgba(${userRgb.r}, ${userRgb.g}, ${userRgb.b}, 0.1)`, borderColor: `rgba(${userRgb.r}, ${userRgb.g}, ${userRgb.b}, 0.4)` }}>
              <Link href={`/user/${user.key}`} onClick={(e) => e.stopPropagation()} className="hover:scale-110 transition-transform">
                <KeyIcon className="w-10 h-10" style={{ color: userColor }} />
              </Link>
              <Link href={`/user/${user.key}`} onClick={(e) => e.stopPropagation()} className="hover:underline">
                <code className="text-lg font-mono font-bold flex" style={{ color: userColor, fontFamily: "'Courier New', 'Consolas', 'Monaco', monospace" }} title={user.key}>
                  {shorten(user.key, 4, 4)}
                </code>
              </Link>
              <CopyButton text={user.key} size="sm" />
            </div>
            
            {/* Refresh Button - Top Right - Only show when not loading */}
            {!isLoading && (
              <button
                onClick={handleRefreshBalance}
                disabled={refreshing}
                className="p-2 rounded-md border hover:bg-white/10 transition-all disabled:opacity-50"
                style={{ backgroundColor: `rgba(${userRgb.r}, ${userRgb.g}, ${userRgb.b}, 0.1)`, borderColor: `rgba(${userRgb.r}, ${userRgb.g}, ${userRgb.b}, 0.4)` }}
                title="Refresh balance"
              >
                <RotateCcw 
                  className={`w-5 h-5 ${refreshing ? 'animate-spin' : ''}`}
                  style={{ color: userColor }}
                />
              </button>
            )}
        </div>
      </div>

      {/* BOTTOM LEVEL - Balance and Mods */}
      <div className="relative z-10 px-4 py-3">
        <div className="flex items-center justify-between gap-3">
          {balance !== undefined && (
            <div className="flex items-center gap-2 px-3 py-1.5 rounded-md border flex-1" style={{ backgroundColor: `rgba(${userRgb.r}, ${userRgb.g}, ${userRgb.b}, 0.08)`, borderColor: `rgba(${userRgb.r}, ${userRgb.g}, ${userRgb.b}, 0.3)` }}>
              <span className="text-sm font-bold uppercase tracking-wide" style={{ color: userColor, fontFamily: "'Courier New', 'Consolas', 'Monaco', monospace" }}>Balance:</span>
              <code className="text-lg font-mono font-bold" style={{ color: userColor, fontFamily: "'Courier New', 'Consolas', 'Monaco', monospace" }}>
                {Number(balance).toFixed(2)}
              </code>
            </div>
          )}
          
          {user.mods && user.mods.length > 0 && (
            <div className="flex items-center gap-2 px-3 py-1.5 rounded-md border flex-1" style={{ backgroundColor: `rgba(${userRgb.r}, ${userRgb.g}, ${userRgb.b}, 0.08)`, borderColor: `rgba(${userRgb.r}, ${userRgb.g}, ${userRgb.b}, 0.3)` }}>
              <span className="text-sm font-bold uppercase tracking-wide" style={{ color: userColor, fontFamily: "'Courier New', 'Consolas', 'Monaco', monospace" }}>Mods:</span>
              <code className="text-lg font-mono font-bold" style={{ color: userColor, fontFamily: "'Courier New', 'Consolas', 'Monaco', monospace" }}>
                {user.mods.length}
              </code>
            </div>
          )}
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