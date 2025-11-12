'use client'
import { text2color, shorten } from '@/app/utils'
import { CopyButton } from '@/app/block/CopyButton'
// import { KeyIcon } from 'lucide-react'
import { KeyIcon } from '@heroicons/react/24/outline'

import { UserType } from '@/app/types'
import Link from 'next/link'

interface UserCardProps {
  user: UserType
  mode?: 'explore' | 'page'
}

export const UserCard = ({ user, mode = 'explore' }: UserCardProps) => {
  const userColor = text2color(user.key)
  
  const hexToRgb = (hex: string) => {
    const result = /^#?([a-f\\d]{2})([a-f\\d]{2})([a-f\\d]{2})$/i.exec(hex)
    return result ? {
      r: parseInt(result[1], 16),
      g: parseInt(result[2], 16),
      b: parseInt(result[3], 16)
    } : { r: 139, g: 92, b: 246 }
  }
  
  const rgb = hexToRgb(userColor)
  const borderColor = `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.4)`
  const glowColor = `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.2)`

  const CardContent = () => (
    <div className="group relative border rounded-lg px-4 py-3 hover:shadow-lg transition-all duration-200 backdrop-blur-sm bg-black" style={{ borderColor: borderColor, boxShadow: `0 0 8px ${glowColor}` }}>
      <div className="absolute -inset-1 bg-gradient-to-r opacity-5 group-hover:opacity-10 blur transition-all duration-300 rounded-lg" style={{ background: `linear-gradient(45deg, ${userColor}, transparent, ${userColor})` }} />
      
      <div className="relative z-10 flex items-center gap-3">
        <div className="flex-shrink-0 p-2 rounded-md border" style={{ backgroundColor: `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.1)`, borderColor: `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.4)` }}>
          <KeyIcon className="w-9 h-9" style={{ color: userColor }} />
        </div>
        
        <div className="flex items-center gap-2">
          <code className="text-xl font-mono font-bold" style={{ color: userColor }} title={user.key}>
            {shorten(user.key, 6, 6)}
          </code>
          <CopyButton text={user.key} size="sm" />
        </div>
        
        {user.crypto_type && (
          <div className="flex items-center gap-2 px-3 py-1.5 rounded-md border" style={{ backgroundColor: `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.08)`, borderColor: `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.3)` }}>
            <span className="text-sm font-bold uppercase tracking-wide" style={{ color: userColor }}>Crypto:</span>
            <code className="text-lg font-mono font-bold" style={{ color: userColor }}>
              {user.crypto_type}
            </code>
          </div>
        )}
        
        {user.balance !== undefined && (
          <div className="flex items-center gap-2 px-3 py-1.5 rounded-md border" style={{ backgroundColor: `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.08)`, borderColor: `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.3)` }}>
            <span className="text-sm font-bold uppercase tracking-wide" style={{ color: userColor }}>Balance:</span>
            <code className="text-lg font-mono font-bold" style={{ color: userColor }}>
              {user.balance}
            </code>
          </div>
        )}
        
        {user.mods && user.mods.length > 0 && (
          <div className="flex items-center gap-2 px-3 py-1.5 rounded-md border" style={{ backgroundColor: `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.08)`, borderColor: `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.3)` }}>
            <span className="text-sm font-bold uppercase tracking-wide" style={{ color: userColor }}>Mods:</span>
            <code className="text-lg font-mono font-bold" style={{ color: userColor }}>
              {user.mods.length}
            </code>
          </div>
        )}
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