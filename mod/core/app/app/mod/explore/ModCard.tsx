
'use client'

import { ModuleType } from '@/app/types'
import { text2color, shorten, time2str } from '@/app/utils'
import { CopyButton } from '@/app/block/ui/CopyButton'
import { Hash, Clock } from 'lucide-react'
import { KeyIcon } from '@heroicons/react/24/outline'
import { CubeIcon } from '@heroicons/react/24/outline'
import Link from 'next/link'
import { useState, useEffect } from 'react' // Added useEffect
import { createPortal } from 'react-dom' // Added createPortal
import { useUserContext } from '@/app/context'
import { GlobeAltIcon } from '@heroicons/react/24/outline'
// i need a local network icon maybe a home icon or something

// Define the props interface for ModCard
import { HomeIcon } from '@heroicons/react/24/outline'
// do a chain icon for the network

interface ModCardProps {
  mod: ModuleType
  card_enabled?: boolean
}

export default function ModCard({ mod}: ModCardProps) {
  const [isHovered, setIsHovered] = useState(false)
  const [hoveredField, setHoveredField] = useState<string | null>(null)
  const [tooltipPosition, setTooltipPosition] = useState({ x: 0, y: 0 })
  // State to ensure we only use portal on the client side
  const [mounted, setMounted] = useState(false)

  useEffect(() => {
    setMounted(true)
  }, [])





  const modColor = text2color(mod.name || mod.key)
  const userColor = text2color(mod.key)
  const updatedTimeStr = mod.updated ? time2str(mod.updated) : time2str(Date.now())
  
  const { user } = useUserContext()
  const networkColor = mod.network === 'local' ? '#7b740dff' : '#0eafb2ff'

  const network2logocomponent = {
    'local': <HomeIcon className="h-4 w-4" style={{ color: networkColor }} />,
    'mainnet': <GlobeAltIcon className="h-4 w-4" style={{ color: networkColor }} />,
    'testnet': <GlobeAltIcon className="h-4 w-4" style={{ color: networkColor }} />,
    'devnet': <GlobeAltIcon className="h-4 w-4" style={{ color: networkColor }} />
  }
  const networkLogo = network2logocomponent[mod.network] || <GlobeAltIcon className="h-4 w-4" style={{ color: networkColor }} />
  const buttonColors = {
    module: { bg: 'rgba(139, 92, 246, 0.2)', border: 'rgba(139, 92, 246, 0.6)' },
    network: mod.network === 'local' ? { bg: '#978e097d', border:  '#7b740dff'} : { bg: 'rgba(0, 128, 128, 0.2)', border: 'rgba(0, 128, 128, 0.6)' },
    cid: { bg: 'rgba(251, 191, 36, 0.2)', border: 'rgba(251, 191, 36, 0.6)' } ,
    updated: { bg: 'rgba(59, 130, 246, 0.2)', border: 'rgba(59, 130, 246, 0.6)' },
    author: { bg: 'rgba(236, 72, 153, 0.2)', border: 'rgba(236, 72, 153, 0.6)' }
  }
  const myMod :boolean = user && user.key === mod.key
  const hexToRgb = (hex: string) => {
    const result = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex)
    return result ? {
      r: parseInt(result[1], 16),
      g: parseInt(result[2], 16),
      b: parseInt(result[3], 16)
    } : { r: 139, g: 92, b: 246 }
  }
  
  const rgb = hexToRgb(modColor)
  const borderColor = `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.4)`
  const glowColor = `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.2)`

  const hasDescription = mod.desc && mod.desc.trim().length > 0

  const handleFieldHover = (field: string, value: string, e: React.MouseEvent) => {
    setHoveredField(field)
    setTooltipPosition({ x: e.clientX, y: e.clientY })
  }

  const handleFieldMove = (e: React.MouseEvent) => {
    setTooltipPosition({ x: e.clientX, y: e.clientY })
  }

  const handleFieldLeave = () => {
    setHoveredField(null)
  }

  const getFieldValue = (field: string): string => {
    switch(field) {
      case 'Name': return mod.name
      case 'Network': return mod.network
      case 'CID': return mod.cid || ''
      case 'Updated': return updatedTimeStr
      case 'Author Key': return mod.key
      default: return ''
    }
  }

  return (
    <>
      <Link href={`/mod/${mod.name}/${mod.key}`}>
        <div 
          className="group relative border-2 rounded-xl px-4 py-3 hover:shadow-xl transition-all duration-300 backdrop-blur-sm hover:scale-[1.01] bg-black cursor-pointer" 
          style={{ borderColor: borderColor, boxShadow: `0 0 12px ${glowColor}`, zIndex: isHovered ? 50 : 1, position: 'relative' }}
          onMouseEnter={() => setIsHovered(true)}
          onMouseLeave={() => setIsHovered(false)}
        >
          <div className="absolute -inset-1 bg-gradient-to-r opacity-5 group-hover:opacity-10 blur-lg transition-all duration-500 rounded-xl" style={{ background: `linear-gradient(45deg, ${modColor}, transparent, ${modColor})` }} />
          
          <div className="relative z-10">
            <div className="flex items-center gap-1 justify-between">
              <div 
                className="flex items-center gap-2 min-w-0 flex-shrink transition-all duration-200 rounded-lg px-2 py-1"
                onMouseEnter={(e) => handleFieldHover('Name', mod.name, e)}
                onMouseLeave={handleFieldLeave}
                onMouseMove={handleFieldMove}
                style={{ backgroundColor: hoveredField === 'Name' ? buttonColors.module.bg : 'transparent' }}
              >
                <code className="text-l font-mono font-bold truncate text-white" style={{ fontFamily: "'Courier New', 'Consolas', 'Monaco', monospace" }} title={mod.name}>
                  {mod.name}
                </code>
                <CopyButton text={mod.name} size="sm" />
              </div>

              {/* {hasDescription && (
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
              )} */}

              <div className="flex items-center gap-2 flex-shrink-0 ml-auto">
                <div 
                  className="hidden sm:flex items-center gap-2 px-3 py-2 rounded-md border transition-all duration-200" 
                  style={{ backgroundColor: hoveredField === 'Network' ? buttonColors.network.bg : 'rgba(14, 175, 178, 0.08)', borderColor: buttonColors.network.border }}
                  onMouseEnter={(e) => handleFieldHover('Network', mod.network, e)}
                  onMouseLeave={handleFieldLeave}
                  onMouseMove={handleFieldMove}
                >
                  {networkLogo}
                  <CopyButton text={String(mod.network)} size="sm" />
                </div>

                { mod.cid && (
                  <div 
                    className="hidden sm:flex items-center gap-2 px-3 py-2 rounded-md border flex-shrink-0 transition-all duration-200" 
                    style={{ backgroundColor: hoveredField === 'CID' ? buttonColors.cid.bg : 'rgba(251, 191, 36, 0.1)', borderColor: buttonColors.cid.border }}
                    onMouseEnter={(e) => handleFieldHover('CID', mod.cid, e)}
                    onMouseLeave={handleFieldLeave}
                    onMouseMove={handleFieldMove}
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
                  onMouseMove={handleFieldMove}
                >
                  <Clock size={16} strokeWidth={2.5} style={{ color: '#3b82f6' }} />
                  <CopyButton text={updatedTimeStr} size="sm" />
                </div>

                <div 
                  className="flex items-center gap-2 px-3 py-2 rounded-md border flex-shrink-0 transition-all duration-200" 
                  style={{ backgroundColor: hoveredField === 'Author Key' ? buttonColors.author.bg : 'rgba(236, 72, 153, 0.1)', borderColor: buttonColors.author.border }}
                  onMouseEnter={(e) => handleFieldHover('Author Key', mod.key, e)}
                  onMouseLeave={handleFieldLeave}
                  onMouseMove={handleFieldMove}
                >
                  <Link href={`/user/${mod.key}`} onClick={(e) => e.stopPropagation()} className="hover:scale-110 transition-transform">
                    <KeyIcon className="w-5 h-5" style={{ color: '#ec4899' }} />
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

      {/* Moving the tooltip render to a Portal targeting the body */}
      {mounted && hoveredField && createPortal(
        <div 
          className="fixed pointer-events-none backdrop-blur-xl"
          style={{ 
            left: `${tooltipPosition.x}px`, 
            top: `${tooltipPosition.y}px`,
            padding: '12px 16px',
            backgroundColor: 'rgba(0, 0, 0, 0.95)',
            border: `2px solid rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.6)`,
            borderRadius: '8px',
            boxShadow: `0 0 30px ${glowColor}, 0 0 60px ${glowColor}`,
            zIndex: 99999
          }}
        >
          <div className="text-xs font-bold text-white/70 mb-1 uppercase tracking-wider" style={{ fontFamily: "'Courier New', 'Consolas', 'Monaco', monospace" }}>
            {hoveredField}
          </div>
          <div className="text-sm font-bold text-white break-all max-w-md" style={{ fontFamily: "'Courier New', 'Consolas', 'Monaco', monospace" }}>
            {getFieldValue(hoveredField)}
          </div>
        </div>,
        document.body
      )}
    </>
  )
}
