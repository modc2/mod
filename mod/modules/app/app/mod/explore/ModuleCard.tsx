'use client'

import { ModuleType } from '@/app/types'
import { CopyButton } from '@/app/block/CopyButton'
import Link from 'next/link'
import { Package, Calendar, User, Hash } from 'lucide-react'

interface ModuleCardProps {
  mod: ModuleType
}

const MODULE_COLORS = [
  { from: 'from-purple-500/20', to: 'to-pink-500/20', text: 'text-purple-400', hover: 'from-purple-400 to-pink-400', border: 'border-purple-500/30' },
  { from: 'from-blue-500/20', to: 'to-cyan-500/20', text: 'text-blue-400', hover: 'from-blue-400 to-cyan-400', border: 'border-blue-500/30' },
  { from: 'from-green-500/20', to: 'to-emerald-500/20', text: 'text-green-400', hover: 'from-green-400 to-emerald-400', border: 'border-green-500/30' },
  { from: 'from-orange-500/20', to: 'to-red-500/20', text: 'text-orange-400', hover: 'from-orange-400 to-red-400', border: 'border-orange-500/30' },
  { from: 'from-yellow-500/20', to: 'to-amber-500/20', text: 'text-yellow-400', hover: 'from-yellow-400 to-amber-400', border: 'border-yellow-500/30' },
  { from: 'from-indigo-500/20', to: 'to-violet-500/20', text: 'text-indigo-400', hover: 'from-indigo-400 to-violet-400', border: 'border-indigo-500/30' },
]

export default function ModuleCard({ mod }: ModuleCardProps) {
  const formatDate = (timestamp: number) => {
    return new Date(timestamp * 1000).toLocaleDateString('en-US', {
      month: 'short',
      day: 'numeric',
      year: 'numeric'
    })
  }

  const colorIndex = mod.name.split('').reduce((acc, char) => acc + char.charCodeAt(0), 0) % MODULE_COLORS.length
  const colors = MODULE_COLORS[colorIndex]

  return (
    <div className={`group relative bg-gradient-to-br ${colors.from} ${colors.to} border ${colors.border} rounded-xl p-5 hover:border-white/30 hover:shadow-2xl hover:shadow-white/10 transition-all duration-300 backdrop-blur-sm overflow-hidden`}>
      <div className={`absolute inset-0 bg-gradient-to-br ${colors.from} via-transparent ${colors.to} opacity-0 group-hover:opacity-100 transition-opacity duration-500`} />
      
      <div className="relative z-10 space-y-3">
        <div className="flex items-start justify-between gap-3">
          <Link href={`${mod.name}/${mod.key}`} className="flex-1 min-w-0">
            <div className="flex items-center gap-2 group/link">
              <div className={`flex-shrink-0 p-2 bg-gradient-to-br ${colors.from} ${colors.to} rounded-lg border border-white/20 group-hover/link:scale-110 transition-transform duration-300`}>
                <Package className={`w-5 h-5 ${colors.text}`} />
              </div>
              <h3 className={`text-2xl font-bold text-white group-hover/link:text-transparent group-hover/link:bg-gradient-to-r group-hover/link:${colors.hover} group-hover/link:bg-clip-text transition-all duration-300 truncate`}>
                {mod.name}
              </h3>
            </div>
          </Link>

          <div className="flex-shrink-0 flex items-center gap-2 bg-black/50 border border-white/20 px-3 py-1.5 rounded-lg backdrop-blur-sm hover:bg-black/60 transition-colors">
            <User className="w-4 h-4 text-white/60" />
            <code className="text-sm text-white/80 font-mono max-w-[100px] truncate" title={mod.key}>
              {mod.key}
            </code>
            <CopyButton text={mod.key} />
          </div>
        </div>

        <div className="flex flex-wrap gap-2">
          {mod.cid && (
            <div className="flex items-center gap-2 bg-black/40 border border-white/20 px-3 py-1.5 rounded-lg backdrop-blur-sm hover:bg-black/50 transition-colors">
              <Hash className={`w-4 h-4 ${colors.text}/80`} />
              <div className="flex items-center gap-1.5">
                <span className="text-sm text-white/60 font-medium">CID:</span>
                <code className={`text-sm ${colors.text} font-mono max-w-[180px] truncate`} title={mod.cid}>
                  {mod.cid.slice(0, 10)}...{mod.cid.slice(-6)}
                </code>
                <CopyButton text={mod.cid} />
              </div>
            </div>
          )}

          {mod.updated && (
            <div className="flex items-center gap-2 bg-black/40 border border-white/20 px-3 py-1.5 rounded-lg backdrop-blur-sm">
              <Calendar className={`w-4 h-4 ${colors.text}/80`} />
              <span className="text-sm text-white/60 font-medium">Updated:</span>
              <span className={`text-sm ${colors.text} font-semibold`}>
                {formatDate(mod.updated)}
              </span>
            </div>
          )}
        </div>

        {(mod.desc || mod.content) && (
          <div className="bg-black/30 border border-white/20 px-4 py-3 rounded-lg backdrop-blur-sm">
            <p className="text-base text-white/80 leading-relaxed line-clamp-2">
              {mod.desc || mod.content}
            </p>
          </div>
        )}
      </div>
    </div>
  )
}