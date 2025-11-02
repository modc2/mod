'use client'

import { UserType } from '@/app/types'
import { CopyButton } from '@/app/block/CopyButton'
import { KeyIcon, CubeIcon, ClockIcon, CheckCircleIcon } from '@heroicons/react/24/outline'
import { motion } from 'framer-motion'

interface UserProps {
  user: UserType
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

const text2color = (text: string): string => {
  if (!text) return '#00ff00'
  let hash = 0
  for (let i = 0; i < text.length; i++) hash = text.charCodeAt(i) + ((hash << 5) - hash)
  const golden_ratio = 0.618033988749895
  const hue = (hash * golden_ratio * 360) % 360
  const saturation = 70 + (Math.abs(hash >> 8) % 25)
  const lightness = 55 + (Math.abs(hash >> 16) % 15)
  return `hsl(${hue}, ${saturation}%, ${lightness}%)`
}

export function User({ user }: UserProps) {
  const userColor = text2color(user.key)
  const modCount = user.mods?.length || 0
  const balance = user.balance || 0

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3 }}
      className="group relative overflow-hidden rounded-2xl border bg-gradient-to-br from-black via-zinc-950 to-black p-6 transition-all hover:shadow-2xl"
      style={{ 
        borderColor: `${userColor}30`,
        boxShadow: `0 0 0 1px ${userColor}10`
      }}
    >
      {/* Gradient overlay on hover */}
      <div 
        className="absolute inset-0 opacity-0 transition-opacity duration-500 group-hover:opacity-100"
        style={{
          background: `radial-gradient(circle at top right, ${userColor}08, transparent 70%)`
        }}
      />

      <div className="relative z-10 space-y-5">
        {/* Header Section */}
        <div className="flex items-start justify-between gap-4">
          <div className="flex items-center gap-4 flex-1 min-w-0">
            {/* Avatar */}
            <div 
              className="flex h-16 w-16 flex-shrink-0 items-center justify-center rounded-xl border-2 bg-gradient-to-br from-black to-zinc-900 shadow-lg"
              style={{ 
                borderColor: `${userColor}50`,
                boxShadow: `0 0 20px ${userColor}20`
              }}
            >
              <KeyIcon className="h-8 w-8" style={{ color: userColor }} />
            </div>

            {/* Key Info */}
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 mb-2">
                <span 
                  className="text-sm font-bold uppercase tracking-wider"
                  style={{ color: `${userColor}90` }}
                >
                  User Key
                </span>
                <CheckCircleIcon 
                  className="h-4 w-4" 
                  style={{ color: userColor }}
                />
              </div>
              <div className="flex items-center gap-2">
                <code 
                  className="text-lg font-mono font-semibold tracking-tight"
                  style={{ color: userColor }}
                >
                  {shorten(user.key)}
                </code>
                <CopyButton content={user.key} size="sm" />
              </div>
            </div>
          </div>
        </div>

        {/* Stats Grid */}
        <div className="grid grid-cols-2 gap-4">
          {/* Modules Count */}
          <div 
            className="rounded-xl border p-4 backdrop-blur-sm transition-all hover:scale-105"
            style={{ 
              borderColor: `${userColor}20`,
              backgroundColor: `${userColor}05`
            }}
          >
            <div className="flex items-center gap-3">
              <div 
                className="rounded-lg p-2"
                style={{ backgroundColor: `${userColor}15` }}
              >
                <CubeIcon className="h-5 w-5" style={{ color: userColor }} />
              </div>
              <div>
                <div className="text-2xl font-bold" style={{ color: userColor }}>
                  {modCount}
                </div>
                <div className="text-xs font-medium text-white/50">
                  Modules
                </div>
              </div>
            </div>
          </div>

          {/* Balance */}
          <div 
            className="rounded-xl border p-4 backdrop-blur-sm transition-all hover:scale-105"
            style={{ 
              borderColor: `${userColor}20`,
              backgroundColor: `${userColor}05`
            }}
          >
            <div className="flex items-center gap-3">
              <div 
                className="rounded-lg p-2"
                style={{ backgroundColor: `${userColor}15` }}
              >
                <ClockIcon className="h-5 w-5" style={{ color: userColor }} />
              </div>
              <div>
                <div className="text-2xl font-bold" style={{ color: userColor }}>
                  {balance.toFixed(2)}
                </div>
                <div className="text-xs font-medium text-white/50">
                  Balance
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* Modules List Preview */}
        {modCount > 0 && (
          <div 
            className="rounded-xl border p-4"
            style={{ 
              borderColor: `${userColor}15`,
              backgroundColor: `${userColor}03`
            }}
          >
            <div className="mb-3 flex items-center gap-2">
              <CubeIcon className="h-4 w-4" style={{ color: `${userColor}80` }} />
              <span className="text-sm font-semibold" style={{ color: `${userColor}90` }}>
                Recent Modules
              </span>
            </div>
            <div className="flex flex-wrap gap-2">
              {user.mods?.slice(0, 5).map((mod: any, idx: number) => (
                <span
                  key={idx}
                  className="inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-sm font-medium transition-all hover:scale-105"
                  style={{ 
                    borderColor: `${userColor}30`,
                    backgroundColor: `${userColor}08`,
                    color: `${userColor}dd`
                  }}
                >
                  {mod.name || 'Unknown'}
                </span>
              ))}
              {modCount > 5 && (
                <span
                  className="inline-flex items-center rounded-lg px-3 py-1.5 text-sm font-medium"
                  style={{ color: `${userColor}70` }}
                >
                  +{modCount - 5} more
                </span>
              )}
            </div>
          </div>
        )}
      </div>

      {/* Hover border effect */}
      <div 
        className="absolute inset-0 rounded-2xl opacity-0 transition-opacity duration-500 group-hover:opacity-100"
        style={{
          boxShadow: `inset 0 0 0 1px ${userColor}30`
        }}
      />
    </motion.div>
  )
}