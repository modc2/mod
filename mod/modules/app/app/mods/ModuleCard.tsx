'use client'

import { ModuleType } from '@/app/types'
import { CopyButton } from '@/app/block/CopyButton'
import { KeyIcon, ClockIcon, HashtagIcon } from '@heroicons/react/24/outline'
import Link from 'next/link'

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

const shorten = (str: string): string => {
  if (!str || str.length <= 12) return str
  return `${str.slice(0, 8)}...${str.slice(-4)}`
}

const time2str = (time: number): string => {
  const d = new Date(time * 1000)
  const now = new Date()
  const diff = now.getTime() - d.getTime()
  if (diff < 60_000) return 'now'
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`
  if (diff < 604_800_000) return `${Math.floor(diff / 86_400_000)}d ago`
  return d.toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: d.getFullYear() !== now.getFullYear() ? 'numeric' : undefined
  })
}

export default function ModuleCard({ mod }: { mod: ModuleType }) {
  const moduleColor = text2color(mod.name)
  const contentHash = mod.content || 'N/A'
  const fullContentAddress = `ipfs://${contentHash}`

  return (
    <Link href={`/${mod.key}/${mod.name}`}>
      <div
        className="group relative p-4 rounded-xl border-2 transition-all duration-300 hover:shadow-2xl cursor-pointer"
        style={{
          backgroundColor: `${moduleColor}08`,
          borderColor: `${moduleColor}40`,
        }}
      >
        {/* Row 1: Name, Key, and Time */}
        <div className="flex items-center gap-3 mb-3">
          <h3
            className="text-xl font-bold flex-shrink-0"
            style={{ color: moduleColor }}
          >
            {mod.name}
          </h3>
          
          {mod.key && (
            <div className="flex items-center gap-2 px-3 py-1 rounded-lg border" style={{ borderColor: `${moduleColor}30`, backgroundColor: `${moduleColor}10` }}>
              <KeyIcon className="h-4 w-4" style={{ color: moduleColor }} />
              <span className="font-mono text-sm" style={{ color: moduleColor }}>{shorten(mod.key)}</span>
              <CopyButton size="sm" content={mod.key} />
            </div>
          )}

          {mod.updated && (
            <div className="flex items-center gap-2 px-3 py-1 rounded-lg border" style={{ borderColor: `${moduleColor}30`, backgroundColor: `${moduleColor}10` }}>
              <ClockIcon className="h-4 w-4" style={{ color: moduleColor }} />
              <span className="text-sm font-medium" style={{ color: moduleColor }}>{time2str(mod.updated)}</span>
            </div>
          )}
        </div>

        {/* Row 2: Content Address */}
        <div className="flex items-center gap-2 px-3 py-2 rounded-lg border" style={{ borderColor: `${moduleColor}30`, backgroundColor: `${moduleColor}10` }}>
          <HashtagIcon className="h-4 w-4 flex-shrink-0" style={{ color: moduleColor }} />
          <span className="font-mono text-sm truncate flex-1" style={{ color: moduleColor }}>
            {fullContentAddress}
          </span>
          <CopyButton size="sm" content={fullContentAddress} />
        </div>
      </div>
    </Link>
  )
}
