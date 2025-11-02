'use client'

import { useUserContext } from '@/app/block/context/UserContext'
import { WalletIcon, CubeIcon } from '@heroicons/react/24/outline'

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
  const { user, authLoading } = useUserContext()

  if (authLoading || !user) return null

  const balanceColor = text2color('balance')
  const modsColor = text2color('modules')

  return (
    <div className="flex items-center gap-2">
      <div 
        className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-bold border"
        style={{ 
          color: balanceColor, 
          backgroundColor: `${balanceColor}14`, 
          borderColor: `${balanceColor}33` 
        }}
      >
        <WalletIcon className="h-4 w-4" />
        <span>{user.balance || 0}</span>
      </div>
      
      <div 
        className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-bold border"
        style={{ 
          color: modsColor, 
          backgroundColor: `${modsColor}14`, 
          borderColor: `${modsColor}33` 
        }}
      >
        <CubeIcon className="h-4 w-4" />
        <span>{user.mods?.length || 0}</span>
      </div>
    </div>
  )
}