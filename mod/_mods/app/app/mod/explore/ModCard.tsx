'use client'

import { ModuleType } from '@/app/types'
import { text2color, shorten, time2str } from '@/app/utils'
import { CopyButton } from '@/app/block/ui/CopyButton'
import { Hash, Clock } from 'lucide-react'
import { KeyIcon } from '@heroicons/react/24/outline'
import { CubeIcon } from '@heroicons/react/24/outline'
import Link from 'next/link'
import { useState } from 'react'
import { useUserContext } from '@/app/context'

interface ModCardProps {
  mod: ModuleType
  card_enabled?: boolean
}

export default function ModCard({ mod, card_enabled = true}: ModCardProps) {
  const [isHovered, setIsHovered] = useState(false)

  const modColor = text2color(mod.name || mod.key)
  const userColor = text2color(mod.key)
  const updatedTimeStr = mod.updated ? time2str(mod.updated) : time2str(Date.now())
  
  const { user } = useUserContext()
  const myMod :boolean = user && user.key === mod.key
  const hexToRgb = (hex: string) => {
    const result = /^#?([a-f\\d]{2})([a-f\\d]{2})([a-f\\d]{2})$/i.exec(hex)
    return result ? {
      r: parseInt(result[1], 16),
      g: parseInt(result[2], 16),
      b: parseInt(result[3], 16)
    } : { r: 139, g: 92, b: 246 }
  }
  
  const rgb = hexToRgb(modColor)
  const userRgb = hexToRgb(userColor)
  const borderColor = `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.4)`
  const glowColor = `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.2)`

  const collateral = mod.collateral ?? 0
  const hasDescription = mod.desc && mod.desc.trim().length > 0

  return (
    <Link href={`/mod/${mod.name}/${mod.key}`}>
      <div 
        className="group relative border-2 rounded-xl px-4 py-3 hover:shadow-xl transition-all duration-300 backdrop-blur-sm overflow-hidden hover:scale-[1.01] bg-black cursor-pointer" 
        style={{ borderColor: borderColor, boxShadow: `0 0 12px ${glowColor}` }}
        onMouseEnter={() => setIsHovered(true)}
        onMouseLeave={() => setIsHovered(false)}
      >
        <div className="absolute -inset-1 bg-gradient-to-r opacity-5 group-hover:opacity-10 blur-lg transition-all duration-500 rounded-xl" style={{ background: `linear-gradient(45deg, ${modColor}, transparent, ${modColor})` }} />
        
        {/* Two-level layout for contracted cards */}
        <div className="relative z-10">
          {/* Top level: Name and Key */}
          <div className="flex items-center gap-3 justify-between mb-3">
            <div className="flex items-center gap-2 min-w-0 flex-shrink">
              <CubeIcon className="h-10 w-10 flex-shrink-0" style={{ color: modColor }} />
              <code className="text-2xl font-mono font-bold truncate text-white" style={{ fontFamily: "'Courier New', 'Consolas', 'Monaco', monospace" }} title={mod.name}>
                {mod.name}
              </code>
            </div>

            {/* Author on top right */}

            <div className="flex items-center gap-2 px-3 py-2 rounded-md border flex-shrink-0" style={{ backgroundColor: `rgba(${userRgb.r}, ${userRgb.g}, ${userRgb.b}, 0.1)`, borderColor: `rgba(${userRgb.r}, ${userRgb.g}, ${userRgb.b}, 0.4)` }}>
              {myMod && (
              <div className="hidden sm:flex items-center gap-2 px-3 py-2 rounded-md border" style={{ backgroundColor: `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.08)`, borderColor: `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.3)` }}>
                  {myMod ? 'me' : ''}
              </div>   
            )}
            
              <Link href={`/user/${mod.key}`} onClick={(e) => e.stopPropagation()} className="hover:scale-110 transition-transform">
                <KeyIcon className="w-10 h-10" style={{ color: userColor }} />
              </Link>
              <Link href={`/user/${mod.key}`} onClick={(e) => e.stopPropagation()} className="hover:underline">
                <code className="text-lg font-mono font-bold" style={{ color: userColor, fontFamily: "'Courier New', 'Consolas', 'Monaco', monospace" }} title={mod.key}>
                  {shorten(mod.key, 2, 2)}
                </code>
              </Link>
              <CopyButton text={mod.key} size="sm" />
            </div>
          </div>

          {/* Bottom level: Description and metadata */}
          <div className="flex items-center gap-3 justify-between">
            {/* Description - scrollable on hover if too long, only show if exists */}
            {hasDescription && (
              <div className="relative flex-1 min-w-0 overflow-hidden" style={{ maxHeight: isHovered ? '120px' : '24px' }}>
                <div 
                  className="text-sm text-white/60 transition-all duration-300"
                  style={{ 
                    fontFamily: "'Courier New', 'Consolas', 'Monaco', monospace",
                    overflowY: isHovered && mod.desc.length > 100 ? 'auto' : 'hidden',
                    whiteSpace: isHovered ? 'normal' : 'nowrap',
                    textOverflow: isHovered ? 'clip' : 'ellipsis',
                    paddingRight: isHovered ? '8px' : '0'
                  }}
                >
                  {mod.desc}
                </div>
              </div>
            )}

            {/* Metadata fields */}
            <div className="flex items-center gap-2 flex-shrink-0">
              {!card_enabled && mod.cid && (
                <div className="hidden sm:flex items-center gap-2 px-3 py-2 rounded-md border flex-shrink-0" style={{ backgroundColor: `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.1)`, borderColor: `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.4)` }}>
                  <Hash size={16} strokeWidth={2.5} style={{ color: modColor }} />
                  <code className="text-lg font-mono font-bold" style={{ color: modColor, fontFamily: "'Courier New', 'Consolas', 'Monaco', monospace" }} title={mod.cid}>
                    {shorten(mod.cid, 4, 4)}
                  </code>
                  <CopyButton text={mod.cid} size="sm" />
                </div>
              )}

              {/* Collateral */}
              <div className="hidden sm:flex items-center gap-2 px-3 py-2 rounded-md border" style={{ backgroundColor: `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.08)`, borderColor: `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.3)` }}>
                <span className="text-lg" title="Collateral">🪙</span>
                <code className="text-lg font-mono font-bold" style={{ color: modColor, fontFamily: "'Courier New', 'Consolas', 'Monaco', monospace" }} title={`Collateral: ${collateral}`}>
                  {collateral}
                </code>
                <CopyButton text={String(collateral)} size="sm" />
              </div>

              {/* Updated */}
              <div className="hidden sm:flex items-center gap-2 px-3 py-2 rounded-md border" style={{ backgroundColor: `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.08)`, borderColor: `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.3)` }}>
                <Clock size={16} strokeWidth={2.5} style={{ color: modColor }} />
                <code className="text-lg font-mono font-bold" style={{ color: modColor, fontFamily: "'Courier New', 'Consolas', 'Monaco', monospace" }} title={updatedTimeStr}>
                  {updatedTimeStr}
                </code>
                <CopyButton text={updatedTimeStr} size="sm" />
              </div>
              {/* MyMod */}

            </div>
          </div>
        </div>

        <style jsx>{`
          div::-webkit-scrollbar {
            width: 4px;
          }
          div::-webkit-scrollbar-track {
            background: rgba(255, 255, 255, 0.05);
            border-radius: 2px;
          }
          div::-webkit-scrollbar-thumb {
            background: rgba(255, 255, 255, 0.2);
            border-radius: 2px;
          }
          div::-webkit-scrollbar-thumb:hover {
            background: rgba(255, 255, 255, 0.3);
          }
        `}</style>
      </div>
    </Link>
  )
}
