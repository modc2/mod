'use client'

import { useMemo } from 'react'
import Link from 'next/link'
import { motion } from 'framer-motion'
import { User, Package, Wallet } from 'lucide-react'
import { UserType } from '@/app/types'

interface UserCardProps {
  user: UserType
  className?: string
}

type Hsl = { h: number; s: number; l: number }

// Shorten long strings for display
const shorten = (str: string): string => {
  if (!str || str.length <= 12) return str
  return `${str.slice(0, 8)}...${str.slice(-4)}`
}

// Generate a stable HSL color from text
const text2hsl = (text?: string): Hsl => {
  if (!text) return { h: 140, s: 100, l: 50 } // default: green-ish
  let hash = 0
  for (let i = 0; i < text.length; i++) {
    hash = text.charCodeAt(i) + ((hash << 5) - hash)
  }
  const goldenRatio = 0.618033988749895
  const hue = Math.abs((hash * goldenRatio * 360) % 360)
  const saturation = 60 + (Math.abs(hash >> 8) % 30) // 60–90
  const lightness = 48 + (Math.abs(hash >> 16) % 16) // 48–64
  return { h: hue, s: saturation, l: lightness }
}

// Build an HSLA color string using modern syntax
const hsla = (c: Hsl, a = 1) => `hsl(${c.h} ${c.s}% ${c.l}% / ${a})`

// Minimal normalized module shape for safe rendering
type NormalizedModule = {
  key: string
  label: string
  slug?: string
  id?: string
}

// Try to build a usable href from a module
const moduleHref = (m: NormalizedModule): string => {
  // Adjust this to your app's actual routing
  if (m.slug) return `/modules/${encodeURIComponent(m.slug)}`
  if (m.id) return `/modules/${encodeURIComponent(m.id)}`
  return `/modules/${encodeURIComponent(m.key)}`
}

// Normalize various possible module shapes into a consistent structure
const normalizeModules = (mods: unknown): NormalizedModule[] => {
  if (!Array.isArray(mods)) return []
  return mods
    .map((item, idx) => {
      if (typeof item === 'string') {
        const label = item
        return { key: item, label, slug: item, id: item }
      }
      if (item && typeof item === 'object') {
        const anyItem = item as Record<string, unknown>
        const key =
          (anyItem.key as string) ||
          (anyItem.slug as string) ||
          (anyItem.id as string) ||
          (anyItem.name as string) ||
          (anyItem.title as string) ||
          `mod-${idx}`

        const label =
          (anyItem.name as string) ||
          (anyItem.title as string) ||
          (anyItem.slug as string) ||
          (anyItem.key as string) ||
          (anyItem.id as string) ||
          key

        return {
          key,
          label,
          slug: (anyItem.slug as string) || undefined,
          id: (anyItem.id as string) || undefined,
        }
      }
      return null
    })
    .filter(Boolean) as NormalizedModule[]
}

