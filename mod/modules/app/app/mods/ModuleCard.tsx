'use client'

import { ModuleType } from '@/app/types'
import { CopyButton } from '@/app/block/CopyButton'
import { KeyIcon, FingerPrintIcon, ClockIcon } from '@heroicons/react/24/outline'
import Link from 'next/link'
import { text2color, shorten, time2utc } from '@/app/utils'

export default function ModuleCard({ mod }: { mod: ModuleType }) {
  const moduleColor = text2color(mod.name)
  const contentHash = mod.content || 'N/A'
  const fullContentAddress = `${contentHash}`

  return (
    <Link href={`/${mod.key}/${mod.name}`}>
      <div
        className="group relative p-4 rounded-xl border-2 transition-all duration-300 hover:shadow-2xl cursor-pointer"
        style={{
          backgroundColor: `${moduleColor}08`,
          borderColor: `${moduleColor}40`,
        }}
      >
        <div className="flex items-start justify-between gap-3 mb-3">
          <h3
            className="text-xl font-bold flex-shrink-0"
            style={{ color: moduleColor }}
          >
            {mod.name}
          </h3>
          
          <div className="flex items-center gap-2 flex-shrink-0">
            {mod.key && (
              <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg border" style={{ borderColor: `${moduleColor}30`, backgroundColor: `${moduleColor}10` }}>
                <KeyIcon className="h-3.5 w-3.5" style={{ color: moduleColor }} />
                <span className="font-mono text-xs" style={{ color: moduleColor }}>{shorten(mod.key, 8)}</span>
                <CopyButton size="sm" content={mod.key} />
              </div>
            )}
          </div>
        </div>

        <div className="flex items-center gap-2 mb-2">
          <div className="flex items-center gap-2 px-3 py-2 rounded-lg border flex-1" style={{ borderColor: `${moduleColor}30`, backgroundColor: `${moduleColor}10` }}>
            <FingerPrintIcon className="h-4 w-4 flex-shrink-0" style={{ color: moduleColor }} />
            <span className="font-mono text-sm truncate flex-1" style={{ color: moduleColor }}>
              {shorten(contentHash, 16)}
            </span>
            <CopyButton size="sm" content={fullContentAddress} />
          </div>

          {mod.updated && (
            <div className="flex items-center gap-2 px-3 py-2 rounded-lg border flex-1" style={{ borderColor: `${moduleColor}30`, backgroundColor: `${moduleColor}10` }}>
              <ClockIcon className="h-4 w-4 flex-shrink-0" style={{ color: moduleColor }} />
              <span className="font-mono text-sm select-all" style={{ color: moduleColor }}>{time2utc(mod.updated)}</span>
              <CopyButton size="sm" content={time2utc(mod.updated)} />
            </div>
          )}
        </div>
      </div>
    </Link>
  )
}
