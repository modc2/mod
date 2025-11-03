'use client'

import { UserType } from '@/app/types'
import { motion } from 'framer-motion'
import { KeyIcon, CubeIcon, CurrencyDollarIcon } from '@heroicons/react/24/outline'
import { CopyButton } from '@/app/block/CopyButton'
import Link from 'next/link'
import { text2color, shorten } from '@/app/utils'

interface UserCardProps {
  user: UserType
}

export function UserCard({ user }: UserCardProps) {
  const userColor = text2color(user.key)

  return (
    <Link href={`/${user.key}`}>
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3 }}
        className="group relative overflow-hidden rounded-2xl border-2 p-6 transition-all duration-300 hover:shadow-2xl hover:scale-[1.02] cursor-pointer backdrop-blur-sm"
        style={{
          backgroundColor: `${userColor}08`,
          borderColor: `${userColor}40`,
          boxShadow: `0 4px 20px ${userColor}15`,
        }}
      >
        {/* Gradient overlay on hover */}
        <div 
          className="absolute inset-0 opacity-0 transition-opacity duration-500 group-hover:opacity-100"
          style={{
            background: `radial-gradient(circle at top right, ${userColor}12, transparent 70%)`
          }}
        />

        <div className="relative z-10 space-y-4">
          {/* Header with Key */}
          <div className="flex items-start justify-between gap-3">
            <div className="flex items-center gap-3 flex-1 min-w-0">
              <div 
                className="flex h-12 w-12 flex-shrink-0 items-center justify-center rounded-xl border-2 bg-gradient-to-br from-black to-zinc-900 shadow-lg"
                style={{ 
                  borderColor: `${userColor}50`,
                  boxShadow: `0 0 15px ${userColor}25`
                }}
              >
                <KeyIcon className="h-6 w-6" style={{ color: userColor }} />
              </div>
              <div className="flex-1 min-w-0">
                <h3
                  className="text-xl font-bold mb-1 truncate"
                  style={{ color: userColor }}
                >
                  {user.key}
                </h3>
                <div className="flex items-center gap-1.5">
                  <span className="font-mono text-xs" style={{ color: `${userColor}cc` }}>
                    {shorten(user.key, 12)}
                  </span>
                  <CopyButton size="sm" content={user.key} />
                </div>
              </div>
            </div>
          </div>

          {/* Stats Grid */}
          <div className="grid grid-cols-2 gap-3">
            <div 
              className="flex items-center gap-2.5 px-4 py-3 rounded-xl border backdrop-blur-sm transition-all hover:scale-105"
              style={{ 
                borderColor: `${userColor}30`, 
                backgroundColor: `${userColor}10` 
              }}
            >
              <div 
                className="rounded-lg p-2"
                style={{ backgroundColor: `${userColor}20` }}
              >
                <CubeIcon className="h-5 w-5" style={{ color: userColor }} />
              </div>
              <div>
                <div className="text-xl font-bold" style={{ color: userColor }}>
                  {user.mods?.length || 0}
                </div>
                <div className="text-xs font-medium text-white/50">Modules</div>
              </div>
            </div>

            <div 
              className="flex items-center gap-2.5 px-4 py-3 rounded-xl border backdrop-blur-sm transition-all hover:scale-105"
              style={{ 
                borderColor: `${userColor}30`, 
                backgroundColor: `${userColor}10` 
              }}
            >
              <div 
                className="rounded-lg p-2"
                style={{ backgroundColor: `${userColor}20` }}
              >
                <CurrencyDollarIcon className="h-5 w-5" style={{ color: userColor }} />
              </div>
              <div>
                <div className="text-xl font-bold" style={{ color: userColor }}>
                  {user.balance || 0}
                </div>
                <div className="text-xs font-medium text-white/50">Balance</div>
              </div>
            </div>
          </div>

          {/* Modules Preview */}
          {user.mods && user.mods.length > 0 && (
            <div 
              className="rounded-xl border p-3"
              style={{ 
                borderColor: `${userColor}20`,
                backgroundColor: `${userColor}05`
              }}
            >
              <div className="flex items-center gap-2 mb-2">
                <CubeIcon className="h-4 w-4" style={{ color: `${userColor}90` }} />
                <span className="text-xs font-semibold" style={{ color: `${userColor}90` }}>
                  Recent Modules
                </span>
              </div>
              <div className="flex flex-wrap gap-2">
                {user.mods.slice(0, 4).map((mod, idx) => (
                  <span
                    key={idx}
                    className="px-2.5 py-1 rounded-lg text-xs font-mono border transition-all hover:scale-105"
                    style={{
                      borderColor: `${userColor}35`,
                      backgroundColor: `${userColor}10`,
                      color: `${userColor}dd`
                    }}
                  >
                    {mod.name}
                  </span>
                ))}
                {user.mods.length > 4 && (
                  <span className="px-2.5 py-1 text-xs font-medium" style={{ color: `${userColor}80` }}>
                    +{user.mods.length - 4} more
                  </span>
                )}
              </div>
            </div>
          )}
        </div>

        {/* Hover border glow */}
        <div 
          className="absolute inset-0 rounded-2xl opacity-0 transition-opacity duration-500 group-hover:opacity-100 pointer-events-none"
          style={{
            boxShadow: `inset 0 0 0 1px ${userColor}40`
          }}
        />
      </motion.div>
    </Link>
  )
}
