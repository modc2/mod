'use client'

import { ModuleType } from '@/app/types'
import { CopyButton } from '@/app/block/CopyButton'
import Link from 'next/link'
import { Package, Calendar, User, Hash } from 'lucide-react'

interface ModuleCardProps {
  mod: ModuleType  // Changed from module: Module
}

export default function ModuleCard({ mod }: ModuleCardProps) {
  const formatDate = (timestamp: number) => {
    return new Date(timestamp * 1000).toLocaleDateString('en-US', {
      month: 'short',
      day: 'numeric',
      year: 'numeric'
    })
  }

  return (
    <div className="group relative bg-gradient-to-br from-white/[0.07] to-white/[0.02] border border-white/10 rounded-2xl p-6 hover:border-white/20 hover:shadow-2xl hover:shadow-white/5 transition-all duration-300 backdrop-blur-sm overflow-hidden">
      {/* Hover gradient overlay */}
      <div className="absolute inset-0 bg-gradient-to-br from-green-500/5 via-transparent to-blue-500/5 opacity-0 group-hover:opacity-100 transition-opacity duration-500" />
      
      <div className="relative z-10 space-y-4">
        {/* Header: Name + Key */}
        <div className="flex items-start justify-between gap-4">
          <Link href={`${mod.name}/${mod.key}`} className="flex-1 min-w-0">
            <div className="flex items-center gap-3 group/link">
              <div className="flex-shrink-0 p-2.5 bg-gradient-to-br from-green-500/20 to-blue-500/20 rounded-xl border border-white/10 group-hover/link:scale-110 transition-transform duration-300">
                <Package className="w-5 h-5 text-green-400" />
              </div>
              <h3 className="text-2xl font-bold text-white group-hover/link:text-transparent group-hover/link:bg-gradient-to-r group-hover/link:from-green-400 group-hover/link:to-blue-400 group-hover/link:bg-clip-text transition-all duration-300 truncate">
                {mod.name}
              </h3>
            </div>
          </Link>

          {/* Author Key Bubble */}
          <div className="flex-shrink-0 flex items-center gap-2 bg-black/40 border border-white/10 px-4 py-2 rounded-xl backdrop-blur-sm hover:bg-black/50 transition-colors">
            <User className="w-4 h-4 text-white/50" />
            <code className="text-xs text-white/70 font-mono max-w-[120px] truncate" title={mod.key}>
              {mod.key}
            </code>
            <CopyButton text={mod.key} />
          </div>
        </div>

        {/* Content Bubbles Row */}
        <div className="flex flex-wrap gap-3">
          {/* CID Bubble */}
          {mod.cid && (
            <div className="flex items-center gap-2 bg-black/30 border border-white/10 px-4 py-2.5 rounded-xl backdrop-blur-sm hover:bg-black/40 transition-colors">
              <Hash className="w-4 h-4 text-blue-400/70" />
              <div className="flex items-center gap-2">
                <span className="text-xs text-white/50 font-medium">CID:</span>
                <code className="text-xs text-blue-400/90 font-mono max-w-[200px] truncate" title={mod.cid}>
                  {mod.cid.slice(0, 12)}...{mod.cid.slice(-8)}
                </code>
                <CopyButton text={mod.cid} />
              </div>
            </div>
          )}

          {/* Updated Date Bubble */}
          {mod.updated && (
            <div className="flex items-center gap-2 bg-black/30 border border-white/10 px-4 py-2.5 rounded-xl backdrop-blur-sm">
              <Calendar className="w-4 h-4 text-purple-400/70" />
              <span className="text-xs text-white/50 font-medium">Updated:</span>
              <span className="text-xs text-purple-400/90 font-semibold">
                {formatDate(mod.updated)}
              </span>
            </div>
          )}
        </div>

        {/* Description/Content Bubble */}
        {(mod.desc || mod.content) && (
          <div className="bg-black/20 border border-white/10 px-5 py-4 rounded-xl backdrop-blur-sm">
            <p className="text-sm text-white/70 leading-relaxed line-clamp-3">
              {mod.desc || mod.content}
            </p>
          </div>
        )}
      </div>
    </div>
  )
}