'use client'

import { useEffect, useState } from 'react'
import { useParams } from 'next/navigation'
import { useUserContext } from '@/app/block/context/UserContext'
import { UserType } from '@/app/types'
import { Loading } from '@/app/block/Loading'
import { UserCard } from '@/app/user/explore/UserCard'
import ModCard from '@/app/mod/explore/ModCard'
import { Footer } from '@/app/block/footer/Footer'

type TabType = 'mods'

function UserModules({ userData }: { userData: UserType }) {
  const { mods } = userData
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-6">
      {mods.map((mod) => (
        <ModCard mod={mod} key={mod.key} />
      ))}
    </div>
  )
}

export default function UserPage() {
  const params = useParams()
  const userKey = params?.key as string
  const { client, keyInstance } = useUserContext()
  const [user, setUser] = useState<UserType | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [activeTab, setActiveTab] = useState<TabType>('mods')

  useEffect(() => {
    const fetchUser = async () => {
      if (!client || !userKey) return
      setLoading(true)
      setError(null)
      try {
        const userData = await client.call('user_info', { key: userKey })
        console.log('Fetched user data:', userData)
        setUser(userData as UserType)
      } catch (err: any) {
        console.error('Error fetching user:', err)
        setError(err?.message || 'Failed to load user')
      } finally {
        setLoading(false)
      }
    }
    fetchUser()
  }, [client, userKey])

  if (loading) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-black via-gray-900 to-black flex items-center justify-center">
        <Loading />
      </div>
    )
  }

  if (error || !user) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-black via-gray-900 to-black flex items-center justify-center">
        <div className="text-center">
          <h1 className="text-4xl font-bold text-red-400 mb-4">ERROR</h1>
          <p className="text-xl text-white/70">{error || 'User not found'}</p>
        </div>
      </div>
    )
  }

  const tabs: { id: TabType; label: string }[] = [
    { id: 'mods', label: 'mods' },
  ]

  return (
    <div className="min-h-screen bg-gradient-to-br from-black via-gray-900 to-black text-white">
      <main className="flex-1 px-6 py-8">
        <div className="max-w-7xl mx-auto space-y-6">
          <div className="mb-8">
            <UserCard user={user} />
          </div>

          <div className="flex flex-wrap gap-3 mb-6">
            {tabs.map((tab) => (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`px-6 py-3 rounded-xl font-black text-base uppercase transition-all duration-300 ${
                  activeTab === tab.id
                    ? 'bg-gradient-to-r from-purple-500 to-pink-500 text-white border-2 border-purple-300 shadow-2xl shadow-purple-500/50 scale-105'
                    : 'bg-purple-500/20 text-purple-300 border-2 border-purple-500/40 hover:bg-purple-500/30 hover:scale-105 hover:border-purple-400/60'
                }`}
              >
                {tab.label}
              </button>
            ))}
          </div>

          <div className="bg-gradient-to-r from-purple-500/10 via-pink-500/10 to-blue-500/10 border-2 border-purple-500/30 rounded-2xl p-6 backdrop-blur-xl shadow-2xl shadow-purple-500/20">
            {activeTab === 'mods' && <UserModules userData={user} />}
          </div>
        </div>
      </main>
      <Footer />
    </div>
  )
}