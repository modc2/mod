'use client'

import { ModuleType } from '@/app/types'
import { text2color, shorten, time2str } from '@/app/utils'
import { CopyButton } from '@/app/block/CopyButton'
import { Package, Hash, Clock, KeyIcon } from 'lucide-react'
import Link from 'next/link'

interface ModCardProps {
  mod: ModuleType
}

export default function ModCard({ mod }: ModCardProps) {
  const modColor = text2color(mod.name || mod.key)
  const userColor = text2color(mod.key)
  
  const hexToRgb = (hex: string) => {
    const result = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex)
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

  return (
    <div className="group relative border-2 rounded-xl p-4 hover:shadow-xl transition-all duration-300 backdrop-blur-sm overflow-hidden h-full flex flex-col hover:scale-[1.01] bg-black" style={{ borderColor: borderColor, boxShadow: `0 0 12px ${glowColor}` }}>
      <div className="absolute -inset-1 bg-gradient-to-r opacity-5 group-hover:opacity-10 blur-lg transition-all duration-500 rounded-xl" style={{ background: `linear-gradient(45deg, ${modColor}, transparent, ${modColor})` }} />
      
      <div className="relative z-10 space-y-2.5 flex-1 flex flex-col">
        <div className="flex items-start justify-between gap-2">
          <Link href={`/mod/${mod.name}/${mod.key}`} className="flex items-center gap-2 group/link flex-1 min-w-0">
            <div className="flex-shrink-0 p-2 rounded-lg border group-hover/link:scale-110 transition-all duration-300" style={{ backgroundColor: `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.1)`, borderColor: `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.4)` }}>
              <Package size={28} strokeWidth={2.5} style={{ color: modColor }} />
            </div>
            <div className="flex-1 min-w-0">
              <div className="border px-2.5 py-1.5 rounded-lg backdrop-blur-sm" style={{ backgroundColor: `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.08)`, borderColor: `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.3)` }}>
                <code className="text-2xl font-mono font-bold truncate block" style={{ color: modColor }} title={mod.name}>
                  {mod.name}
                </code>
              </div>
            </div>
          </Link>
          
          <Link href={`/user/${mod.key}`} className="flex-shrink-0 p-1.5 rounded-lg border hover:scale-110 transition-all duration-300 cursor-pointer" style={{ backgroundColor: `rgba(${userRgb.r}, ${userRgb.g}, ${userRgb.b}, 0.1)`, borderColor: `rgba(${userRgb.r}, ${userRgb.g}, ${userRgb.b}, 0.4)` }} title={`View user: ${shorten(mod.key, 8, 8)}`}>
            <KeyIcon size={20} strokeWidth={2.5} style={{ color: userColor }} />
          </Link>
        </div>

        <Link href={`/mod/${mod.name}/${mod.key}`} className="flex-1 min-w-0 space-y-2">
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
        </Link>
      </div>
    </div>
  )
}
