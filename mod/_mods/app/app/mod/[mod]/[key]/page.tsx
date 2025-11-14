'use client'

import { useEffect, useState } from 'react'
import { useParams } from 'next/navigation'
import { Loading } from '@/app/block/Loading'
import { ModuleType } from '@/app/types'
import { useUserContext } from '@/app/block/context/UserContext'
import { ModContent, ModApi, ModApp } from './tabs'
import ModCard from '@/app/mod/explore/ModCard'
import { AlertCircle } from 'lucide-react'
import { text2color } from '@/app/utils'

export default function ModulePage() {
  const params = useParams()
  const { client } = useUserContext()
  const modName = params.mod as string
  const modKey = params.key as string

  const [mod, setMod] = useState<ModuleType | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [activeTab, setActiveTab] = useState<'content' | 'api' | 'app'>('content')

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
  const borderColor = `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.4)`
  const glowColor = `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.2)`

  return (
    <div className="min-h-screen bg-gradient-to-br from-black via-gray-900 to-black text-white flex flex-col">
      <main className="flex-1 px-6 py-8">
        <div className="max-w-7xl mx-auto space-y-6">
          <div className="mb-8">
            <ModCard mod={mod} card_enabled={false} />
          </div>

          <div className="rounded-2xl overflow-hidden backdrop-blur-xl shadow-2xl border-2" style={{ backgroundColor: '#0f0f0f', borderColor: borderColor, boxShadow: `0 0 24px ${glowColor}` }}>
            <div className="flex border-b-2" style={{ borderColor: borderColor }}>
              {(['content', 'api', 'app'] as const).map((tab) => {
                const isActive = activeTab === tab
                return (
                  <button
                    key={tab}
                    onClick={() => setActiveTab(tab)}
                    className="flex-1 px-6 py-4 font-black text-lg uppercase tracking-wider transition-all duration-300 relative overflow-hidden group"
                    style={{
                      backgroundColor: isActive ? `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.15)` : 'transparent',
                      color: isActive ? moduleColor : '#6b7280',
                      borderBottom: isActive ? `4px solid ${moduleColor}` : 'none'
                    }}
                  >
                    {isActive && (
                      <div 
                        className="absolute inset-0 opacity-10" 
                        style={{ 
                          background: `linear-gradient(135deg, ${moduleColor}, transparent)` 
                        }}
                      />
                    )}
                    <span className="relative z-10">{tab}</span>
                  </button>
                )
              })}
            </div>

            <div className="p-6" style={{ backgroundColor: '#0a0a0a' }}>
              {activeTab === 'content' && <ModContent mod={mod} />}
              {activeTab === 'api' && <ModApi mod={mod} />}
              {activeTab === 'app' && <ModApp mod={mod} moduleColor={moduleColor} />}
            </div>
          </div>
        </div>
      </main>

    </div>
  )
}