export function UserCard({ user, className = '' }: UserCardProps) {
  const color = useMemo(() => text2hsl(user?.key), [user?.key])
  const balance = user?.balance ?? 0
  const formattedBalance = useMemo(() => new Intl.NumberFormat().format(balance), [balance])

  const modules = useMemo(() => normalizeModules(user?.mods), [user?.mods])
  const modCount = modules.length
  const maxChips = 6
  const visibleModules = modules.slice(0, maxChips)
  const remaining = Math.max(0, modCount - visibleModules.length)

  return (
    <motion.div
      initial={{ opacity: 0, y: 18 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.28, ease: 'easeOut' }}
      className={`group relative ${className}`}
    >
      <Link
        href={`/users/${user.key}`}
        aria-label={`View ${user.key} profile`}
        title={user.key}
        className="block"
      >
        <div
          className="relative overflow-hidden rounded-2xl border bg-black/40 backdrop-blur-xl transition-all duration-300 group-hover:bg-black/60 group-hover:scale-[1.02] focus-within:scale-[1.02]"
          style={{
            borderColor: hsla(color, 0.22),
            boxShadow: '0 0 0 0 transparent',
          }}
        >
          {/* Gradient overlay */}
          <div
            className="absolute inset-0 opacity-0 group-hover:opacity-100 transition-opacity duration-300 pointer-events-none"
            style={{
              background: `radial-gradient(80% 80% at 100% 0%, ${hsla(color, 0.12)} 0%, transparent 70%)`,
            }}
          />

          <div className="relative p-6">
            {/* Header */}
            <div className="flex items-start justify-between mb-4">
              <div className="flex items-center gap-4">
                <div
                  className="flex items-center justify-center w-14 h-14 rounded-xl border-2"
                  style={{
                    borderColor: hsla(color, 0.45),
                    backgroundColor: hsla(color, 0.1),
                  }}
                >
                  <User
                    className="w-7 h-7"
                    style={{ color: hsla(color, 0.95) }}
                    aria-hidden="true"
                  />
                </div>
                <div>
                  <h3
                    className="text-xl font-bold tracking-tight mb-1"
                    style={{ color: hsla(color, 0.98) }}
                  >
                    {shorten(user.key)}
                  </h3>
                  <p
                    className="text-sm text-white/55 font-mono truncate max-w-[18rem]"
                    title={user.key}
                  >
                    {user.key}
                  </p>
                </div>
              </div>

              {/* Focus ring indicator (keyboard users) */}
              <div
                className="rounded-full opacity-0 group-focus-within:opacity-100 transition-opacity"
                style={{ boxShadow: `0 0 0 2px ${hsla(color, 0.5)}` }}
                aria-hidden="true"
              />
            </div>

            {/* Stats */}
            <div
              className="flex flex-wrap items-center gap-x-8 gap-y-3 pt-4 border-t"
              style={{ borderColor: hsla(color, 0.18) }}
            >
              <div className="flex items-center gap-2">
                <Package
                  className="w-5 h-5"
                  style={{ color: hsla(color, 0.75) }}
                  aria-hidden="true"
                />
                <span
                  className="text-base font-semibold"
                  style={{ color: hsla(color, 0.98) }}
                >
                  {modCount}
                </span>
                <span className="text-sm text-white/60">
                  {modCount === 1 ? 'module' : 'modules'}
                </span>
              </div>

              <div className="flex items-center gap-2">
                <Wallet
                  className="w-5 h-5"
                  style={{ color: hsla(color, 0.75) }}
                  aria-hidden="true"
                />
                <span
                  className="text-base font-semibold"
                  style={{ color: hsla(color, 0.98) }}
                >
                  {formattedBalance}
                </span>
                <span className="text-sm text-white/60">balance</span>
              </div>
            </div>

            {/* Modules list */}
            <div className="mt-4">
              <div className="mb-2 text-sm text-white/70">Created modules</div>

              {modCount === 0 ? (
                <div
                  className="text-sm text-white/45 bg-white/5 border rounded-md px-3 py-2"
                  style={{ borderColor: hsla(color, 0.12) }}
                >
                  No modules yet
                </div>
              ) : (
                <div className="flex flex-wrap gap-2">
                  {visibleModules.map((m, i) => (
                    <motion.span
                      key={m.key}
                      initial={{ opacity: 0, y: 6 }}
                      animate={{ opacity: 1, y: 0 }}
                      transition={{ duration: 0.22, delay: 0.02 * i }}
                    >
                      <Link
                        href={moduleHref(m)}
                        className="inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs transition-colors hover:brightness-110 focus-visible:outline-none focus-visible:ring-2"
                        style={{
                          borderColor: hsla(color, 0.28),
                          backgroundColor: hsla(color, 0.08),
                          color: hsla(color, 0.98),
                          boxShadow: `0 0 0 0 ${hsla(color, 0)}`,
                        }}
                        title={m.label}
                        aria-label={`Open module ${m.label}`}
                      >
                        <Package className="w-3.5 h-3.5" aria-hidden="true" />
                        <span className="truncate max-w-[10rem]">{m.label}</span>
                      </Link>
                    </motion.span>
                  ))}

                  {remaining > 0 && (
                    <span
                      className="inline-flex items-center rounded-full border px-2.5 py-1 text-xs text-white/75"
                      style={{
                        borderColor: hsla(color, 0.16),
                        backgroundColor: hsla(color, 0.06),
                      }}
                      title={`${remaining} more ${remaining === 1 ? 'module' : 'modules'}`}
                    >
                      +{remaining} more
                    </span>
                  )}
                </div>
              )}
            </div>
          </div>

          {/* Hover indicator */}
          <div
            className="absolute bottom-0 left-0 right-0 h-1 transform scale-x-0 group-hover:scale-x-100 transition-transform duration-300 origin-left"
            style={{ backgroundColor: hsla(color, 1) }}
          />
        </div>
      </Link>
    </motion.div>
  )
}