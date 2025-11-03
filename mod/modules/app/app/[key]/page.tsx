'use client'

import { useEffect, useState, useCallback } from 'react'
import { Client } from '@/app/block/client/client'
import { Loading } from '@/app/block/Loading'
import { UserType } from '@/app/types'
import { useUserContext } from '@/app/block/context/UserContext'
import {
  UserIcon,
  ArrowPathIcon,
  ClockIcon,
  KeyIcon,
  CubeIcon,
} from '@heroicons/react/24/outline'
import { CopyButton } from '@/app/block/CopyButton'
import { motion, AnimatePresence } from 'framer-motion'
import { ModuleCard } from '@/app/mods/ModuleCard'

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
  const saturation = 65 + (Math.abs(hash >> 8) % 35)
  const lightness = 50 + (Math.abs(hash >> 16) % 20)
  return `hsl(${hue}, ${saturation}%, ${lightness}%)`
}

export default function UserPage({ params }: { params: { key: string } }) {
  const user_key = params.key
  const { keyInstance, authLoading, client } = useUserContext()

  const [user, setUser] = useState<UserType | undefined>()
  const [error, setError] = useState<string>('')
  const [loading, setLoading] = useState<boolean>(true)
  const [syncing, setSyncing] = useState<boolean>(false)
  const [hasFetched, setHasFetched] = useState(false)

  const fetchUser = useCallback(async (update = false) => {
    try {
      update ? setSyncing(true) : setLoading(true)
      const params = { key: user_key }
      const foundUser = await client.call('user_info', params)
      console.log('fetched user', foundUser)

      if (foundUser) {
        setUser(foundUser as UserType)
        setError('')
      } else {
        setError(`User ${user_key} not found`)
      }
    } catch (err: any) {
      setError(err?.message || 'Failed to fetch user')
    } finally {
      setLoading(false)
      setSyncing(false)
    }
  }, [user_key, client])

  useEffect(() => {
    if ((!hasFetched && !authLoading) || user === undefined) {
      setHasFetched(true)
      fetchUser(false)
    }
  }, [hasFetched, fetchUser, authLoading, user])

  const handleSync = useCallback(() => {
    fetchUser(true)
  }, [fetchUser])

  if (authLoading || loading || user === undefined) return <Loading />

  const userColor = text2color(user.key)

  return (
    <div className="min-h-screen bg-black text-white user-page w-full">
      <div className="w-full max-w-full">
        <div className="w-full px-4 py-3 border-b border-white/10 bg-gradient-to-r from-black via-gray-900/50 to-black">
          <div className="flex flex-wrap items-center gap-3">
            <span
              className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-2xl font-bold"
              style={{ color: userColor, backgroundColor: `${userColor}14`, border: `2px solid ${userColor}33` }}
            >
              <UserIcon className="h-7 w-7" />
              User Profile
            </span>

            <div className="flex-1 min-w-[8px]" />

            {user.key && (
              <div
                className="inline-flex items-center gap-2 px-3 py-1.5 rounded-md text-base border bg-black/60"
                style={{ borderColor: `${userColor}33` }}
              >
                <KeyIcon className="h-4 w-4" style={{ color: userColor }} />
                <span className="font-mono select-all">{user.key}</span>
                <CopyButton size="sm" content={user.key} />
              </div>
            )}

            <button
              onClick={handleSync}
              disabled={syncing}
              className="inline-flex items-center justify-center h-10 w-10 rounded-lg border-2 transition font-bold text-lg"
              style={{ borderColor: `${userColor}4D`, color: userColor, backgroundColor: syncing ? `${userColor}10` : 'transparent' }}
              title="Sync"
            >
              <ArrowPathIcon className={`h-5 w-5 ${syncing ? 'animate-spin' : ''}`} />
            </button>
          </div>
        </div>

        <div className="w-full px-4 py-6 border-b border-white/10 bg-black/90">
          <div className="max-w-4xl mx-auto">
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <div className="p-4 rounded-lg border" style={{ borderColor: `${userColor}33`, backgroundColor: `${userColor}0a` }}>
                <div className="text-sm text-white/60 mb-1">Total Modules</div>
                <div className="text-3xl font-bold" style={{ color: userColor }}>{user.mods?.length || 0}</div>
              </div>
              <div className="p-4 rounded-lg border" style={{ borderColor: `${userColor}33`, backgroundColor: `${userColor}0a` }}>
                <div className="text-sm text-white/60 mb-1">Balance</div>
                <div className="text-3xl font-bold" style={{ color: userColor }}>{user.balance || 0}</div>
              </div>
              <div className="p-4 rounded-lg border" style={{ borderColor: `${userColor}33`, backgroundColor: `${userColor}0a` }}>
                <div className="text-sm text-white/60 mb-1">Address</div>
                <div className="text-lg font-mono select-all" style={{ color: userColor }}>{user.key}</div>
              </div>
            </div>
          </div>
        </div>

        <AnimatePresence mode="wait">
          <motion.div
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -10 }}
            transition={{ duration: 0.2 }}
            className="w-full p-4"
          >
            <div className="max-w-4xl mx-auto">
              <h2 className="text-2xl font-bold mb-4 flex items-center gap-2" style={{ color: userColor }}>
                <CubeIcon className="h-6 w-6" />
                User Modules ({user.mods?.length || 0})
              </h2>
              {user.mods && user.mods.length > 0 ? (
                <div className="space-y-4">
                  {user.mods.map((mod) => (
                    <ModuleCard key={mod.key || mod.name} mod={mod} />
                  ))}
                </div>
              ) : (
                <div className="h-[200px] flex items-center justify-center text-xl text-white/70">
                  <div className="text-center">
                    <CubeIcon className="h-16 w-16 mx-auto mb-4 opacity-70" />
                    <p className="font-bold">No Modules Yet</p>
                  </div>
                </div>
              )}
            </div>
          </motion.div>
        </AnimatePresence>
      </div>
    </div>
  )
}