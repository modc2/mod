'use client'

import Link from 'next/link'
import { ModuleType } from '@/app/types'
import { CopyButton } from '@/app/block/CopyButton'
import { Package, Calendar } from 'lucide-react'

interface ModuleCardProps {
  mod: ModuleType
}

const formatDate = (ts?: number) => {
  if (!ts) return 'Unknown'
  try {
    return new Date(ts * 1000).toLocaleDateString()
  } catch {
    return 'Unknown'
  }
}

export default function ModuleCard({ mod }: ModuleCardProps) {
  const titleHref = `/${mod.key}/${encodeURIComponent(mod.name || '')}`
  const description = mod.desc || mod.content || ''

  return (
    <div className="group relative overflow-hidden rounded-2xl border border-white/10 bg-gradient-to-br from-white/[0.06] to-white/[0.03] p-6 transition-all duration-300 hover:border-white/20 hover:shadow-2xl hover:shadow-white/5 backdrop-blur-sm">
      {/* Subtle hover glow */}
      <div className="pointer-events-none absolute inset-0 opacity-0 transition-opacity duration-500 group-hover:opacity-100" style={{ background: 'radial-gradient(100% 100% at 100% 0%, rgba(74,222,128,0.08) 0%, transparent 60%)' }} />

      <div className="relative z-10">
        {/* Header: icon, name, key chip */}
        <div className="mb-5 flex items-start justify-between gap-4">
          <div className="flex items-center gap-4 min-w-0">
            <div className="rounded-xl border border-white/10 bg-gradient-to-br from-green-500/15 to-blue-500/15 p-2 transition-transform duration-300 group-hover:scale-110">
              <Package className="h-5 w-5 text-green-400" />
            </div>

            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <Link href={titleHref} className="min-w-0">
                  <h3 className="max-w-full truncate text-2xl font-bold text-white transition-all duration-300 group-hover:bg-gradient-to-r group-hover:from-green-400 group-hover:to-blue-400 group-hover:bg-clip-text group-hover:text-transparent">
                    {mod.name}
                  </h3>
                </Link>

                <div className="inline-flex items-center gap-2 rounded-full border border-white/15 bg-black/40 px-3 py-1.5 text-xs font-mono text-white/80 backdrop-blur-sm">
                  <span className="truncate max-w-[14rem]">{mod.key}</span>
                  <span className="h-3 w-px bg-white/15" />
                  <CopyButton text={mod.key} />
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* Bubbles: content and updated */}
        <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
          {/* Content bubble */}
          <div className="md:col-span-2 rounded-xl border border-white/10 bg-white/[0.05] p-4">
            <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-white/50">
              Content
            </div>
            {description ? (
              <p className="whitespace-pre-line text-sm leading-relaxed text-white/80">
                {description}
              </p>
            ) : (
              <p className="text-sm text-white/45">No description provided.</p>
            )}
          </div>

          {/* Updated bubble */}
          <div className="rounded-xl border border-white/10 bg-white/[0.05] p-4">
            <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-white/50">
              Updated
            </div>
            <div className="flex items-center gap-2 text-sm text-white/80">
              <Calendar className="h-4 w-4 text-white/60" />
              <span>{formatDate(mod.updated)}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}