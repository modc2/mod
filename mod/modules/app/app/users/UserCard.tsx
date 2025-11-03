'use client'

import { UserType } from '@/app/types'
import { motion } from 'framer-motion'
import { UserCircleIcon, CubeIcon, CurrencyDollarIcon } from '@heroicons/react/24/outline'
import { CopyButton } from '@/app/block/CopyButton'
import Link from 'next/link'

interface UserCardProps {
  user: UserType
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

  return (
    <Link href={`/${user.key}`}>
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        className="rounded-xl border bg-gradient-to-br from-black/60 to-black/40 backdrop-blur-sm overflow-hidden cursor-pointer hover:scale-[1.02] transition-transform"
        style={{ borderColor: `${userColor}33` }}
      >
        <div className="p-4">
          <div className="flex items-center gap-4">
            <div
              className="flex-shrink-0 w-16 h-16 rounded-xl flex items-center justify-center"
              style={{ backgroundColor: `${userColor}14`, border: `2px solid ${userColor}33` }}
            >
              <UserCircleIcon className="w-10 h-10" style={{ color: userColor }} />
            </div>

            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-3 mb-2">
                <h3 className="text-lg font-bold font-mono select-all flex items-center gap-2" style={{ color: userColor }}>
                  {user.key}
                  <span onClick={(e) => e.preventDefault()}>
                    <CopyButton content={user.key} size="sm" />
                  </span>
                </h3>
              </div>
              
              <div className="flex items-center gap-2 mb-2">
                <div className="flex items-center gap-2 px-3 py-1 rounded-md text-sm border" style={{ borderColor: `${userColor}33`, backgroundColor: `${userColor}0f` }}>
                  <CubeIcon className="w-4 h-4" style={{ color: userColor }} />
                  <span className="font-semibold" style={{ color: userColor }}>{user.mods?.length || 0}</span>
                </div>
                <div className="flex items-center gap-2 px-3 py-1 rounded-md text-sm border" style={{ borderColor: `${userColor}33`, backgroundColor: `${userColor}0f` }}>
                  <CurrencyDollarIcon className="w-4 h-4" style={{ color: userColor }} />
                  <span className="font-semibold" style={{ color: userColor }}>{user.balance || 0}</span>
                </div>
              </div>

              {user.mods && user.mods.length > 0 && (
                <div className="flex flex-wrap gap-2">
                  {user.mods.slice(0, 5).map((mod, idx) => (
                    <span
                      key={idx}
                      className="px-3 py-1 rounded-md text-xs font-mono border"
                      style={{
                        borderColor: `${userColor}33`,
                        backgroundColor: `${userColor}0a`,
                        color: `${userColor}cc`
                      }}
                    >
                      {mod.name}
                    </span>
                  ))}
                  {user.mods.length > 5 && (
                    <span className="px-3 py-1 text-xs" style={{ color: `${userColor}99` }}>
                      +{user.mods.length - 5} more
                    </span>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>
      </motion.div>
    </Link>
  )
}
