"use client";

import { useEffect, useState } from 'react'
import { useParams } from 'next/navigation'
import { userContext } from '@/context'
import { UserType } from '@/types'
import { Loading } from '@/ui/Loading'
import { UserCard } from '@/user/UserCard'
import Transfer from '@/user/transfer'
import Mods from '@/user/mods'
import ContractsInterface from '@/user/contracts/Contracts'
import { Admin } from '@/user/admin/Admin'
import { Portfolio } from '@/user/portfolio/Portfolio'
import { Billing } from '@/user/billing'
import Create from '@/user/create'
import { Txs } from '@/user/txs'
import { ethers } from 'ethers'
import { getChainConfig } from '@/network/chainConfig'
import MarketABI from '@/contracts//market/Market.sol/Market.json'
import { MagnifyingGlassIcon } from '@heroicons/react/24/outline'

type TabType = 'mods' | 'sign' | 'transfer' | 'admin' | 'contracts' | 'billing' | 'portfolio' | 'create' | 'txs'

const DEFAULT_TABS: { id: TabType; label: string; color: string }[] = [
  { id: 'admin', label: 'admin', color: '#ef4444' },
  { id: 'billing', label: 'billing', color: '#eab308' },
  { id: 'contracts', label: 'contracts', color: '#14b8a6' },
  { id: 'mods', label: 'mods', color: '#a855f7' },
  { id: 'portfolio', label: 'portfolio', color: '#6366f1' },
  { id: 'transfer', label: 'transfer', color: '#3b82f6' },
  { id: 'txs', label: 'txs', color: '#06b6d4' },
]

export default function UserPage() {
  const params = useParams()
  const userKey = params?.user as string
  const { client, user } = userContext()
  const [userData, setUserData] = useState<UserType | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [marketCredit, setMarketCredit] = useState<number>(0)
  const { user: currentUser } = userContext()
  const myMod = currentUser && currentUser.key === userKey

  const [activeTab, setActiveTab] = useState<TabType>(() => {
    if (typeof window !== 'undefined') {
      const saved = localStorage.getItem(`user_tab_${userKey}`)
      return (saved as TabType) || 'mods'
    }
    return 'mods'
  })

  const [userTabs, setUserTabs] = useState<{ id: TabType; label: string; color: string }[]>(() => {
    if (typeof window !== 'undefined') {
      const saved = localStorage.getItem('user_page_tabs')
    }
    return DEFAULT_TABS
  })

  const [searchTerm, setSearchTerm] = useState('')

  useEffect(() => {
    if (typeof window !== 'undefined') {
      localStorage.setItem('user_page_tabs', JSON.stringify(userTabs))
    }
  }, [userTabs])

  useEffect(() => {
    if (typeof window !== 'undefined' && userKey) {
      localStorage.setItem(`user_tab_${userKey}`, activeTab)
    }
  }, [activeTab, userKey])

  useEffect(() => {
    const fetchUser = async () => {
      if (!client || !userKey) return
      setLoading(true)
      setError(null)
      try {
        const data = await client.call('user', { key: userKey , expand: true, update:false })
        setUserData(data as UserType)
        
        if (user?.key && typeof window !== 'undefined' && window.ethereum) {
          try {
            const chainConfig = getChainConfig()
            if (!chainConfig) throw new Error('Chain config not found')
            
            const provider = new ethers.BrowserProvider(window.ethereum)
            const marketAddress = chainConfig.contracts.Market.address
            const marketContract = new ethers.Contract(marketAddress, MarketABI.abi, provider)
            
            let balance = await marketContract.balanceOf(user.key, 'Market')
            let decimals = await marketContract.decimals()
            decimals = Number(decimals) + 10
            balance = parseFloat(ethers.formatUnits(balance, decimals))
        
            setMarketCredit(balance)
          } catch (err) {
            console.error('Error fetching market credit:', err)
            setMarketCredit(0)
          }
        }
      } catch (err: any) {
        console.error('Error fetching user:', err)
        setError(err?.message || 'Failed to load user')
      } finally {
        setLoading(false)
      }
    }
    fetchUser()
  }, [client, userKey, user])

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <Loading />
      </div>
    )
  }

  if (error || !userData) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="text-center">
          <h1 className="text-4xl font-bold text-red-400 mb-4">ERROR</h1>
          <p className="text-xl text-white/70">{error || 'User not found'}</p>
        </div>
      </div>
    )
  }

  let displayTabs = myMod ? userTabs : userTabs.filter(tab => tab.id === 'mods')
  
  const filteredTabs = displayTabs.filter(tab => 
    tab.label.toLowerCase().includes(searchTerm.toLowerCase())
  )

  return (
    <div className="min-h-screen text-white">
      <main className="flex-1 px-6 py-8">
        <div className="max-w-7xl mx-auto space-y-6">
          <div className="mb-8">
            <UserCard user={userData} mode="page" />
          </div>

          {myMod && displayTabs.length > 3 && (
            <div className="relative mb-4">
              <input
                type="text"
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                placeholder="Search tabs..."
                className="w-full px-4 py-3 pl-12 bg-gradient-to-r from-black/60 to-purple-950/20 backdrop-blur-sm border-2 border-purple-500/40 rounded-xl text-white placeholder-white/40 focus:outline-none focus:border-purple-400 focus:ring-2 focus:ring-purple-500/30 transition-all font-mono shadow-lg"
                style={{
                  boxShadow: '0 0 20px rgba(168, 85, 247, 0.2)'
                }}
              />
              <MagnifyingGlassIcon className="w-5 h-5 text-purple-400 absolute left-4 top-1/2 -translate-y-1/2" />
            </div>
          )}

          <div className="flex flex-wrap gap-3 mb-6">
            {filteredTabs.map((tab) => (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`px-6 py-3 rounded-xl font-black text-base uppercase transition-all duration-300 backdrop-blur-sm ${
                  activeTab === tab.id
                    ? 'scale-[1.05] shadow-2xl border-2'
                    : 'bg-black/40 text-white/60 border-2 border-white/20 hover:border-white/40 hover:text-white/90 hover:scale-[1.02] hover:shadow-lg'
                }`}
                style={
                  activeTab === tab.id
                    ? {
                        background: `linear-gradient(135deg, ${tab.color}20 0%, ${tab.color}40 100%)`,
                        borderColor: tab.color,
                        color: tab.color,
                        boxShadow: `0 0 30px ${tab.color}40, 0 0 60px ${tab.color}20`
                      }
                    : undefined
                }
              >
                {tab.label}
              </button>
            ))}
          </div>

          {filteredTabs.length === 0 && searchTerm && (
            <div className="text-center text-white/40 py-8 font-mono">
              No tabs found matching "{searchTerm}"
            </div>
          )}

          <div className="bg-gradient-to-br from-black/60 to-black/80 border-2 border-white/30 rounded-xl p-6 backdrop-blur-sm shadow-xl">
            {activeTab === 'mods' && <Mods userData={userData} />}
            {activeTab === 'portfolio' &&  user && <Portfolio />}
            {activeTab === 'transfer' &&  user && <Transfer />}
            {activeTab === 'create' &&  user && <Create />}
            {activeTab === 'admin' && user && <Admin userData={userData} />}
            {activeTab === 'contracts' &&  user && <ContractsInterface />}
            {activeTab === 'billing' &&  user && <Billing />}
            {activeTab === 'txs' &&  user && <Txs />}
          </div>
        </div>
      </main>
    </div>
  )
}
