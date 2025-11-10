'use client'

import { useEffect, useState, useMemo } from 'react'
import { useParams } from 'next/navigation'
import { Client } from '@/app/block/client/client'
import { Loading } from '@/app/block/Loading'
import { ModuleType } from '@/app/types'
import { useUserContext } from '@/app/block/context/UserContext'
import { ModContent, ModApi, ModApp } from './tabs'
import { Footer } from '@/app/block/footer/Footer'
import ModCard from '@/app/mod/explore/ModCard'
import { AlertCircle } from 'lucide-react'

export default function ModulePage(  ) {
  const params = useParams()
  const { client } = useUserContext()
  const modName = params.mod as string
  const modKey = params.key as string

  const [mod, setMod] = useState<ModuleType | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [activeTab, setActiveTab] = useState<'content' | 'api' | 'app'>('content')

  useEffect(() => {
    const fetchMod = async () => {
      if (!modName || !modKey) return
      setLoading(true)
      setError(null)
      try {
        const data = await client.call('mod', { mod: modName, key: modKey , content:true, schema:true })
        setMod(data as ModuleType)
      } catch (err: any) {
        console.error('Failed to fetch mod:', err)
      } finally {
        setLoading(false)
      }
    }
    fetchMod()
  }, [modName, modKey, client])

  if (loading) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-black via-gray-900 to-black flex items-center justify-center">
        <Loading />
      </div>
    )
  }

  if (error || !mod) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-black via-gray-900 to-black flex items-center justify-center p-6">
        <div className="max-w-2xl w-full flex items-start gap-4 p-6 rounded-2xl border-2 border-rose-500/50 bg-gradient-to-br from-rose-500/20 to-rose-600/15 backdrop-blur-xl shadow-2xl shadow-rose-500/20">
          <AlertCircle className="w-10 h-10 text-rose-400 flex-shrink-0 mt-1" />
          <div className="flex-1">
            <h3 className="text-3xl font-black text-rose-300 mb-2 uppercase tracking-wide">ERROR</h3>
            <p className="text-xl text-rose-200/90 font-bold">{error || 'Module not found'}</p>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-black via-gray-900 to-black text-white flex flex-col">
      <main className="flex-1 px-6 py-8">
        <div className="max-w-7xl mx-auto space-y-6">
          <div className="mb-8">
            <ModCard mod={mod} />
          </div>

          <div className="bg-gradient-to-r from-purple-500/10 via-pink-500/10 to-blue-500/10 border-2 border-purple-500/30 rounded-2xl overflow-hidden backdrop-blur-xl shadow-2xl shadow-purple-500/20">
            <div className="flex border-b-2 border-purple-500/30">
              {(['content', 'api', 'app'] as const).map((tab) => (
                <button
                  key={tab}
                  onClick={() => setActiveTab(tab)}
                  className={`flex-1 px-6 py-4 font-black text-lg uppercase tracking-wider transition-all duration-300 ${
                    activeTab === tab
                      ? 'bg-gradient-to-r from-purple-500/30 to-pink-500/30 text-purple-200 border-b-4 border-purple-400 shadow-lg'
                      : 'text-purple-400/60 hover:bg-purple-500/10 hover:text-purple-300'
                  }`}
                >
                  {tab}
                </button>
              ))}
            </div>

            <div className="p-6 bg-black/40">
              {activeTab === 'content' && <ModContent mod={mod} />}
              {activeTab === 'api' && <ModApi mod={mod} />}
              {activeTab === 'app' && <ModApp mod={mod} />}
            </div>
          </div>
        </div>
      </main>

      <Footer />
    </div>
  )
}
