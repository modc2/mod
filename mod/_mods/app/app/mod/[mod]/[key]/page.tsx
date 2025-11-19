'use client'

import { useEffect, useState } from 'react'
import { useParams } from 'next/navigation'
import { Loading } from '@/app/block/ui/Loading'
import { ModuleType } from '@/app/types'
import { useUserContext } from '@/app/context'
import { ModContent, ModApi, ModApp } from './tabs'
import ModCard from '@/app/mod/explore/ModCard'
import { AlertCircle } from 'lucide-react'
import { text2color } from '@/app/utils'
import UpdateMod from '@/app/user/wallet/UpdateMod'

export default function ModulePage() {
  const params = useParams()
  const { client, user } = useUserContext()
  const modName = params.mod as string
  const modKey = params.key as string



  const [mod, setMod] = useState<ModuleType | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [activeTab, setActiveTab] = useState<'content' | 'api' | 'app' | 'update'>('api')
  const [myMod, setMyMod] = useState(false)

  const moduleColor = mod ? text2color(mod.name || mod.key) : '#ffffff'

  useEffect(() => {
    const fetchMod = async () => {
      if (!modName || !modKey) return
      setLoading(true)
      setError(null)
      try {
        if (!client) {
          setError('Client not initialized')
          return
        }
        const data = await client.call('mod', { mod: modName, key: modKey, content: true, schema: true })
        if (user?.key && data.key === user.key) {
          setMyMod(true)
        } else {
          setMyMod(false)
        }
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

  const hexToRgb = (hex: string) => {
    const result = /^#?([a-f\\d]{2})([a-f\\d]{2})([a-f\\d]{2})$/i.exec(hex)
    return result ? {
      r: parseInt(result[1], 16),
      g: parseInt(result[2], 16),
      b: parseInt(result[3], 16)
    } : { r: 255, g: 255, b: 255 }
  }

  const rgb = hexToRgb(moduleColor)

  const availableTabs = myMod ? ['api', 'app', 'update'] : ['api', 'app']

  return (
    <div className="min-h-screen bg-gradient-to-br from-black via-gray-900 to-black text-white flex flex-col">
      <main className="flex-1 px-6 py-8">
        <div className="max-w-7xl mx-auto space-y-6">
          <div className="mb-8">
            <ModCard mod={mod} card_enabled={false} />
          </div>

          <div className="flex flex-wrap gap-3 mb-6">
            {(availableTabs as const).map((tab) => {
              const isActive = activeTab === tab
              return (
                <button
                  key={tab}
                  onClick={() => setActiveTab(tab as any)}
                  className={`px-6 py-3 rounded-xl font-black text-base uppercase transition-all duration-300 ${
                    isActive
                      ? 'text-white border-2 shadow-2xl scale-105'
                      : 'text-gray-400 border-2 border-gray-600/40 hover:scale-105 hover:border-gray-500/60'
                  }`}
                  style={{
                    backgroundColor: isActive ? `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.3)` : `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.1)`,
                    borderColor: isActive ? `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.8)` : undefined,
                    boxShadow: isActive ? `0 0 24px rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.5)` : undefined
                  }}
                >
                  {tab}
                </button>
              )
            })}
          </div>

          <div>
            {activeTab === 'content' && <ModContent mod={mod} />}
            {activeTab === 'api' && <ModApi mod={mod} />}
            {activeTab === 'app' && <ModApp mod={mod} moduleColor={moduleColor} />}
            {activeTab === 'update' && myMod && <UpdateMod mod={mod} />}
          </div>
        </div>
      </main>
    </div>
  )
}