"use client"

import { UserType } from '@/app/types'
import { motion } from 'framer-motion'
import { User, Package, Wallet } from 'lucide-react'
import Link from 'next/link'

interface UserCardProps {
  user: UserType
}

const shorten = (str: string): string => {
  if (!str || str.length <= 12) return str
  return `${str.slice(0, 8)}...${str.slice(-4)}`
}

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

export function UserCard({ user }: UserCardProps) {
  const userColor = text2color(user.key)
  const modCount = user.mods?.length || 0

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3 }}
      className="group relative"
    >
      <Link href={`/users/${user.key}`}>
        <div
          className="relative overflow-hidden rounded-2xl border bg-black/40 backdrop-blur-xl transition-all duration-300 hover:bg-black/60 hover:scale-[1.02]"
          style={{ borderColor: `${userColor}33` }}
        >
          {/* Gradient overlay */}
          <div
            className="absolute inset-0 opacity-0 group-hover:opacity-100 transition-opacity duration-300"
            style={{
              background: `radial-gradient(circle at top right, ${userColor}15, transparent 70%)`
            }}
          />

          <div className="relative p-6">
            {/* Header */}
            <div className="flex items-start justify-between mb-4">
              <div className="flex items-center gap-4">
                <div
                  className="flex items-center justify-center w-14 h-14 rounded-xl border-2"
                  style={{
                    borderColor: `${userColor}66`,
                    backgroundColor: `${userColor}14`
                  }}
                >
                  <User className="w-7 h-7" style={{ color: userColor }} />
                </div>
                <div>
                  <h3
                    className="text-xl font-bold tracking-tight mb-1"
                    style={{ color: userColor }}
                  >
                    {shorten(user.key)}
                  </h3>
                  <p className="text-sm text-white/50 font-mono">{user.key}</p>
                </div>
              </div>
            </div>

            {/* Stats */}
            <div className="flex items-center gap-6 pt-4 border-t" style={{ borderColor: `${userColor}22` }}>
              <div className="flex items-center gap-2">
                <Package className="w-5 h-5" style={{ color: `${userColor}99` }} />
                <span className="text-base font-semibold" style={{ color: userColor }}>
                  {modCount}
                </span>
                <span className="text-sm text-white/50">
                  {modCount === 1 ? 'module' : 'modules'}
                </span>
              </div>

              <div className="flex items-center gap-2">
                <Wallet className="w-5 h-5" style={{ color: `${userColor}99` }} />
                <span className="text-base font-semibold" style={{ color: userColor }}>
                  {user.balance || 0}
                </span>
                <span className="text-sm text-white/50">balance</span>
              </div>
            </div>
          </div>

          {/* Hover indicator */}
          <div
            className="absolute bottom-0 left-0 right-0 h-1 transform scale-x-0 group-hover:scale-x-100 transition-transform duration-300 origin-left"
            style={{ backgroundColor: userColor }}
          />
        </div>
      </Link>
    </motion.div>
  )
}
