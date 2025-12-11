'use client'

import { useEffect, useState } from 'react'
import { useParams } from 'next/navigation'
import { useUserContext } from '@/bloc/context'
import { UserType } from '@/bloc/types'
import { Loading } from '@/bloc/ui/Loading'
import { UserCard } from '@/bloc/user/UserCard'

// 
import  Transfer  from '@/bloc/user/wallet/transfer'
import RegMod from '@/bloc/user/wallet/reg'
import UpdateMod from '@/bloc/user/wallet/update'
import ClaimMod from '@/bloc/user/wallet/claim'
import {UserModules} from '@/bloc/user/wallet/usermods/UserModules'


type TabType = 'mods' | 'sign' | 'transfer' | 'register' | 'update' | 'claim'

export default function UserPage() {
  const params = useParams()
  const userKey = params?.user as string
  const { client, user } = useUserContext()
  const [userData, setUserData] = useState<UserType | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [activeTab, setActiveTab] = useState<TabType>('mods')
  const { user: currentUser } = useUserContext()
  const myMod = currentUser && currentUser.key === userKey
  
  useEffect(() => {
    const fetchUser = async () => {
      if (!client || !userKey) return
      setLoading(true)
      setError(null)
      try {
        const data = await client.call('user', { address: userKey })
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

  let tabs: { id: TabType; label: string; color: string }[] = [
    { id: 'transfer', label: 'transfer', color: 'blue' },
    { id: 'mods', label: 'mods', color: 'purple' },
    { id: 'register', label: 'register', color: 'green' },
    { id: 'update', label: 'update', color: 'orange' },
    { id: 'claim', label: 'claim', color: 'pink' },
  ]

  if (!myMod) {
    tabs = tabs.filter(tab => tab.id === 'mods' )
  }

  const getButtonColors = (tabColor: string, isActive: boolean) => {
    const colorMap: Record<string, { active: string; inactive: string }> = {
      blue: {
        active: 'bg-gradient-to-r from-blue-500 to-cyan-500 text-white border-2 border-blue-300 shadow-2xl shadow-blue-500/50',
        inactive: 'bg-blue-500/20 text-blue-300 border-2 border-blue-500/40 hover:bg-blue-500/30 hover:border-blue-400/60'
      },
      purple: {
        active: 'bg-gradient-to-r from-purple-500 to-pink-500 text-white border-2 border-purple-300 shadow-2xl shadow-purple-500/50',
        inactive: 'bg-purple-500/20 text-purple-300 border-2 border-purple-500/40 hover:bg-purple-500/30 hover:border-purple-400/60'
      },
      green: {
        active: 'bg-gradient-to-r from-green-500 to-emerald-500 text-white border-2 border-green-300 shadow-2xl shadow-green-500/50',
        inactive: 'bg-green-500/20 text-green-300 border-2 border-green-500/40 hover:bg-green-500/30 hover:border-green-400/60'
      },
      orange: {
        active: 'bg-gradient-to-r from-orange-500 to-yellow-500 text-white border-2 border-orange-300 shadow-2xl shadow-orange-500/50',
        inactive: 'bg-orange-500/20 text-orange-300 border-2 border-orange-500/40 hover:bg-orange-500/30 hover:border-orange-400/60'
      },
      pink: {
        active: 'bg-gradient-to-r from-pink-500 to-rose-500 text-white border-2 border-pink-300 shadow-2xl shadow-pink-500/50',
        inactive: 'bg-pink-500/20 text-pink-300 border-2 border-pink-500/40 hover:bg-pink-500/30 hover:border-pink-400/60'
      }
    }
    return isActive ? colorMap[tabColor].active : colorMap[tabColor].inactive
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-black via-gray-900 to-black text-white">
      <main className="flex-1 px-6 py-8">
        <div className="max-w-7xl mx-auto space-y-6">
          <div className="mb-8">
            <UserCard user={userData}/>
          </div>

          <div className="flex flex-wrap gap-3 mb-6">
            {tabs.map((tab) => (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`px-6 py-3 rounded-xl font-black text-base uppercase transition-all duration-300 ${
                  getButtonColors(tab.color, activeTab === tab.id)
                } ${activeTab === tab.id ? 'scale-105' : 'hover:scale-105'}`}
              >
                {tab.label}
              </button>
            ))}
          </div>

          <div className="bg-gradient-to-r from-purple-500/10 via-pink-500/10 to-blue-500/10 border-2 border-purple-500/30 rounded-2xl p-6 backdrop-blur-xl shadow-2xl shadow-purple-500/20">
            {activeTab === 'mods' && <UserModules userData={userData} />}
            {activeTab === 'transfer' && client?.key && user && <Transfer />}
            {activeTab === 'register' && client?.key && user && <RegMod />}
            {activeTab === 'update' && client?.key && user && <UpdateMod />}
            {activeTab === 'claim' && client?.key && user && <ClaimMod />}

          </div>
        </div>
      </main>
    </div>
  )
}
