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
import { GlobeAltIcon } from '@heroicons/react/24/outline'

interface ModCardProps {
  mod: ModuleType
  card_enabled?: boolean
}

const buttonColors = {
  module: { bg: 'rgba(139, 92, 246, 0.2)', border: 'rgba(139, 92, 246, 0.6)' },
  network: { bg: 'rgba(34, 197, 94, 0.2)', border: 'rgba(34, 197, 94, 0.6)' },
  cid: { bg: 'rgba(251, 191, 36, 0.2)', border: 'rgba(251, 191, 36, 0.6)' },
  updated: { bg: 'rgba(59, 130, 246, 0.2)', border: 'rgba(59, 130, 246, 0.6)' },
  author: { bg: 'rgba(236, 72, 153, 0.2)', border: 'rgba(236, 72, 153, 0.6)' }
}

export default function ModCard({ mod, card_enabled = true}: ModCardProps) {
  const [isHovered, setIsHovered] = useState(false)
  const [hoveredField, setHoveredField] = useState<string | null>(null)
  const [hoveredValue, setHoveredValue] = useState<string | null>(null)
  const [tooltipPosition, setTooltipPosition] = useState({ x: 0, y: 0 })

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

  const handleFieldHover = (field: string, value: string, e: React.MouseEvent) => {
    setHoveredField(field)
    setHoveredValue(value)
    setTooltipPosition({ x: e.clientX, y: e.clientY })
  }

  const handleFieldLeave = () => {
    setHoveredField(null)
    setHoveredValue(null)
  }

  return (
    <Link href={`/mod/${mod.name}/${mod.key}`}>
      <div 
        className="group relative border-2 rounded-xl px-4 py-3 hover:shadow-xl transition-all duration-300 backdrop-blur-sm overflow-hidden hover:scale-[1.01] bg-black cursor-pointer" 
        style={{ borderColor: borderColor, boxShadow: `0 0 12px ${glowColor}` }}
        onMouseEnter={() => setIsHovered(true)}
        onMouseLeave={() => setIsHovered(false)}
      >
        <div className="absolute -inset-1 bg-gradient-to-r opacity-5 group-hover:opacity-10 blur-lg transition-all duration-500 rounded-xl" style={{ background: `linear-gradient(45deg, ${modColor}, transparent, ${modColor})` }} />
        
        <div className="relative z-10">
          <div className="flex items-center gap-3 justify-between">
            <div 
              className="flex items-center gap-2 min-w-0 flex-shrink transition-all duration-200 rounded-lg px-2 py-1"
              onMouseEnter={(e) => handleFieldHover('Module Name', mod.name, e)}
              onMouseLeave={handleFieldLeave}
              onMouseMove={(e) => setTooltipPosition({ x: e.clientX, y: e.clientY })}
              style={{ backgroundColor: hoveredField === 'Module Name' ? buttonColors.module.bg : 'transparent' }}
            >
              <CubeIcon className="h-10 w-10 flex-shrink-0" style={{ color: '#8b5cf6' }} />
              <code className="text-2xl font-mono font-bold truncate text-white" style={{ fontFamily: "'Courier New', 'Consolas', 'Monaco', monospace" }} title={mod.name}>
                {mod.name}
              </code>
              <CopyButton text={mod.name} size="sm" />
            </div>

            {hasDescription && (
              <div className="relative flex-1 min-w-0 overflow-hidden mx-3" style={{ maxHeight: isHovered ? '120px' : '24px' }}>
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

            <div className="flex items-center gap-2 flex-shrink-0 ml-auto">


              <div 
                className="hidden sm:flex items-center gap-2 px-3 py-2 rounded-md border transition-all duration-200" 
                style={{ backgroundColor: hoveredField === 'Network' ? buttonColors.network.bg : 'rgba(34, 197, 94, 0.08)', borderColor: buttonColors.network.border }}
                onMouseEnter={(e) => handleFieldHover('Network', mod.net, e)}
                onMouseLeave={handleFieldLeave}
                onMouseMove={(e) => setTooltipPosition({ x: e.clientX, y: e.clientY })}
              >
                <GlobeAltIcon className="h-4 w-4" style={{ color: '#22c55e' }} />
                <code className="text-lg font-mono font-bold" style={{ color: '#22c55e', fontFamily: "'Courier New', 'Consolas', 'Monaco', monospace" }} title={`Network: ${mod.net}`}>
                  {mod.net}
                </code>
                <CopyButton text={String(mod.net)} size="sm" />
              </div>

              { mod.cid && (
                <div 
                  className="hidden sm:flex items-center gap-2 px-3 py-2 rounded-md border flex-shrink-0 transition-all duration-200" 
                  style={{ backgroundColor: hoveredField === 'CID' ? buttonColors.cid.bg : 'rgba(251, 191, 36, 0.1)', borderColor: buttonColors.cid.border }}
                  onMouseEnter={(e) => handleFieldHover('CID', mod.cid, e)}
                  onMouseLeave={handleFieldLeave}
                  onMouseMove={(e) => setTooltipPosition({ x: e.clientX, y: e.clientY })}
                >
                  <Hash size={16} strokeWidth={2.5} style={{ color: '#fbbf24' }} />
                  <CopyButton text={mod.cid} size="sm" />
                </div>
              )}

              <div 
                className="hidden sm:flex items-center gap-2 px-3 py-2 rounded-md border transition-all duration-200" 
                style={{ backgroundColor: hoveredField === 'Updated' ? buttonColors.updated.bg : 'rgba(59, 130, 246, 0.08)', borderColor: buttonColors.updated.border }}
                onMouseEnter={(e) => handleFieldHover('Updated', updatedTimeStr, e)}
                onMouseLeave={handleFieldLeave}
                onMouseMove={(e) => setTooltipPosition({ x: e.clientX, y: e.clientY })}
              >
                <Clock size={16} strokeWidth={2.5} style={{ color: '#3b82f6' }} />
                <CopyButton text={updatedTimeStr} size="sm" />
              </div>

              <div 
                className="flex items-center gap-2 px-3 py-2 rounded-md border flex-shrink-0 transition-all duration-200" 
                style={{ backgroundColor: hoveredField === 'Author Key' ? buttonColors.author.bg : 'rgba(236, 72, 153, 0.1)', borderColor: buttonColors.author.border }}
                onMouseEnter={(e) => handleFieldHover('Author Key', mod.key, e)}
                onMouseLeave={handleFieldLeave}
                onMouseMove={(e) => setTooltipPosition({ x: e.clientX, y: e.clientY })}
              >
                <Link href={`/user/${mod.key}`} onClick={(e) => e.stopPropagation()} className="hover:scale-110 transition-transform">
                  <KeyIcon className="w-10 h-10" style={{ color: '#ec4899' }} />
                </Link>
                <CopyButton text={mod.key} size="sm" />
                {myMod && (
                  <div className="hidden sm:flex items-center gap-2 px-3 py-2 rounded-md border" style={{ backgroundColor: `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.08)`, borderColor: `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.3)` }}>
                    me
                  </div>   
                )}
              </div>
            </div>
          </div>
        </div>

        {hoveredField && hoveredValue && (
          <div 
            className="fixed z-50 px-4 py-3 bg-black/95 border-2 rounded-lg shadow-2xl pointer-events-none backdrop-blur-xl"
            style={{ 
              left: `${tooltipPosition.x + 15}px`, 
              top: `${tooltipPosition.y + 15}px`,
              borderColor: `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.6)`,
              boxShadow: `0 0 30px ${glowColor}, 0 0 60px ${glowColor}`
            }}
          >
            <div className="text-xs font-bold text-white/70 mb-1 uppercase tracking-wider" style={{ fontFamily: "'Courier New', 'Consolas', 'Monaco', monospace" }}>
              {hoveredField}
            </div>
            <div className="text-sm font-bold text-white break-all max-w-md" style={{ fontFamily: "'Courier New', 'Consolas', 'Monaco', monospace" }}>
              {hoveredValue}
            </div>
          </div>
        )}

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
