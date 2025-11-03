'use client'

import { useEffect, useState } from 'react'
import { useParams } from 'next/navigation'
import { Loading } from '@/app/block/Loading'
import { Footer } from '@/app/block/Footer'
import { useUserContext } from '@/app/block/context/UserContext'
import { UserType } from '@/app/types'
import { AlertCircle, UserIcon } from 'lucide-react'
import { CopyButton } from '@/app/block/CopyButton'
import { KeyIcon, CubeIcon, ClockIcon, CheckCircleIcon } from '@heroicons/react/24/outline'
import { motion } from 'framer-motion'
import { text2color, shorten } from '@/app/utils'
interface UserProps {
  user: UserType
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
      className="group relative overflow-hidden rounded-2xl bg-gradient-to-br from-black via-zinc-950 to-black p-6 transition-all hover:shadow-2xl"
      style={{ 
        border: `2px solid ${userColor}30`,
        boxShadow: `0 0 0 1px ${userColor}10`
      }}
    >
      <div 
        className="absolute inset-0 opacity-0 transition-opacity duration-500 group-hover:opacity-100"
        style={{
          background: `radial-gradient(circle at top right, ${userColor}08, transparent 70%)`
        }}
      />

      <div className="relative z-10 space-y-5">
        <div className="flex items-start justify-between gap-4">
          <div className="flex items-center gap-4 flex-1 min-w-0">
            <div 
              className="flex h-16 w-16 flex-shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-black to-zinc-900 shadow-lg"
              style={{ 
                border: `2px solid ${userColor}50`,
                boxShadow: `0 0 20px ${userColor}20`
              }}
            >
              <KeyIcon className="h-8 w-8" style={{ color: userColor }} />
            </div>

            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 mb-2">
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

        <div className="grid grid-cols-2 gap-4">
          <div 
            className="rounded-xl p-4 backdrop-blur-sm transition-all hover:scale-105"
            style={{ 
              border: `1px solid ${userColor}20`,
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

          <div 
            className="rounded-xl p-4 backdrop-blur-sm transition-all hover:scale-105"
            style={{ 
              border: `1px solid ${userColor}20`,
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

        {modCount > 0 && (
          <div 
            className="rounded-xl p-4"
            style={{ 
              border: `1px solid ${userColor}15`,
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
                  className="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium transition-all hover:scale-105"
                  style={{ 
                    border: `1px solid ${userColor}30`,
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

      <div 
        className="absolute inset-0 rounded-2xl opacity-0 transition-opacity duration-500 group-hover:opacity-100"
        style={{
          boxShadow: `inset 0 0 0 1px ${userColor}30`
        }}
      />
    </motion.div>
  )
}


export default function UserPage() {
  const user = useParams()
  const { client } = useUserContext()
  
  const [state, setState] = useState<{
    user: UserType | null
    loading: boolean
    error: string | null
  }>({
    user: null,
    loading: true,
    error: null
  })

  useEffect(() => {
    const fetchUserDetails = async () => {
      if (!client || !user.key) {
        setState({ user: null, loading: false, error: 'No client or user key available' })
        return
      }

      setState(prev => ({ ...prev, loading: true, error: null }))
      
      try {
        const userData: UserType = await client.call('user_info', { key: user.key })
        setState({ user: userData, loading: false, error: null })
      } catch (err: any) {
        console.error('Failed to fetch user details:', err)
        setState({
          user: null,
          loading: false,
          error: err.message || 'Failed to load user details'
        })
      }
    }

    fetchUserDetails()
  }, [user.key, client])

  const userColor = user.key ? text2color(user.key) : '#10b981'

  return (
    <div className="min-h-screen bg-gradient-to-b from-black via-zinc-950 to-black text-white flex flex-col">
      {state.error && (
        <div className="max-w-5xl mx-auto w-full px-6 mt-8">
          <div className="flex items-start gap-4 p-6 rounded-2xl bg-gradient-to-br from-rose-500/15 to-rose-600/10 backdrop-blur-xl" style={{border: '1px solid rgba(244, 63, 94, 0.25)'}}>
            <AlertCircle className="w-6 h-6 text-rose-400 flex-shrink-0 mt-0.5" />
            <div className="flex-1 min-w-0">
              <h3 className="text-lg font-semibold text-rose-300 mb-1">Error Loading User</h3>
              <p className="text-sm text-rose-200/80">{state.error}</p>
            </div>
          </div>
        </div>
      )}

      <main className="flex-1 px-6 py-12">
        <div className="max-w-5xl mx-auto">
          {state.loading ? (
            <div className="py-32 flex flex-col items-center justify-center gap-4">
              <Loading />
              <p className="text-white/50 text-sm">Loading user details...</p>
            </div>
          ) : state.user ? (
            <div className="space-y-6">
              <User user={state.user} />
              
              {state.user.mods && state.user.mods.length > 0 && (
                <div 
                  className="rounded-2xl p-6 backdrop-blur-sm"
                  style={{
                    border: `1px solid ${userColor}20`,
                    backgroundColor: `${userColor}05`
                  }}
                >
                  <h2 className="text-xl font-bold mb-4" style={{ color: userColor }}>
                    All Modules ({state.user.mods.length})
                  </h2>
                  <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
                    {state.user.mods.map((mod: any, idx: number) => (
                      <div
                        key={idx}
                        className="p-4 rounded-xl transition-all hover:scale-105 cursor-pointer"
                        style={{
                          border: `1px solid ${userColor}30`,
                          backgroundColor: `${userColor}08`
                        }}
                      >
                        <div className="font-semibold mb-1" style={{ color: userColor }}>
                          {mod.name || 'Unnamed Module'}
                        </div>
                        {mod.description && (
                          <p className="text-sm text-white/60 line-clamp-2">
                            {mod.description}
                          </p>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          ) : (
            <div className="py-32 flex flex-col items-center justify-center gap-4">
              <div className="w-16 h-16 rounded-2xl bg-white/5 flex items-center justify-center">
                <UserIcon className="w-8 h-8 text-white/20" />
              </div>
              <div className="text-center">
                <h3 className="text-xl font-semibold text-white/60 mb-2">User Not Found</h3>
                <p className="text-sm text-white/40">The requested user could not be found</p>
              </div>
            </div>
          )}
        </div>
      </main>

      <Footer />
    </div>
  )
}
