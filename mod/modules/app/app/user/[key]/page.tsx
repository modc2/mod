'use client'

import { useEffect, useState } from 'react'
import { useParams } from 'next/navigation'
import { Loading } from '@/app/block/Loading'
import { Footer } from '@/app/block/Footer'
import { User } from './User'
import { useUserContext } from '@/app/block/context/UserContext'
import { UserType } from '@/app/types'
import { AlertCircle, UserIcon } from 'lucide-react'
import { text2color } from '@/app/utils'

export default function UserPage() {
  const params = useParams()
  const userKey = params?.key as string
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
      if (!client || !userKey) {
        setState({ user: null, loading: false, error: 'No client or user key available' })
        return
      }

      setState(prev => ({ ...prev, loading: true, error: null }))
      
      try {
        // Fetch user details with their modules
        const userData: UserType = await client.call('user_info', { key: userKey })
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
  }, [userKey, client])

  const userColor = userKey ? text2color(userKey) : '#10b981'

  return (
    <div className="min-h-screen bg-gradient-to-b from-black via-zinc-950 to-black text-white flex flex-col">
      {/* Header Section */}
      <div className="border-b border-white/10 bg-black/40 backdrop-blur-xl">
        <div className="max-w-5xl mx-auto px-6 py-8">
          <div className="flex items-center gap-4">
            <div 
              className="w-16 h-16 rounded-2xl border-2 flex items-center justify-center shadow-lg"
              style={{
                borderColor: `${userColor}50`,
                backgroundColor: `${userColor}10`,
                boxShadow: `0 0 30px ${userColor}20`
              }}
            >
              <UserIcon className="w-8 h-8" style={{ color: userColor }} />
            </div>
            <div>
              <h1 className="text-3xl font-bold mb-1" style={{ color: userColor }}>
                User Profile
              </h1>
              <p className="text-white/50 text-sm font-mono">
                {userKey || 'Loading...'}
              </p>
            </div>
          </div>
        </div>
      </div>

      {/* Error Display */}
      {state.error && (
        <div className="max-w-5xl mx-auto w-full px-6 mt-8">
          <div className="flex items-start gap-4 p-6 rounded-2xl border border-rose-500/25 bg-gradient-to-br from-rose-500/15 to-rose-600/10 backdrop-blur-xl">
            <AlertCircle className="w-6 h-6 text-rose-400 flex-shrink-0 mt-0.5" />
            <div className="flex-1 min-w-0">
              <h3 className="text-lg font-semibold text-rose-300 mb-1">Error Loading User</h3>
              <p className="text-sm text-rose-200/80">{state.error}</p>
            </div>
          </div>
        </div>
      )}

      {/* Main Content */}
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
              
              {/* Additional User Stats */}
              {state.user.mods && state.user.mods.length > 0 && (
                <div 
                  className="rounded-2xl border p-6 backdrop-blur-sm"
                  style={{
                    borderColor: `${userColor}20`,
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
                        className="p-4 rounded-xl border transition-all hover:scale-105 cursor-pointer"
                        style={{
                          borderColor: `${userColor}30`,
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
