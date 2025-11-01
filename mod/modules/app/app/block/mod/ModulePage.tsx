'use client'

import { useEffect, useState, useCallback } from 'react'
import { Client } from '@/app/blocks/client/client'
import { Loading } from '@/app/blocks/Loading'
import { ModuleType } from '@/apptypes'
import { useUserContext } from '@/app/blocks/context/UserContext'
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
import { CopyButton } from '@/app/blocks/CopyButton'
import { ModuleContent } from '@/app/blocks/mod/ModuleContent'
import ModuleSchema from '@/app/blocks/mod/ModuleApi'
import { ModuleApp } from '@/app/blocks/mod/ModuleApp'
import { motion, AnimatePresence } from 'framer-motion'

type TabType = 'app' | 'api' | 'content'

interface ModuleProps {
  module_name: string
  content?: boolean
  api?: boolean
}

const shorten = (str: string): string => {
  if (!str || str.length <= 12) return str
  return `${str.slice(0, 8)}...${str.slice(-4)}`
}

const time2str = (time: number): string => {
  const d = new Date(time * 1000)
  const now = new Date()
  const diff = now.getTime() - d.getTime()
  if (diff < 60_000) return 'now'
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`
  if (diff < 604_800_000) return `${Math.floor(diff / 86_400_000)}d ago`
  return d.toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: d.getFullYear() !== now.getFullYear() ? 'numeric' : undefined
  })
}

const text2color = (text: string): string => {
  if (!text) return '#00ff00'
  let hash = 0
  for (let i = 0; i < text.length; i++) hash = text.charCodeAt(i) + ((hash << 5) - hash)
  const golden_ratio = 0.618033988749895
  const hue = (hash * golden_ratio * 360) % 360
  const saturation = 65 + (Math.abs(hash >> 8) % 35)
  const lightness = 50 + (Math.abs(hash >> 16) % 20)
  return `hsl(${hue}, ${saturation}%, ${lightness}%)`
}

export default function Module({ module_name }: ModuleProps) {
  const { keyInstance, authLoading } = useUserContext()

  const [mod, setModule] = useState<ModuleType | undefined>()
  const [error, setError] = useState<string>('')
  const [loading, setLoading] = useState<boolean>(true)
  const [syncing, setSyncing] = useState<boolean>(false)
  const [activeTab, setActiveTab] = useState<TabType>('api')
  const [hasFetched, setHasFetched] = useState(false)
  const [columnSize, setColumnSize] = useState<'compact' | 'normal' | 'wide'>('normal')

  const fetchModule = useCallback(async (update = false) => {
    try {
      update ? setSyncing(true) : setLoading(true)
      const client = new Client(undefined, keyInstance)
      const params = { mod: module_name, content: true , schema: true}
      const foundModule = await client.call('mod', params)
      if (foundModule) {
        setModule(foundModule as ModuleType)
        setError('')
      } else {
        setError(`Module ${module_name} not found`)
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
      fetchModule(false)
    }
  }, [hasFetched, fetchModule, authLoading, mod])

  const handleSync = useCallback(() => {
    fetchModule(true)
  }, [fetchModule])

  if (authLoading || loading || mod === undefined) return <Loading />

  const moduleColor = text2color(mod.name)

  const tabs: Array<{ id: TabType; icon: any }> = [
    { id: 'api', icon: ServerIcon },
    { id: 'app',  icon: ComputerDesktopIcon },
    { id: 'content', icon: CodeBracketIcon },
  ]

  const columnWidths = {
    compact: 'max-w-4xl',
    normal: 'max-w-6xl',
    wide: 'max-w-7xl'
  }
  
  return (
    <div className="min-h-screen bg-black text-white mod-page">
      <div className="w-full">
        {/* Enhanced Header with Column Size Control */}
        <div className="w-full px-6 py-4 border-b border-white/10 bg-gradient-to-r from-black via-gray-900/50 to-black">
          <div className="flex flex-wrap items-center gap-4">
            <span
              className="inline-flex items-center gap-3 px-5 py-3 rounded-lg text-4xl font-bold"
              style={{ color: moduleColor, backgroundColor: `${moduleColor}14`, border: `2px solid ${moduleColor}33` }}
            >
              <CubeIcon className="h-10 w-10" />
              {mod.name}
            </span>

            {Array.isArray(mod.tags) && mod.tags.map((tag, i) => (
              <span
                key={`${tag}-${i}`}
                className="inline-flex items-center gap-2 px-4 py-2 rounded-md text-xl border"
                style={{ color: moduleColor, backgroundColor: `${moduleColor}0f`, borderColor: `${moduleColor}33` }}
              >
                <TagIcon className="h-6 w-6" />
                {tag}
              </span>
            ))}

            <div className="flex-1 min-w-[8px]" />

            {/* Column Size Selector */}
            <div className="inline-flex items-center gap-2 px-4 py-2 rounded-lg border bg-black/60" style={{ borderColor: `${moduleColor}33` }}>
              <span className="text-base font-semibold" style={{ color: moduleColor }}>Width:</span>
              {(['compact', 'normal', 'wide'] as const).map((size) => (
                <button
                  key={size}
                  onClick={() => setColumnSize(size)}
                  className={`px-4 py-2 rounded-md text-base font-bold transition-all ${
                    columnSize === size ? 'text-black' : ''
                  }`}
                  style={{
                    backgroundColor: columnSize === size ? moduleColor : 'transparent',
                    color: columnSize === size ? '#000' : `${moduleColor}99`,
                    border: `1px solid ${moduleColor}33`
                  }}
                >
                  {size.charAt(0).toUpperCase() + size.slice(1)}
                </button>
              ))}
            </div>

            {mod.key && (
              <div
                className="inline-flex items-center gap-2 px-4 py-2 rounded-md text-xl border bg-black/60"
                style={{ borderColor: `${moduleColor}33` }}
              >
                <KeyIcon className="h-6 w-6" style={{ color: moduleColor }} />
                <span className="font-mono text-lg">{shorten(mod.key)}</span>
                <CopyButton size="sm" content={mod.key} />
              </div>
            )}

            <div
              className="inline-flex items-center gap-2 px-4 py-2 rounded-md text-xl border bg-black/60"
              style={{ borderColor: `${moduleColor}33` }}
            >
              <ClockIcon className="h-6 w-6" style={{ color: moduleColor }} />
              <span className="font-medium text-lg">{time2str(mod.created)}</span>
            </div>

            <button
              onClick={handleSync}
              disabled={syncing}
              className="inline-flex items-center justify-center h-12 w-12 rounded-lg border-2 transition font-bold text-xl"
              style={{ borderColor: `${moduleColor}4D`, color: moduleColor, backgroundColor: syncing ? `${moduleColor}10` : 'transparent' }}
              title="Sync"
            >
              <ArrowPathIcon className={`h-7 w-7 ${syncing ? 'animate-spin' : ''}`} />
            </button>
          </div>
        </div>

        {/* Professional Tabs */}
        <div className="flex border-b border-white/10 bg-black/90">
          {tabs.map(({ id, icon: Icon }) => {
            const active = activeTab === id
            return (
              <button
                key={id}
                onClick={() => setActiveTab(id)}
                className="group relative px-8 py-4 text-xl font-bold flex items-center gap-3 transition-all"
              >
                {active && (
                  <motion.div layoutId="activeTab" className="absolute inset-0" style={{ backgroundColor: `${moduleColor}10` }} />
                )}
                <Icon className="h-7 w-7 relative z-10" style={{ color: active ? moduleColor : `${moduleColor}80` }} />
                <span className="relative z-10 uppercase tracking-wide" style={{ color: active ? moduleColor : `${moduleColor}80` }}>
                  {id}
                </span>
                {active && (
                  <motion.div layoutId="activeTabBorder" className="absolute bottom-0 left-0 right-0 h-1" style={{ backgroundColor: moduleColor }} />
                )}
              </button>
            )
          })}
        </div>

        {/* Content Area with Dynamic Width */}
        <AnimatePresence mode="wait">
          <motion.div
            key={activeTab}
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -10 }}
            transition={{ duration: 0.2 }}
            className={`mx-auto p-6 ${columnWidths[columnSize]}`}
          >
            {activeTab === 'app' && (
              mod.url_app ? (
                <div className="rounded-lg border border-white/10 overflow-hidden">
                  <ModuleApp mod={mod} moduleColor={moduleColor} />
                </div>
              ) : (
                <div className="h-[400px] flex items-center justify-center text-2xl text-white/70">
                  <div className="text-center">
                    <ComputerDesktopIcon className="h-20 w-20 mx-auto mb-4 opacity-70" />
                    <p className="font-bold">No Application Available</p>
                  </div>
                </div>
              )
            )}
            {activeTab === 'content' && (
              <ModuleContent
                files={mod.content || {}}
                showSearch={true}
                compactMode={false}
              />
            )}

            {activeTab === 'api' && (
              <div className="rounded-lg border border-white/10">
                <ModuleSchema mod={mod} />
              </div>
            )}
          </motion.div>
        </AnimatePresence>
      </div>
    </div>
  )
}