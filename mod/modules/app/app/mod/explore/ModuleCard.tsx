'use client'

import { ModuleType } from '@/app/types'
import { CopyButton } from '@/app/block/CopyButton'
import Link from 'next/link'
import { Package, User, Clock, Hash, Coins } from 'lucide-react'
import { shorten, text2color } from "@/app/utils"

interface ModuleCardProps {
  mod: ModuleType
}

function getTimeAgo(timestamp: number): string {
  const now = Math.floor(Date.now() / 1000)
  const seconds = now - timestamp
  
  if (seconds < 60) return `${seconds}s ago`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`
  if (seconds < 2592000) return `${Math.floor(seconds / 86400)}d ago`
  if (seconds < 31536000) return `${Math.floor(seconds / 2592000)}mo ago`
  return `${Math.floor(seconds / 31536000)}y ago`
}

export default function ModuleCard({ mod }: ModuleCardProps) {
  const moduleColor = text2color(mod.name)
  const userColor = text2color(mod.key)
  
  const hexToRgb = (hex: string) => {
    const result = /^#?([a-f\\d]{2})([a-f\\d]{2})([a-f\\d]{2})$/i.exec(hex)
    return result ? {
      r: parseInt(result[1], 16),
      g: parseInt(result[2], 16),
      b: parseInt(result[3], 16)
    } : { r: 139, g: 92, b: 246 }
  }
  
  const rgb = hexToRgb(moduleColor)
  const bgColor = `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.1)`
  const borderColor = moduleColor
  const hoverBgColor = `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.2)`
  const userRgb = hexToRgb(userColor)

  return (
    <Link
      href={`/mod/${mod.name}/${mod.key}`}
      className="group relative border-2 rounded-2xl p-6 backdrop-blur-md transition-all duration-300 hover:shadow-2xl hover:scale-[1.02] block"
      style={{
        backgroundColor: bgColor,
        borderColor: moduleColor,
        boxShadow: `0 0 20px ${moduleColor}20`
      }}
    >
      <div className="flex items-start justify-between gap-4 mb-5">
        <div className="flex items-center gap-3 flex-1 min-w-0">
          <div 
            className="flex-shrink-0 p-3 rounded-xl border-2 transition-all duration-300 group-hover:scale-110 group-hover:rotate-6 shadow-lg"
            style={{
              backgroundColor: `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.2)`,
              borderColor: moduleColor
            }}
          >
            <Package size={32} strokeWidth={2.5} style={{ color: moduleColor }} />
          </div>
          
          <div className="flex-1 min-w-0">
            <h3 
              className="text-2xl font-black lowercase tracking-wide truncate mb-1"
              style={{ color: moduleColor }}
              title={mod.name}
            >
              {mod.name}
            </h3>
            <div className="flex items-center gap-2">
              <span className="text-xs text-white/50 font-bold lowercase tracking-wide">module</span>
            </div>
          </div>
        </div>

        <Link 
          href={`/user/${mod.key}`}
          className="relative flex-shrink-0 p-3 rounded-xl border-2 transition-all duration-300 hover:scale-110 group/user shadow-lg"
          style={{
            backgroundColor: `rgba(${userRgb.r}, ${userRgb.g}, ${userRgb.b}, 0.2)`,
            borderColor: userColor
          }}
          title={`view user profile: ${mod.key}`}
          onClick={(e) => e.stopPropagation()}
        >
          <User size={24} strokeWidth={2.5} style={{ color: userColor }} />
        </Link>
      </div>

      {mod.desc && (
        <p className="text-white/70 text-sm mb-5 line-clamp-2 leading-relaxed">
          {mod.desc}
        </p>
      )}

      <div className="space-y-3">
        {mod.balance !== undefined && (
          <div 
            className="flex items-center gap-2 border-2 px-4 py-3 rounded-xl backdrop-blur-sm transition-all hover:bg-opacity-80" 
            style={{ 
              borderColor: `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.4)`,
              backgroundColor: `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.08)`
            }}
          >
            <Coins size={16} strokeWidth={2.5} style={{ color: moduleColor }} />
            <span className="text-xs text-white/50 font-bold lowercase tracking-wide">balance:</span>
            <span className="text-xs font-bold flex-1" style={{ color: moduleColor }}>
              {mod.balance.toLocaleString()}
            </span>
          </div>
        )}

        {mod.content && (
          <div 
            className="flex items-center gap-2 border-2 px-4 py-3 rounded-xl backdrop-blur-sm transition-all hover:bg-opacity-80" 
            style={{ 
              borderColor: `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.4)`,
              backgroundColor: `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.08)`
            }}
          >
            <Hash size={16} strokeWidth={2.5} style={{ color: moduleColor }} />
            <span className="text-xs text-white/50 font-bold lowercase tracking-wide">cid:</span>
            <code className="text-xs font-mono font-bold flex-1 truncate" style={{ color: moduleColor }} title={mod.content}>
              {shorten(mod.content, 12)}
            </code>
            <CopyButton text={mod.content} size="sm" />
          </div>
        )}

        {mod.updated && (
          <div 
            className="flex items-center gap-2 border-2 px-4 py-3 rounded-xl backdrop-blur-sm transition-all hover:bg-opacity-80" 
            style={{ 
              borderColor: `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.4)`,
              backgroundColor: `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.08)`
            }}
          >
            <Clock size={16} strokeWidth={2.5} style={{ color: moduleColor }} />
            <span className="text-xs text-white/50 font-bold lowercase tracking-wide">updated:</span>
            <span className="text-xs font-bold" style={{ color: moduleColor }}>
              {new Date(mod.updated * 1000).toLocaleDateString()} ({getTimeAgo(mod.updated)})
            </span>
          </div>
        )}
      </div>
    </Link>
  )
}
