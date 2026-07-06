"use client";
import { text2color, shorten, time2str, timeAgo, colorWithOpacity } from '@/utils'
import { KeyIcon, QrCodeIcon } from '@heroicons/react/24/outline'
import { ModuleType } from '@/types'
import Link from 'next/link'
import { CopyButton } from '@/ui/CopyButton'
import { Lock, ChevronDown } from 'lucide-react'
import { useState, useEffect, useRef } from 'react'
import { QRCode } from '@/ui/QRCode'
import { userContext } from '@/context'

interface ModCardProps {
  mod: ModuleType
  card_enabled?: boolean
  compact?: boolean
  allVersions?: ModuleType[]
  onVersionSelect?: (modKey: string) => void
  historicalVersions?: any[]
  selectedHistoricalIndex?: number
  onHistoricalVersionChange?: (index: number) => void
}

const TERM_FONT = "var(--font-digital), 'JetBrains Mono', 'Courier New', monospace"

export default function ModCard({
  mod,
  card_enabled = true,
  compact = false,
  allVersions = [],
  onVersionSelect,
  historicalVersions = [],
  selectedHistoricalIndex = 0,
  onHistoricalVersionChange
}: ModCardProps) {
  const [isNameQrHovered, setIsNameQrHovered] = useState(false)
  const [isHovered, setIsHovered] = useState(false)
  const [ownerDropdownOpen, setOwnerDropdownOpen] = useState(false)
  const [versionDropdownOpen, setVersionDropdownOpen] = useState(false)
  const ownerDropdownRef = useRef<HTMLDivElement>(null)
  const versionDropdownRef = useRef<HTMLDivElement>(null)

  const modColor = text2color(mod.name || mod.key)
  const updatedTimeStr = mod.updated ? time2str(mod.updated * 1000) : time2str(Date.now())
  const agoStr = mod.updated ? timeAgo(mod.updated * 1000) : ''
  const websiteUrl = typeof window !== 'undefined' ? `${window.location.origin}/${mod.name}` : ''
  const fnCount = mod.schema ? Object.keys(mod.schema).length : 0

  const isExpanded = !card_enabled && !compact

  const currentVersionIndex = allVersions.findIndex(v => v.key === mod.key)
  const hasMultipleOwners = allVersions.length > 1
  const hasHistoricalVersions = historicalVersions.length > 0

  const handleOwnerSelect = (ownerKey: string) => {
    if (onVersionSelect) onVersionSelect(ownerKey)
    setOwnerDropdownOpen(false)
  }

  const handleHistoricalSelect = (index: number) => {
    if (onHistoricalVersionChange) onHistoricalVersionChange(index)
    setVersionDropdownOpen(false)
  }

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (ownerDropdownRef.current && !ownerDropdownRef.current.contains(event.target as Node)) {
        setOwnerDropdownOpen(false)
      }
      if (versionDropdownRef.current && !versionDropdownRef.current.contains(event.target as Node)) {
        setVersionDropdownOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  // Compact single-line layout for expanded page header
  if (compact) {
    return (
      <div
        className="relative overflow-visible group flex items-center gap-4"
        style={{ fontFamily: TERM_FONT }}
      >
        <div className="flex items-center gap-3">
          <code
            className="tracking-wider text-2xl"
            style={{ color: 'var(--text-primary)', fontFamily: TERM_FONT, textShadow: `0 0 12px ${colorWithOpacity(modColor, 0.4)}` }}
          >
            {mod.name?.toLowerCase()}
          </code>
          <CopyButton text={mod.name} size="sm" showValueOnHover={true} />
        </div>

        {fnCount > 0 && (
          <span className="text-sm" style={{ color: 'var(--text-tertiary)', fontFamily: TERM_FONT }}>
            [{fnCount} fn]
          </span>
        )}

        {mod.public === false && (
          <Lock size={14} className="flex-shrink-0" style={{ color: 'var(--text-tertiary)' }} />
        )}

        <div className="flex-1" />

        <div className="flex items-center gap-3 flex-shrink-0" style={{ fontSize: '13px', fontFamily: TERM_FONT }}>
          <span style={{ color: 'var(--text-tertiary)' }} title={updatedTimeStr}>{agoStr || updatedTimeStr}</span>

          <div className="relative" ref={ownerDropdownRef}>
            <span
              className="cursor-pointer"
              style={{ color: 'var(--text-secondary)' }}
              title={mod.key}
              onClick={(e) => {
                e.preventDefault()
                e.stopPropagation()
                if (hasMultipleOwners) setOwnerDropdownOpen(!ownerDropdownOpen)
              }}
            >
              {hasMultipleOwners && (
                <span style={{ color: 'var(--text-tertiary)', marginRight: '4px' }}>
                  [{currentVersionIndex + 1}/{allVersions.length}]
                </span>
              )}
              <Link href={`/user/${mod.key}`} onClick={(e) => e.stopPropagation()} className="inline">
                <KeyIcon className="w-3.5 h-3.5 inline hover:scale-110 transition-transform" style={{ strokeWidth: 2.5, color: 'var(--text-secondary)' }} />
              </Link>
              {' '}{shorten(mod.key, 4, 4)}
              <CopyButton text={mod.key} size="sm" showValueOnHover={true} />
              {hasMultipleOwners && <ChevronDown size={10} className="inline ml-1" style={{ color: 'var(--text-tertiary)' }} />}
            </span>

            {hasMultipleOwners && ownerDropdownOpen && (
              <div
                className="absolute top-full mt-2 right-0 z-50 min-w-[200px] max-h-[300px] overflow-y-auto"
                style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border-color)' }}
              >
                {allVersions.map((version, idx) => (
                  <div
                    key={version.key}
                    className="flex items-center gap-2 px-3 py-2 cursor-pointer transition-colors"
                    style={{
                      backgroundColor: idx === currentVersionIndex ? colorWithOpacity(modColor, 0.1) : 'transparent',
                      borderBottom: idx < allVersions.length - 1 ? '1px solid var(--border-color)' : 'none',
                      fontFamily: TERM_FONT, fontSize: '12px',
                    }}
                    onClick={(e) => { e.preventDefault(); e.stopPropagation(); handleOwnerSelect(version.key) }}
                    onMouseEnter={(e) => { e.currentTarget.style.backgroundColor = colorWithOpacity(modColor, 0.15) }}
                    onMouseLeave={(e) => { e.currentTarget.style.backgroundColor = idx === currentVersionIndex ? colorWithOpacity(modColor, 0.1) : 'transparent' }}
                  >
                    <code style={{ color: 'var(--text-secondary)', fontFamily: TERM_FONT }}>
                      {shorten(version.key, 6, 6)}
                    </code>
                    {idx === currentVersionIndex && <span style={{ color: modColor }}>*</span>}
                  </div>
                ))}
              </div>
            )}
          </div>

          {mod.cid && (
            <div className="relative" ref={versionDropdownRef}>
              <span
                className="cursor-pointer"
                style={{ color: 'var(--text-tertiary)' }}
                title={mod.cid}
                onClick={(e) => {
                  e.preventDefault()
                  e.stopPropagation()
                  if (hasHistoricalVersions) setVersionDropdownOpen(!versionDropdownOpen)
                }}
              >
                {hasHistoricalVersions && (
                  <span style={{ marginRight: '4px' }}>v{historicalVersions.length - selectedHistoricalIndex}</span>
                )}
                {shorten(mod.cid || '', 4, 4)}
                <CopyButton text={mod.cid || ''} size="sm" showValueOnHover={true} />
                {hasHistoricalVersions && <ChevronDown size={10} className="inline ml-1" />}
              </span>

              {hasHistoricalVersions && versionDropdownOpen && (
                <div
                  className="absolute top-full mt-2 right-0 z-50 min-w-[200px] max-h-[300px] overflow-y-auto"
                  style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border-color)' }}
                >
                  {historicalVersions.map((version, idx) => {
                    const versionNum = historicalVersions.length - idx
                    return (
                      <div
                        key={idx}
                        className="flex items-center gap-2 px-3 py-2 cursor-pointer transition-colors"
                        style={{
                          backgroundColor: idx === selectedHistoricalIndex ? colorWithOpacity(modColor, 0.1) : 'transparent',
                          borderBottom: idx < historicalVersions.length - 1 ? '1px solid var(--border-color)' : 'none',
                          fontFamily: TERM_FONT, fontSize: '12px',
                        }}
                        onClick={(e) => { e.preventDefault(); e.stopPropagation(); handleHistoricalSelect(idx) }}
                        onMouseEnter={(e) => { e.currentTarget.style.backgroundColor = colorWithOpacity(modColor, 0.15) }}
                        onMouseLeave={(e) => { e.currentTarget.style.backgroundColor = idx === selectedHistoricalIndex ? colorWithOpacity(modColor, 0.1) : 'transparent' }}
                      >
                        <span style={{ color: 'var(--text-secondary)' }}>v{versionNum}</span>
                        <code className="flex-1 truncate" style={{ color: 'var(--text-tertiary)', fontFamily: TERM_FONT }}>
                          {shorten(version.data || version.cid || '', 6, 6)}
                        </code>
                        {idx === selectedHistoricalIndex && <span style={{ color: modColor }}>*</span>}
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          )}

          {mod.url && (
            <span style={{ color: 'var(--accent-success, #22c55e)' }}>LIVE</span>
          )}
        </div>
      </div>
    )
  }

  const cardContent = (
    <div
      className={`relative overflow-hidden group h-full ${card_enabled ? 'cursor-pointer' : ''}`}
      style={{
        fontFamily: TERM_FONT,
        ...(card_enabled ? {
          background: 'var(--bg-secondary)',
          border: `1px solid ${isHovered ? colorWithOpacity(modColor, 0.25) : 'var(--border-color)'}`,
          borderRadius: '14px',
          backdropFilter: 'blur(20px)',
          WebkitBackdropFilter: 'blur(20px)',
          boxShadow: isHovered
            ? `0 8px 32px rgba(0,0,0,0.15), 0 0 0 1px ${colorWithOpacity(modColor, 0.08)}`
            : '0 2px 8px rgba(0,0,0,0.06)',
          transition: 'all 0.3s cubic-bezier(0.4, 0, 0.2, 1)',
          transform: isHovered ? 'translateY(-2px)' : 'translateY(0)',
        } : {}),
      }}
      onMouseEnter={() => card_enabled && setIsHovered(true)}
      onMouseLeave={() => card_enabled && setIsHovered(false)}
    >
      {/* Subtle accent gradient overlay */}
      {card_enabled && (
        <div
          className="absolute inset-0 pointer-events-none"
          style={{
            borderRadius: '14px',
            background: `linear-gradient(135deg, ${colorWithOpacity(modColor, isHovered ? 0.06 : 0.02)} 0%, transparent 70%)`,
            transition: 'background 0.3s ease',
          }}
        />
      )}

      {/* Left accent line */}
      {card_enabled && (
        <div
          className="absolute left-0 top-3 bottom-3"
          style={{
            width: '2px',
            borderRadius: '0 2px 2px 0',
            background: isHovered
              ? modColor
              : colorWithOpacity(modColor, 0.2),
            transition: 'all 0.3s ease',
            boxShadow: isHovered ? `0 0 8px ${colorWithOpacity(modColor, 0.4)}` : 'none',
          }}
        />
      )}

      <div className={`relative ${card_enabled ? 'pl-5 pr-4 pt-4 pb-3.5' : ''}`}>
        {/* Header */}
        <div className="flex items-center gap-2.5 mb-2.5">
          <code
            className="truncate"
            style={{
              color: 'var(--text-primary)',
              fontFamily: TERM_FONT,
              fontSize: isExpanded ? '22px' : '18px',
              fontWeight: 600,
              letterSpacing: '0.02em',
              transition: 'all 0.3s ease',
            }}
          >
            {mod.name}
          </code>
          {fnCount > 0 && (
            <span
              className="flex-shrink-0 px-1.5 py-0.5 rounded-md"
              style={{
                color: colorWithOpacity(modColor, 0.7),
                fontSize: '12px',
                fontFamily: TERM_FONT,
                fontWeight: 500,
                background: colorWithOpacity(modColor, 0.06),
                border: `1px solid ${colorWithOpacity(modColor, 0.1)}`,
              }}
            >
              {fnCount} fn
            </span>
          )}
          {mod.public === false && (
            <Lock size={12} className="flex-shrink-0" style={{ color: 'var(--text-tertiary)', opacity: 0.6 }} />
          )}

          <div className="flex-1" />

          {mod.local && (
            <span
              className="flex-shrink-0 px-1.5 py-0.5 rounded-md"
              style={{
                color: '#fbbf24',
                fontSize: '10px',
                fontFamily: TERM_FONT,
                fontWeight: 700,
                background: 'rgba(251, 191, 36, 0.08)',
                border: '1px solid rgba(251, 191, 36, 0.2)',
                letterSpacing: '0.05em',
              }}
            >
              LOCAL
            </span>
          )}

          {mod.url && (
            <span className="flex items-center gap-1.5" style={{
              color: 'var(--accent-success, #34d399)',
              fontSize: '12px',
              fontFamily: TERM_FONT,
              fontWeight: 500,
              letterSpacing: '0.05em',
            }}>
              <span className="w-1.5 h-1.5 rounded-full animate-pulse" style={{
                background: 'var(--accent-success, #34d399)',
                boxShadow: '0 0 6px var(--accent-success, #34d399)',
              }} />
              LIVE
            </span>
          )}

          {/* QR on hover */}
          <div className="flex items-center flex-shrink-0 opacity-0 group-hover:opacity-100 transition-opacity duration-200">
            <div
              className="relative"
              onMouseEnter={() => setIsNameQrHovered(true)}
              onMouseLeave={() => setIsNameQrHovered(false)}
            >
              <div className="w-6 h-6 flex items-center justify-center" style={{ color: 'var(--text-tertiary)' }}>
                <QrCodeIcon className="h-3.5 w-3.5 cursor-pointer hover:opacity-80 transition-opacity" />
              </div>
              {isNameQrHovered && (
                <div
                  className="absolute bottom-full left-1/2 -translate-x-1/2 mb-3 p-3 z-[9999]"
                  style={{
                    border: '1px solid var(--border-color)',
                    borderRadius: '12px',
                    background: 'var(--bg-sidebar)',
                    backdropFilter: 'blur(20px)',
                    boxShadow: '0 8px 32px rgba(0,0,0,0.2)',
                  }}
                >
                  <QRCode value={websiteUrl} size={120} color={modColor} />
                </div>
              )}
            </div>
          </div>
        </div>

        {mod.desc && (
          <p
            className={`mb-2.5 ${isExpanded ? '' : 'line-clamp-2'}`}
            style={{
              color: 'var(--text-tertiary)',
              fontFamily: TERM_FONT,
              fontSize: '13px',
              lineHeight: '1.5',
            }}
          >
            {mod.desc}
          </p>
        )}

        {/* Metadata row */}
        <div className="flex items-center gap-3" style={{ fontSize: '13px', fontFamily: TERM_FONT, color: 'var(--text-tertiary)', opacity: 0.7 }}>
          <span title={updatedTimeStr}>{agoStr || updatedTimeStr}</span>

          <span className="opacity-30">|</span>

          <span
            className="cursor-default flex items-center gap-1"
            title={mod.key}
            onClick={(e) => e.preventDefault()}
          >
            <Link href={`/user/${mod.key}`} onClick={(e) => e.stopPropagation()}>
              <KeyIcon
                className="inline w-3 h-3 hover:scale-110 transition-transform"
                style={{ color: 'var(--text-tertiary)', strokeWidth: 2 }}
              />
            </Link>
            <code style={{ fontFamily: TERM_FONT }}>{shorten(mod.key, 4, 4)}</code>
          </span>

          {mod.cid && (
            <>
              <span className="opacity-30">|</span>
              <span
                className="cursor-default"
                title={mod.cid}
                onClick={(e) => e.preventDefault()}
              >
                <code style={{ fontFamily: TERM_FONT }}>{shorten(mod.cid || '', 4, 4)}</code>
              </span>
            </>
          )}
        </div>
      </div>
    </div>
  )

  if (!card_enabled) return cardContent

  return (
    <Link href={`/mod/${mod.name}`}>
      {cardContent}
    </Link>
  )
}
