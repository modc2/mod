'use client'

import { UserType } from '@/app/types'
import { CopyButton } from '@/app/block/CopyButton'
import Link from 'next/link'
import { User, Hash, Package, Wallet, Sparkles } from 'lucide-react'
import { shorten, text2color } from "@/app/utils";

interface UserCardProps {
  user: UserType
}

const USER_COLORS = [
  { from: 'from-purple-500/20', to: 'to-pink-500/20', text: 'text-purple-400', hover: 'from-purple-400 to-pink-400', border: 'border-purple-500/40', glow: 'shadow-purple-500/20' },
  { from: 'from-blue-500/20', to: 'to-cyan-500/20', text: 'text-blue-400', hover: 'from-blue-400 to-cyan-400', border: 'border-blue-500/40', glow: 'shadow-blue-500/20' },
  { from: 'from-green-500/20', to: 'to-emerald-500/20', text: 'text-green-400', hover: 'from-green-400 to-emerald-400', border: 'border-green-500/40', glow: 'shadow-green-500/20' },
  { from: 'from-orange-500/20', to: 'to-red-500/20', text: 'text-orange-400', hover: 'from-orange-400 to-red-400', border: 'border-orange-500/40', glow: 'shadow-orange-500/20' },
  { from: 'from-yellow-500/20', to: 'to-amber-500/20', text: 'text-yellow-400', hover: 'from-yellow-400 to-amber-400', border: 'border-yellow-500/40', glow: 'shadow-yellow-500/20' },
  { from: 'from-indigo-500/20', to: 'to-violet-500/20', text: 'text-indigo-400', hover: 'from-indigo-400 to-violet-400', border: 'border-indigo-500/40', glow: 'shadow-indigo-500/20' },
]

export function UserCard({ user }: UserCardProps) {
  const colorIndex = user.key.split('').reduce((acc, char) => acc + char.charCodeAt(0), 0) % USER_COLORS.length
  const colors = USER_COLORS[colorIndex]
  const modCount = user.mods?.length || 0
  const balance = user.balance || 0
  const keyColor = text2color(user.key)

  return (
    <div className={`group relative bg-gradient-to-br ${colors.from} ${colors.to} border-2 ${colors.border} rounded-2xl p-6 hover:border-white/50 hover:shadow-2xl hover:${colors.glow} transition-all duration-300 backdrop-blur-sm overflow-hidden h-full flex flex-col hover:scale-[1.02]`}>
      <div className={`absolute inset-0 bg-gradient-to-br ${colors.from} via-transparent ${colors.to} opacity-0 group-hover:opacity-100 transition-opacity duration-500`} />
      
      <div className="relative z-10 space-y-4 flex-1 flex flex-col">
        <Link href={`/user/${user.key}`} className="flex-1 min-w-0">
          <div className="flex items-start justify-between gap-3 mb-5">
            <div className="flex items-center gap-4 group/link flex-1 min-w-0">
              <div className={`flex-shrink-0 p-3 bg-gradient-to-br ${colors.from} ${colors.to} rounded-xl border-2 border-white/40 group-hover/link:scale-110 group-hover/link:rotate-6 transition-all duration-300 shadow-xl`}>
                <User className={`${colors.text}`} size={36} strokeWidth={2.5} />
              </div>
              <div className="flex-1 min-w-0">
                <div className="bg-black/60 border-2 border-white/30 px-4 py-3 rounded-xl backdrop-blur-sm shadow-lg">
                  <div className="flex items-center gap-2 mb-1">
                    <Sparkles size={14} strokeWidth={2.5} style={{ color: keyColor }} />
                    <span className="text-xs text-white/60 font-bold uppercase tracking-wider">Key</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <code className="text-lg font-mono font-bold truncate" style={{ color: keyColor }} title={user.key}>
                      {user.key}
                    </code>
                    <CopyButton text={user.key} size="sm" />
                  </div>
                </div>
              </div>
            </div>
          </div>

          <div className="flex flex-wrap gap-3 mb-5">
            <div className="flex items-center gap-2 bg-black/60 border-2 border-white/30 px-4 py-2.5 rounded-xl backdrop-blur-sm hover:bg-black/70 hover:border-white/50 transition-all shadow-lg group/stat">
              <Package className={`${colors.text} group-hover/stat:rotate-12 transition-transform`} size={18} strokeWidth={2.5} />
              <span className="text-sm text-white/70 font-bold uppercase tracking-wide">Modules:</span>
              <span className={`text-sm ${colors.text} font-black`}>{modCount}</span>
            </div>

            {user.balance !== undefined && (
              <div className="flex items-center gap-2 bg-black/60 border-2 border-white/30 px-4 py-2.5 rounded-xl backdrop-blur-sm shadow-lg">
                <Wallet className={`${colors.text}`} size={18} strokeWidth={2.5} />
                <span className="text-sm text-white/70 font-bold uppercase tracking-wide">Balance:</span>
                <span className={`text-sm ${colors.text} font-black`}>{balance.toFixed(2)}</span>
              </div>
            )}
          </div>

          {user.address && (
            <div className="bg-black/50 border-2 border-white/30 px-5 py-4 rounded-xl backdrop-blur-sm flex-1 shadow-lg hover:bg-black/60 transition-all">
              <div className="flex items-center gap-2 mb-2">
                <Hash className={`${colors.text}`} size={18} strokeWidth={2.5} />
                <span className="text-sm text-white/70 font-bold uppercase tracking-wide">Address</span>
              </div>
              <div className="flex items-center gap-2">
                <code className={`text-base font-mono ${colors.text} font-bold truncate flex-1`} title={user.address}>
                  {user.address.slice(0, 8)}...{user.address.slice(-8)}
                </code>
                <CopyButton text={user.address} size="sm" />
              </div>
            </div>
          )}
        </Link>
      </div>
    </div>
  )
}
