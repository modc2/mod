'use client'

import { useState } from 'react'
import { CopyButton } from '@/app/block/CopyButton'
import { User, Coins, Package } from 'lucide-react'
import { text2color, shorten } from '@/app/utils'

interface KeyCardProps {
  keyAddress: string
  balance?: number
  moduleCount?: number
}

export default function KeyCard({ keyAddress, balance = 0, moduleCount = 0 }: KeyCardProps) {
  const [isHovered, setIsHovered] = useState(false)
  const keyColor = text2color(keyAddress)
  
  const hexToRgb = (hex: string) => {
    const result = /^#?([a-f\\d]{2})([a-f\\d]{2})([a-f\\d]{2})$/i.exec(hex)
    return result ? {
      r: parseInt(result[1], 16),
      g: parseInt(result[2], 16),
      b: parseInt(result[3], 16)
    } : { r: 139, g: 92, b: 246 }
  }
  
  const rgb = hexToRgb(keyColor)
  const bgColor = `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.15)`
  const borderColor = `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.4)`
  const hoverBgColor = `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.25)`

  return (
    <div 
      className="relative group transition-all duration-300"
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
    >
      <div 
        className="border-2 rounded-2xl p-6 backdrop-blur-sm transition-all duration-300 hover:shadow-2xl"
        style={{
          backgroundColor: isHovered ? hoverBgColor : bgColor,
          borderColor: borderColor,
          boxShadow: isHovered ? `0 20px 60px ${bgColor}` : 'none'
        }}
      >
        <div className="flex items-center gap-4">
          <div 
            className="flex-shrink-0 p-4 rounded-xl border-2 transition-all duration-300 group-hover:scale-110 group-hover:rotate-6 shadow-xl"
            style={{
              backgroundColor: `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.2)`,
              borderColor: `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.5)`
            }}
          >
            <User size={40} strokeWidth={2.5} style={{ color: keyColor }} />
          </div>
          
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-3 mb-2">
              <span className="text-xs text-white/60 font-bold uppercase tracking-wider">KEY ADDRESS</span>
            </div>
            <div className="flex items-center gap-2">
              <code 
                className="text-lg font-mono font-bold truncate"
                style={{ color: keyColor }}
                title={keyAddress}
              >
                {shorten(keyAddress, 16)}
              </code>
              <CopyButton text={keyAddress} />
            </div>
          </div>
        </div>

        <div className="mt-4 pt-4 border-t-2 space-y-3" style={{ borderColor: borderColor }}>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Coins size={20} strokeWidth={2.5} style={{ color: keyColor }} />
              <span className="text-sm text-white/70 font-bold uppercase tracking-wide">Balance</span>
            </div>
            <span 
              className="text-xl font-black"
              style={{ color: keyColor }}
            >
              {balance.toLocaleString()}
            </span>
          </div>
          
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Package size={20} strokeWidth={2.5} style={{ color: keyColor }} />
              <span className="text-sm text-white/70 font-bold uppercase tracking-wide">Modules</span>
            </div>
            <span 
              className="text-xl font-black"
              style={{ color: keyColor }}
            >
              {moduleCount}
            </span>
          </div>
        </div>
      </div>
    </div>
  )
}
