'use client'

import { useEffect, useState } from 'react'
import { useParams } from 'next/navigation'
import { useUserContext } from '@/app/context'
import { UserType } from '@/app/types'
import { Loading } from '@/app/block/ui/Loading'
import { UserCard } from '@/app/user/explore/UserCard'
import { Transfer } from '@/app/user/wallet/Transfer'
import { SignVerify } from '@/app/user/wallet/SignVerify'
import {UserModules} from '@/app/user/wallet/UserModules'


type TabType = 'mods' | 'sign' | 'transfer'

export default function UserPage() {
  const params = useParams()
  const userKey = params?.user as string
  const { client, user } = useUserContext()
  const [userData, setUserData] = useState<UserType | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [activeTab, setActiveTab] = useState<TabType>('mods')

  useEffect(() => {
    const fetchUser = async () => {
      if (!client || !userKey) return
      setLoading(true)
      setError(null)
      try {
        const data = await client.call('user_info', { key: userKey })
        setUserData(data as UserType)
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

  if (error || !userData) {
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
    { id: 'transfer', label: 'transfer' },
    { id: 'mods', label: 'mods' },
    { id: 'sign', label: 'sign & verify' }
  ]

  return (
    <div className="min-h-screen bg-gradient-to-br from-black via-gray-900 to-black text-white">
      <main className="flex-1 px-6 py-8">
        <div className="max-w-7xl mx-auto space-y-6">
          <div className="mb-8">
            <UserCard user={userData} mode="page" />
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
            {activeTab === 'mods' && <UserModules userData={userData} />}
            {activeTab === 'sign' && client?.key && <SignVerify keyInstance={client.key} />}
            {activeTab === 'transfer' && client?.key && user && <Transfer />}
          </div>
        </div>
      </main>
    </div>
  )
}
