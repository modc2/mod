'use client'

import { ModuleType } from '@/app/types'
import { CopyButton } from '@/app/block/CopyButton'
import Link from 'next/link'
import { Package, Calendar, User, Hash, Sparkles } from 'lucide-react'
import { time2str, text2color, shorten } from '@/app/utils'

interface ModuleCardProps {
  mod: ModuleType
}

const MODULE_COLORS = [
  { from: 'from-purple-500/20', to: 'to-pink-500/20', text: 'text-purple-400', hover: 'from-purple-400 to-pink-400', border: 'border-purple-500/40', glow: 'shadow-purple-500/20' },
  { from: 'from-blue-500/20', to: 'to-cyan-500/20', text: 'text-blue-400', hover: 'from-blue-400 to-cyan-400', border: 'border-blue-500/40', glow: 'shadow-blue-500/20' },
  { from: 'from-green-500/20', to: 'to-emerald-500/20', text: 'text-green-400', hover: 'from-green-400 to-emerald-400', border: 'border-green-500/40', glow: 'shadow-green-500/20' },
  { from: 'from-orange-500/20', to: 'to-red-500/20', text: 'text-orange-400', hover: 'from-orange-400 to-red-400', border: 'border-orange-500/40', glow: 'shadow-orange-500/20' },
  { from: 'from-yellow-500/20', to: 'to-amber-500/20', text: 'text-yellow-400', hover: 'from-yellow-400 to-amber-400', border: 'border-yellow-500/40', glow: 'shadow-yellow-500/20' },
  { from: 'from-indigo-500/20', to: 'to-violet-500/20', text: 'text-indigo-400', hover: 'from-indigo-400 to-violet-400', border: 'border-indigo-500/40', glow: 'shadow-indigo-500/20' },
]

export default function ModuleCard({ mod }: ModuleCardProps) {
  const colorIndex = mod.name.split('').reduce((acc, char) => acc + char.charCodeAt(0), 0) % MODULE_COLORS.length
  const colors = MODULE_COLORS[colorIndex]
  const keyColor = text2color(mod.key)

  return (
    <div className={`group relative bg-gradient-to-br ${colors.from} ${colors.to} border-2 ${colors.border} rounded-2xl p-6 hover:border-white/50 hover:shadow-2xl hover:${colors.glow} transition-all duration-300 backdrop-blur-sm overflow-hidden h-full flex flex-col hover:scale-[1.02]`}>
      <div className={`absolute inset-0 bg-gradient-to-br ${colors.from} via-transparent ${colors.to} opacity-0 group-hover:opacity-100 transition-opacity duration-500`} />
      
      <div className="relative z-10 space-y-4 flex-1 flex flex-col">
        <Link href={`${mod.name}/${mod.key}`} className="flex-1 min-w-0">
          <div className="flex items-start justify-between gap-3 mb-5">
            <div className="flex items-center gap-4 group/link flex-1 min-w-0">
              <div className={`flex-shrink-0 p-3 bg-gradient-to-br ${colors.from} ${colors.to} rounded-xl border-2 border-white/40 group-hover/link:scale-110 group-hover/link:rotate-6 transition-all duration-300 shadow-xl`}>
                <Package className={`${colors.text}`} size={36} strokeWidth={2.5} />
              </div>
              <div className="flex-1 min-w-0">
                <h3 className={`text-3xl font-black text-white group-hover/link:text-transparent group-hover/link:bg-gradient-to-r group-hover/link:${colors.hover} group-hover/link:bg-clip-text transition-all duration-300 truncate uppercase tracking-wide drop-shadow-lg`}>
                  {mod.name}
                </h3>
                <div className="flex items-center gap-2 mt-1">
                  <Sparkles className={`${colors.text}`} size={14} strokeWidth={2.5} />
                  <span className="text-xs text-white/60 font-bold uppercase tracking-wider">Module</span>
                </div>
              </div>
            </div>
          </div>

          <div className="flex flex-wrap gap-3 mb-5">
            {mod.content && (
              <div className="flex items-center gap-2 bg-black/60 border-2 border-white/30 px-4 py-2.5 rounded-xl backdrop-blur-sm hover:bg-black/70 hover:border-white/50 transition-all shadow-lg group/cid">
                <Hash className={`${colors.text} group-hover/cid:rotate-12 transition-transform`} size={18} strokeWidth={2.5} />
                <div className="flex items-center gap-2">
                  <span className="text-sm text-white/70 font-bold uppercase tracking-wide">CID:</span>
                  <code className={`text-sm ${colors.text} font-mono font-bold max-w-[140px] truncate`} title={mod.content}>
                    {shorten(mod.content)}
                  </code>
                  <CopyButton text={mod.content} />
                </div>
              </div>
            )}

            {mod.updated && (
              <div className="flex items-center gap-2 bg-black/60 border-2 border-white/30 px-4 py-2.5 rounded-xl backdrop-blur-sm shadow-lg">
                <Calendar className={`${colors.text}`} size={18} strokeWidth={2.5} />
                <span className="text-sm text-white/70 font-bold uppercase tracking-wide">Updated:</span>
                <span className={`text-sm ${colors.text} font-black`}>
                  {time2str(mod.updated)}
                </span>
              </div>
            )}
          </div>

          {mod.desc && (
            <div className="bg-black/50 border-2 border-white/30 px-5 py-4 rounded-xl backdrop-blur-sm flex-1 shadow-lg hover:bg-black/60 transition-all">
              <p className="text-base text-white/90 leading-relaxed line-clamp-3 font-medium">
                {mod.desc}
              </p>
            </div>
          )}
        </Link>

        <div className="flex items-center gap-2 bg-black/60 border-2 border-white/30 px-4 py-2.5 rounded-xl backdrop-blur-sm shadow-lg mt-auto group/key hover:bg-black/70 transition-all">
          <User className={`${colors.text} group-hover/key:scale-110 transition-transform`} size={16} strokeWidth={2.5} />
          <span className="text-xs text-white/60 font-bold uppercase tracking-wide">Key:</span>
          <code className="text-xs font-mono font-bold text-white/80 flex-1 truncate" title={mod.key}>
            {mod.key}
          </code>
          <CopyButton text={mod.key} size="sm" />
        </div>
      </div>
    </div>
  )
}