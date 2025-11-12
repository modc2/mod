'use client'

import { ModuleType } from '@/app/types'
import { text2color, shorten, time2str } from '@/app/utils'
import { CopyButton } from '@/app/block/CopyButton'
import { Package, Hash, Clock, KeyIcon, ChevronDown, ChevronUp } from 'lucide-react'
import Link from 'next/link'
import { useState } from 'react'

interface ModCardProps {
  mod: ModuleType
  oneliner?: boolean
}

export default function ModCard({ mod, oneliner = false }: ModCardProps) {
  const [expanded, setExpanded] = useState(false)
  const modColor = text2color(mod.name || mod.key)
  const userColor = text2color(mod.key)
  
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

  if (oneliner && !expanded) {
    return (
      <div 
        className="group relative border rounded-lg px-4 py-2 hover:shadow-lg transition-all duration-200 backdrop-blur-sm cursor-pointer bg-black flex items-center gap-3"
        style={{ borderColor: borderColor, boxShadow: `0 0 8px ${glowColor}` }}
        onClick={() => setExpanded(true)}
      >
        <div className="absolute -inset-1 bg-gradient-to-r opacity-5 group-hover:opacity-10 blur transition-all duration-300 rounded-lg" style={{ background: `linear-gradient(45deg, ${modColor}, transparent, ${modColor})` }} />
        
        <div className="relative z-10 flex items-center gap-3 flex-1 min-w-0">
          <div className="flex-shrink-0 p-1.5 rounded-md border" style={{ backgroundColor: `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.1)`, borderColor: `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.4)` }}>
            <Package size={20} strokeWidth={2.5} style={{ color: modColor }} />
          </div>
          
          <Link href={`/mod/${mod.name}/${mod.key}`} className="flex-1 min-w-0">
            <code className="text-lg font-mono font-bold truncate block" style={{ color: modColor }} title={mod.name}>
              {mod.name}
            </code>
          </Link>

          <div className="flex items-center gap-2 flex-shrink-0">
            <div className="relative flex items-center gap-1.5 px-2.5 py-1.5 rounded-md border" 
              style={{ backgroundColor: `rgba(${userRgb.r}, ${userRgb.g}, ${userRgb.b}, 0.1)`, borderColor: `rgba(${userRgb.r}, ${userRgb.g}, ${userRgb.b}, 0.4)` }}
            >
              <Link href={`/user/${mod.key}`} onClick={(e) => e.stopPropagation()} className="hover:scale-110 transition-transform">
                <KeyIcon size={14} style={{ color: userColor }} />
              </Link>
              <Link href={`/user/${mod.key}`} onClick={(e) => e.stopPropagation()} className="hover:underline">
                <code className="text-xs font-mono font-bold" style={{ color: userColor }} title={mod.key}>
                  {shorten(mod.key, 4, 4)}
                </code>
              </Link>
              <CopyButton text={mod.key} size="sm" />
            </div>
            <ChevronDown size={16} className="text-white/60" />
          </div>
        </div>
      </div>
    )
  }

  return (
    <Link href={`/mod/${mod.name}/${mod.key}`}>
      <div className="group relative border-2 rounded-xl p-4 hover:shadow-xl transition-all duration-300 backdrop-blur-sm overflow-hidden h-full flex flex-col hover:scale-[1.01] bg-black cursor-pointer" style={{ borderColor: borderColor, boxShadow: `0 0 12px ${glowColor}` }}>
        <div className="absolute -inset-1 bg-gradient-to-r opacity-5 group-hover:opacity-10 blur-lg transition-all duration-500 rounded-xl" style={{ background: `linear-gradient(45deg, ${modColor}, transparent, ${modColor})` }} />
        
        <div className="relative z-10 space-y-2.5 flex-1 flex flex-col">
          {oneliner && (
            <button
              onClick={(e) => {
                e.preventDefault()
                e.stopPropagation()
                setExpanded(false)
              }}
              className="absolute top-0 right-0 p-1 rounded-md hover:bg-white/10 transition-all z-10"
            >
              <ChevronUp size={16} className="text-white/60" />
            </button>
          )}

          <div className="flex items-start justify-between gap-2">
            <div className="flex items-center gap-2 group/link flex-1 min-w-0">
              <div className="flex-shrink-0 p-2 rounded-lg border group-hover/link:scale-110 transition-all duration-300" style={{ backgroundColor: `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.1)`, borderColor: `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.4)` }}>
                <Package size={28} strokeWidth={2.5} style={{ color: modColor }} />
              </div>
              <div className="flex-1 min-w-0">
                <code className="text-2xl font-mono font-bold truncate block" style={{ color: modColor }} title={mod.name}>
                  {mod.name}
                </code>
              </div>
            </div>
            
            <div className="flex-shrink-0">
              <div className="flex items-center gap-2 px-3 py-2 rounded-lg border" style={{ backgroundColor: `rgba(${userRgb.r}, ${userRgb.g}, ${userRgb.b}, 0.1)`, borderColor: `rgba(${userRgb.r}, ${userRgb.g}, ${userRgb.b}, 0.4)` }}>
                <Link href={`/user/${mod.key}`} onClick={(e) => e.stopPropagation()} className="hover:scale-110 transition-transform">
                  <KeyIcon size={18} strokeWidth={2.5} style={{ color: userColor }} />
                </Link>
                <Link href={`/user/${mod.key}`} onClick={(e) => e.stopPropagation()} className="hover:underline">
                  <code className="text-sm font-mono font-bold" style={{ color: userColor }} title={mod.key}>
                    {shorten(mod.key, 6, 6)}
                  </code>
                </Link>
                <CopyButton text={mod.key} size="sm" />
              </div>
            </div>
          </div>

          <div className="flex-1 min-w-0 space-y-2">
            {mod.desc && (
              <p className="text-base text-white/60 line-clamp-2">{mod.desc}</p>
            )}

            <div className="flex gap-2">
              <div className="border px-2.5 py-1.5 rounded-lg backdrop-blur-sm flex-1" style={{ backgroundColor: `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.08)`, borderColor: `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.3)` }}>
                <div className="flex items-center gap-1.5 mb-0.5">
                  <Hash size={16} strokeWidth={2.5} style={{ color: modColor }} />
                  <span className="text-sm font-bold uppercase tracking-wide" style={{ color: modColor }}>CID</span>
                </div>
                <div className="flex items-center gap-1.5">
                  <code className="text-base font-mono font-bold truncate flex-1" style={{ color: modColor }} title={mod.cid}>
                    {shorten(mod.cid, 6, 6)}
                  </code>
                  <CopyButton text={mod.cid} size="sm" />
                </div>
              </div>

              <div className="border px-2.5 py-1.5 rounded-lg backdrop-blur-sm flex-1" style={{ backgroundColor: `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.08)`, borderColor: `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.3)` }}>
                <div className="flex items-center gap-1.5 mb-0.5">
                  <Clock size={16} strokeWidth={2.5} style={{ color: modColor }} />
                  <span className="text-sm font-bold uppercase tracking-wide" style={{ color: modColor }}>Updated</span>
                </div>
                <div className="flex items-center gap-1.5">
                  <code className="text-base font-mono font-bold truncate flex-1" style={{ color: modColor }} title={mod.updated}>
                    {time2str(mod.updated)}
                  </code>
                  <CopyButton text={mod.updated} size="sm" />
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </Link>
  )
}
