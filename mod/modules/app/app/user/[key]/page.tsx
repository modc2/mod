'use client'

import { useEffect, useState } from 'react'
import { useParams } from 'next/navigation'
import { Loading } from '@/app/block/Loading'
import { Footer } from '@/app/block/footer/Footer'
import { useUserContext } from '@/app/block/context/UserContext'
import { UserType, ModuleType } from '@/app/types'
import { AlertCircle, User as UserIcon, Package, Hash } from 'lucide-react'
import { CopyButton } from '@/app/block/CopyButton'
import { text2color, shorten } from '@/app/utils'
import ModuleCard from '@/app/mod/explore/ModuleCard'

const USER_COLORS = [
  { from: 'from-purple-500/20', to: 'to-pink-500/20', text: 'text-purple-400', hover: 'from-purple-400 to-pink-400', border: 'border-purple-500/30' },
  { from: 'from-blue-500/20', to: 'to-cyan-500/20', text: 'text-blue-400', hover: 'from-blue-400 to-cyan-400', border: 'border-blue-500/30' },
  { from: 'from-green-500/20', to: 'to-emerald-500/20', text: 'text-green-400', hover: 'from-green-400 to-emerald-400', border: 'border-green-500/30' },
  { from: 'from-orange-500/20', to: 'to-red-500/20', text: 'text-orange-400', hover: 'from-orange-400 to-red-400', border: 'border-orange-500/30' },
  { from: 'from-yellow-500/20', to: 'to-amber-500/20', text: 'text-yellow-400', hover: 'from-yellow-400 to-amber-400', border: 'border-yellow-500/30' },
  { from: 'from-indigo-500/20', to: 'to-violet-500/20', text: 'text-indigo-400', hover: 'from-indigo-400 to-violet-400', border: 'border-indigo-500/30' },
]

interface UserProps {
  user: UserType
}

export function User({ user }: UserProps) {
  const colorIndex = user.key.split('').reduce((acc, char) => acc + char.charCodeAt(0), 0) % USER_COLORS.length
  const colors = USER_COLORS[colorIndex]
  const modCount = user.mods?.length || 0
  const userColor = text2color(user.key)

  return (
    <div className={`group relative bg-gradient-to-br ${colors.from} ${colors.to} border-2 rounded-xl p-8 hover:shadow-2xl transition-all duration-300 backdrop-blur-sm overflow-hidden`} style={{ borderColor: `${userColor}80`, boxShadow: `0 0 20px ${userColor}40` }}>
      <div className={`absolute inset-0 bg-gradient-to-br ${colors.from} via-transparent ${colors.to} opacity-0 group-hover:opacity-100 transition-opacity duration-500`} />
      
      <div className="relative z-10 space-y-6">
        <div className="flex items-start justify-between gap-4">
          <div className="flex items-center gap-4 flex-1 min-w-0">
            <div className={`flex-shrink-0 p-3 bg-gradient-to-br ${colors.from} ${colors.to} rounded-lg border border-white/20`}>
              <UserIcon className={`${colors.text}`} size={40} strokeWidth={2} />
            </div>
            <div className="flex-1 min-w-0">

              <div className="flex items-center gap-2">
                <code className="text-xl font-mono font-bold" style={{ color: userColor }} title={user.key}>
                  {shorten(user.key, 8)}
                </code>
                <CopyButton text={user.key} />
              </div>
            </div>
          </div>
        </div>

        <div className="grid grid-cols-2 gap-4">
          <div className="bg-black/40 border-2 rounded-lg p-5 backdrop-blur-sm" style={{ borderColor: `${userColor}60`, backgroundColor: `${userColor}10` }}>
            <div className="flex items-center gap-3 mb-2">
              <Package className={`${colors.text}/80`} size={24} strokeWidth={2} />
              <span className="text-xl text-white/60 font-medium">Modules</span>
            </div>
            <div className={`text-4xl font-bold ${colors.text}`}>
              {modCount}
            </div>
          </div>

          {user.balance !== undefined && (
            <div className="bg-black/40 border-2 rounded-lg p-5 backdrop-blur-sm" style={{ borderColor: `${userColor}60`, backgroundColor: `${userColor}10` }}>
              <div className="flex items-center gap-3 mb-2">
                <Hash className={`${colors.text}/80`} size={24} strokeWidth={2} />
                <span className="text-xl text-white/60 font-medium">Balance</span>
              </div>
              <div className={`text-4xl font-bold ${colors.text}`}>
                {user.balance.toFixed(2)}
              </div>
            </div>
          )}
        </div>

        {user.address && (
          <div className="bg-black/40 border-2 px-4 py-3 rounded-lg backdrop-blur-sm flex items-center gap-3" style={{ borderColor: `${userColor}60`, backgroundColor: `${userColor}10` }}>
            <Hash className={`${colors.text}/80`} size={20} strokeWidth={2} />
            <span className="text-lg text-white/60 font-medium">Address:</span>
            <code className={`text-lg ${colors.text} font-mono flex-1`} title={user.address}>
              {user.address}
            </code>
            <CopyButton text={user.address} />
          </div>
        )}
      </div>
    </div>
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

  const userMods = state.user?.mods || []

  return (
    <div className="min-h-screen bg-gradient-to-b from-black via-zinc-950 to-black text-white flex flex-col">
      {state.error && (
        <div className="max-w-6xl mx-auto w-full px-8 mt-10">
          <div className="flex items-start gap-5 p-8 rounded-3xl bg-gradient-to-br from-rose-500/20 to-rose-600/15 backdrop-blur-xl border-2 border-rose-500/30">
            <AlertCircle className="w-8 h-8 text-rose-400 flex-shrink-0 mt-1" />
            <div className="flex-1 min-w-0">
              <h3 className="text-2xl font-bold text-rose-300 mb-2">Error Loading User</h3>
              <p className="text-lg text-rose-200/90">{state.error}</p>
            </div>
          </div>
        </div>
      )}

      <main className="flex-1 px-8 py-16">
        <div className="max-w-6xl mx-auto">
          {state.loading ? (
            <div className="py-40 flex flex-col items-center justify-center gap-6">
              <Loading />
              <p className="text-white/60 text-xl font-medium">Loading user details...</p>
            </div>
          ) : state.user ? (
            <div className="space-y-8">
              <User user={state.user} />
              
              {userMods.length > 0 && (
                <div className="space-y-6">
                  <div className="grid grid-cols-1 gap-4">
                    {userMods.map((mod: ModuleType) => (
                      <div key={`${mod.name}-${mod.key}`} className="transform hover:scale-[1.02] transition-all duration-300 ease-out">
                        <ModuleCard mod={mod} />
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          ) : (
            <div className="py-40 flex flex-col items-center justify-center gap-6">
              <div className="w-20 h-20 rounded-3xl bg-white/5 flex items-center justify-center">
                <UserIcon className="w-10 h-10 text-white/20" />
              </div>
              <div className="text-center">
                <h3 className="text-3xl font-bold text-white/70 mb-3">User Not Found</h3>
                <p className="text-lg text-white/50">The requested user could not be found</p>
              </div>
            </div>
          )}
        </div>
      </main>

      <Footer />
    </div>
  )
}