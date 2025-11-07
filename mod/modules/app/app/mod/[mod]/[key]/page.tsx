'use client'

import { useEffect, useState, useCallback } from 'react'
import { Client } from '@/app/block/client/client'
import { Loading } from '@/app/block/Loading'
import { ModType } from '@/app/types'
import { useUserContext } from '@/app/block/context/UserContext'
import {
  CodeBracketIcon,
  ServerIcon,
  ArrowPathIcon,
  TagIcon,
  ClockIcon,
  KeyIcon,
  ComputerDesktopIcon,
  CubeIcon,
} from '@heroicons/react/24/outline'
import { Globe } from 'lucide-react'
import { CopyButton } from '@/app/block/CopyButton'
import { ModContent } from './tabs/ModContent'
import ModApi from './tabs/ModApi'
import { ModApp } from './tabs/ModApp'
import { motion, AnimatePresence } from 'framer-motion'
import { time2str, text2color, shorten } from '@/app/utils'
type TabType = 'app' | 'api' | 'content'


export default function Mod({ params }: { params: { mod: string, key: string } }){

  const module_name = params.mod
  const module_key = params.key

  const { keyInstance, authLoading } = useUserContext()

  const [mod, setMod] = useState<ModType | undefined>()
  const [error, setError] = useState<string>('')
  const [loading, setLoading] = useState<boolean>(true)
  const [syncing, setSyncing] = useState<boolean>(false)
  const [activeTab, setActiveTab] = useState<TabType>('api')
  const [hasFetched, setHasFetched] = useState(false)

  const fetchMod = useCallback(async (update = false) => {
    try {
      update ? setSyncing(true) : setLoading(true)
      const client = new Client(undefined, keyInstance)
      const params = { mod: module_name, content: true , schema: true, key:module_key}
      const foundMod = await client.call('mod', params)
      console.log("fetched module", foundMod)

      if (foundMod) {
        setMod(foundMod as ModType)
        setError('')
      } else {
        setError(`Mod ${module_name} not found`)
      }
    } catch (err: any) {
      setError(err?.message || 'Failed to fetch mod')
    } finally {
      setLoading(false)
      setSyncing(false)
    }
  }, [module_name, keyInstance])

  useEffect(() => {
    if ((!hasFetched && !authLoading) || mod === undefined) {
      setHasFetched(true)
      fetchMod(false)
    }
  }, [hasFetched, fetchMod, authLoading, mod])

  const handleSync = useCallback(() => {
    fetchMod(true)
  }, [fetchMod])

  if (authLoading || loading || mod === undefined) return <Loading />

  const moduleColor = text2color(mod.name)

  const tabs: Array<{ id: TabType; icon: any; label: string }> = [
    { id: 'api', icon: ServerIcon, label: 'API' },
    { id: 'app',  icon: ComputerDesktopIcon, label: 'APP' },
    { id: 'content', icon: CodeBracketIcon, label: 'CONTENT' },
  ]
  
  return (  return (
    <div className="min-h-screen bg-gradient-to-br from-black via-gray-900 to-black text-white mod-page w-full">
      <div className="w-full max-w-full">
        <div className="w-full px-4 py-4 border-b border-white/10 bg-black/95 backdrop-blur-sm">
          <div className="flex flex-wrap items-center gap-3">          <div className="flex flex-wrap items-center gap-3">
            <span
              className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-2xl font-bold"
              style={{ color: moduleColor, backgroundColor: `${moduleColor}14`, border: `2px solid ${moduleColor}33` }}
            >
              <CubeIcon className="h-7 w-7" />
              {mod.name}
            </span>

            {Array.isArray(mod.tags) && mod.tags.map((tag, i) => (
              <span
                key={`${tag}-${i}`}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-base border"
                style={{ color: moduleColor, backgroundColor: `${moduleColor}0f`, borderColor: `${moduleColor}33` }}
              >
                <TagIcon className="h-4 w-4" />
                {tag}
              </span>
            ))}

            <div className="flex-1 min-w-[8px]" />

            {mod.key && (
              <div
                className="inline-flex items-center gap-2 px-3 py-1.5 rounded-md text-base border bg-black/60"
                style={{ borderColor: `${moduleColor}33` }}
              >
                <KeyIcon className="h-4 w-4" style={{ color: moduleColor }} />
                <span className="font-mono">{shorten(mod.key)}</span>
                <CopyButton size="sm" content={mod.key} />
              </div>
            )}

            <div
              className="inline-flex items-center gap-2 px-3 py-1.5 rounded-md text-base border bg-black/60"
              style={{ borderColor: `${moduleColor}33` }}
            >
              <ClockIcon className="h-4 w-4" style={{ color: moduleColor }} />
              <span className="font-medium">{time2str(mod.created)}</span>
            </div>

            <button
              onClick={handleSync}
              disabled={syncing}
              className="inline-flex items-center justify-center h-10 w-10 rounded-lg border-2 transition font-bold text-lg"
              style={{ borderColor: `${moduleColor}4D`, color: moduleColor, backgroundColor: syncing ? `${moduleColor}10` : 'transparent' }}
              title="Sync"
            >
              <ArrowPathIcon className={`h-5 w-5 ${syncing ? 'animate-spin' : ''}`} />
            </button>
          </div>
        </div>

        <div className="flex border-b border-white/10 bg-black/90">        <div className="flex border-b border-white/10 bg-black/95 backdrop-blur-sm">
          {tabs.map(({ id, icon: Icon, label }) => {
            const active = activeTab === id
            return (
              <button
                key={id}
                onClick={() => setActiveTab(id)}
                className="group relative px-6 py-3.5 text-sm font-bold flex items-center gap-2.5 transition-all hover:bg-white/5"
              >
                {active && (
                  <motion.div layoutId="activeTab" className="absolute inset-0" style={{ backgroundColor: `${moduleColor}12` }} />
                )}
                <Icon className="h-5 w-5 relative z-10" style={{ color: active ? moduleColor : `${moduleColor}70` }} />
                {id === 'content' && <Globe className="h-5 w-5 relative z-10" style={{ color: active ? moduleColor : `${moduleColor}70` }} />}
                <span className="relative z-10 uppercase tracking-wider" style={{ color: active ? moduleColor : `${moduleColor}70` }}>
                  {id === 'content' ? 'CONTENT' : label}
                </span>
                {active && (
                  <motion.div layoutId="activeTabBorder" className="absolute bottom-0 left-0 right-0 h-0.5" style={{ backgroundColor: moduleColor }} />
                )}
              </button>
            )
          })}
        </div>        </div>

        <AnimatePresence mode="wait">        <AnimatePresence mode="wait">
          <motion.div
            key={activeTab}
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -10 }}
            transition={{ duration: 0.2 }}
            className="w-full"
          >
            {activeTab === 'app' && (
              mod.url_app ? (
                <div className="w-full h-[calc(100vh-280px)] min-h-[600px] rounded-lg border border-white/10 overflow-hidden bg-black/50">
                  <ModApp mod={mod} moduleColor={moduleColor} />
                </div>
              ) : (
                <div className="h-[400px] flex items-center justify-center text-xl text-white/70">
                  <div className="text-center">
                    <ComputerDesktopIcon className="h-16 w-16 mx-auto mb-4 opacity-70" />
                    <p className="font-bold">No Application Available</p>
                  </div>
                </div>
              )
            )}
            {activeTab === 'content' && (
              <div className="w-full">
                <ModContent
                  files={mod.content || {}}
                  showSearch={true}
                  compactMode={false}
                />
              </div>
            )}

            {activeTab === 'api' && (
              <div className="w-full h-[calc(100vh-280px)] min-h-[600px] rounded-lg border border-white/10 overflow-hidden bg-black/50">
                <ModApi mod={mod} />
              </div>
            )}
          </motion.div>
        </AnimatePresence>        </AnimatePresence>
      </div>
    </div>
  )
}