import { HomeIcon } from '@heroicons/react/24/outline'

import { ModuleType } from '@/bloc/types'
import { text2color, shorten, time2str } from '@/bloc/utils'
import { CopyButton } from '@/bloc/ui/CopyButton'
import { Hash, Clock, Settings } from 'lucide-react'
import { KeyIcon } from '@heroicons/react/24/outline'
import { CubeIcon } from '@heroicons/react/24/outline'
import Link from 'next/link'
import { useState, useEffect } from 'react'
import { createPortal } from 'react-dom'
import { useUserContext } from '@/bloc/context'
import { GlobeAltIcon } from '@heroicons/react/24/outline'
import ModAdminPanel from './ModAdminPanel'

interface ModCardProps {
  mod: ModuleType
  card_enabled?: boolean
}

export default function ModCard({ mod}: ModCardProps) {
  const [isHovered, setIsHovered] = useState(false)
  const [hoveredField, setHoveredField] = useState<string | null>(null)
  const [tooltipPosition, setTooltipPosition] = useState({ x: 0, y: 0 })
  const [mounted, setMounted] = useState(false)
  const [showAdminPanel, setShowAdminPanel] = useState(false)
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
      case 'name': return mod.name
      case 'network': return mod.network
      case 'cid': return mod.cid || ''
      case 'updated': return updatedTimeStr
      case 'author': return mod.key
      default: return ''
    }
  }

    return (
      <>
        <Link href={`/mod/${mod.name}/${mod.key}`}>
            <div 
              className="group relative border-2 rounded-xl px-4 py-3 hover:shadow-xl transition-all duration-300 backdrop-blur-sm hover:scale-[1.01] bg-black cursor-pointer" 
              style={{ borderColor: borderColor, boxShadow: `0 0 12px ${glowColor}`, minHeight: 'fit-content' }}
              onMouseEnter={() => setIsHovered(true)}
              onMouseLeave={() => setIsHovered(false)}
            >
            <div className="absolute -inset-1 bg-gradient-to-r opacity-5 group-hover:opacity-10 blur-lg transition-all duration-500 rounded-xl" style={{ background: `linear-gradient(45deg, ${modColor}, transparent, ${modColor})` }} />
            
              <div className="relative z-10">
                <div className="flex flex-col gap-3">
                  <div className="flex items-center gap-2 px-3 py-2 rounded-md border" style={{ backgroundColor: `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.1)`, borderColor: `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.4)` }}>
                    <CubeIcon className="w-10 h-10" style={{ color: modColor }} />
                    <code className="text-lg font-mono font-bold" style={{ color: modColor, fontFamily: "'Courier New', 'Consolas', 'Monaco', monospace", minWidth: '120px', display: 'inline-block' }} title={mod.name}>
                      {mod.name}
                    </code>
                    <CopyButton text={mod.name} size="sm" />
                    {myMod && (
                      <button
                        onClick={(e) => {
                          e.preventDefault()
                          e.stopPropagation()
                          setShowAdminPanel(true)
                        }}
                        className="ml-auto p-2 hover:bg-purple-500/20 rounded-lg transition-colors"
                        title="Admin Settings"
                      >
                        <Settings className="w-5 h-5 text-purple-400" />
                      </button>
                    )}
                  </div>
                  
                  <div className="flex flex-wrap items-center gap-2">
                  <div 
                    className="flex items-center gap-2 px-3 py-2 rounded-md border" 
                    style={{ backgroundColor: `rgba(${userRgb.r}, ${userRgb.g}, ${userRgb.b}, 0.08)`, borderColor: `rgba(${userRgb.r}, ${userRgb.g}, ${userRgb.b}, 0.3)` }}
                    onMouseEnter={(e) => handleFieldHover('network', mod.network, e)}
                    onMouseMove={handleFieldMove}
                    onMouseLeave={handleFieldLeave}
                  >
                    {networkLogo}
                    <CopyButton text={String(mod.network)} size="sm" />
                  </div>

                  { mod.cid && (
                    <div 
                      className="flex items-center gap-2 px-3 py-2 rounded-md border" 
                      style={{ backgroundColor: 'rgba(251, 191, 36, 0.08)', borderColor: 'rgba(251, 191, 36, 0.3)' }}
                      onMouseEnter={(e) => handleFieldHover('cid', mod.cid, e)}
                      onMouseMove={handleFieldMove}
                      onMouseLeave={handleFieldLeave}
                    >
                      <Hash size={16} style={{ color: '#fbbf24' }} />
                      <CopyButton text={mod.cid} size="sm" />
                    </div>
                  )}

                  <div 
                    className="flex items-center gap-2 px-3 py-2 rounded-md border" 
                    style={{ backgroundColor: 'rgba(59, 130, 246, 0.08)', borderColor: 'rgba(59, 130, 246, 0.3)' }}
                    onMouseEnter={(e) => handleFieldHover('updated', updatedTimeStr, e)}
                    onMouseMove={handleFieldMove}
                    onMouseLeave={handleFieldLeave}
                  >
                    <Clock size={16} style={{ color: '#3b82f6' }} />
                    <CopyButton text={updatedTimeStr} size="sm" />
                  </div>

                  <div 
                    className="flex items-center gap-2 px-3 py-2 rounded-md border" 
                    style={{ backgroundColor: 'rgba(236, 72, 153, 0.08)', borderColor: 'rgba(236, 72, 153, 0.3)' }}
                       onMouseEnter={(e) => handleFieldHover('author', mod.key, e)}
                      onMouseMove={handleFieldMove}
                      onMouseLeave={handleFieldLeave}
                      title={mod.key}
                    >
                      <Link href={`/user/${mod.key}`} onClick={(e) => e.stopPropagation()} className="hover:scale-110 transition-transform">
                        <KeyIcon className="w-5 h-5" style={{ color: '#ec4899' }} />
                      </Link>
                      <CopyButton text={mod.key} size="sm" />
                  </div>
                  </div>
                </div>
              </div>
          </div>
        </Link>
        
        {mounted && hoveredField && createPortal(
          <div 
            className="fixed z-[9999] px-3 py-2 bg-black/95 border-2 rounded-lg text-white text-sm font-mono pointer-events-none backdrop-blur-xl shadow-2xl"
            style={{ 
              left: `${tooltipPosition.x + 15}px`, 
              top: `${tooltipPosition.y + 15}px`,
              borderColor: hoveredField === 'network' ? `rgba(${userRgb.r}, ${userRgb.g}, ${userRgb.b}, 0.6)` :
                          hoveredField === 'cid' ? 'rgba(251, 191, 36, 0.6)' :
                          hoveredField === 'updated' ? 'rgba(59, 130, 246, 0.6)' :
                          hoveredField === 'author' ? 'rgba(236, 72, 153, 0.6)' : 'rgba(139, 92, 246, 0.6)',
              maxWidth: '400px',
              wordBreak: 'break-all'
            }}
          >
            <div className="font-bold mb-1 uppercase text-xs" style={{
              color: hoveredField === 'network' ? userColor :
                     hoveredField === 'cid' ? '#fbbf24' :
                     hoveredField === 'updated' ? '#3b82f6' :
                     hoveredField === 'author' ? '#ec4899' : modColor
            }}>
              {hoveredField}
            </div>
            <div>{getFieldValue(hoveredField)}</div>
          </div>,
          document.body
        )}

        {showAdminPanel && myMod && (
          <ModAdminPanel mod={mod} onClose={() => setShowAdminPanel(false)} />
        )}
      </>
    )
}
