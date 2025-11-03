'use client'

import { useEffect, useState, useCallback } from 'react'
import { Client } from '@/app/block/client/client'
import { Loading } from '@/app/block/Loading'
import { ModuleType } from '@/app/types'
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
import { CopyButton } from '@/app/block/CopyButton'
import { ModuleContent } from '@/app/mod/[mod]/[key]/ModuleContent'
import ModuleSchema from '@/app/mod/[mod]/[key]/ModuleApi'
import { ModuleApp } from '@/app/mod/[mod]/[key]/ModuleApp'
import { motion, AnimatePresence } from 'framer-motion'
import { time2str, text2color, shorten } from '@/app/utils'
type TabType = 'app' | 'api' | 'content'

interface ModuleProps {
  module_name: string
  content?: boolean
  api?: boolean
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
        <div className="w-full px-4 py-3 border-b border-white/10 bg-gradient-to-r from-black via-gray-900/50 to-black md:px-6 md:py-4">
          <div className="flex flex-wrap items-center gap-2 md:gap-4">
            <span
              className="inline-flex items-center gap-2 px-3 py-2 rounded-lg text-2xl font-bold md:gap-3 md:px-5 md:py-3 md:text-4xl"
              style={{ color: moduleColor, backgroundColor: `${moduleColor}14`, border: `2px solid ${moduleColor}33` }}
            >
              <CubeIcon className="h-6 w-6 md:h-10 md:w-10" />
              <span className="truncate max-w-[150px] sm:max-w-none">{mod.name}</span>
            </span>

            {Array.isArray(mod.tags) && mod.tags.slice(0, 2).map((tag, i) => (
              <span
                key={`${tag}-${i}`}
                className="hidden sm:inline-flex items-center gap-2 px-3 py-1.5 rounded-md text-base border md:px-4 md:py-2 md:text-xl"
                style={{ color: moduleColor, backgroundColor: `${moduleColor}0f`, borderColor: `${moduleColor}33` }}
              >
                <TagIcon className="h-4 w-4 md:h-6 md:w-6" />
                {tag}
              </span>
            ))}

            <div className="flex-1 min-w-[8px]" />

            {/* Column Size Selector - hidden on mobile */}
            <div className="hidden lg:inline-flex items-center gap-2 px-3 py-2 rounded-lg border bg-black/60 md:px-4" style={{ borderColor: `${moduleColor}33` }}>
              <span className="text-sm font-semibold md:text-base" style={{ color: moduleColor }}>Width:</span>
              {([`compact`, 'normal', 'wide'] as const).map((size) => (
                <button
                  key={size}
                  onClick={() => setColumnSize(size)}
                  className={`px-3 py-1.5 rounded-md text-sm font-bold transition-all md:px-4 md:py-2 md:text-base ${
                    columnSize === size ? 'text-black' : ''
                  }`}
                  style={{
                    backgroundColor: columnSize === size ? moduleColor : 'transparent',
                    color: columnSize === size ? '#000' : `${moduleColor}99`,
                    border: `1px solid ${moduleColor}33`
                  }}
                >
                  {size.charAt(0).toUpperCase()}
                </button>
              ))}
            </div>

            {mod.key && (
              <div
                className="hidden md:inline-flex items-center gap-2 px-3 py-1.5 rounded-md text-base border bg-black/60 md:px-4 md:py-2 md:text-xl"
                style={{ borderColor: `${moduleColor}33` }}
              >
                <KeyIcon className="h-4 w-4 md:h-6 md:w-6" style={{ color: moduleColor }} />
                <span className="font-mono text-sm md:text-lg">{shorten(mod.key)}</span>
                <CopyButton size="sm" content={mod.key} />
              </div>
            )}

            <div
              className="hidden sm:inline-flex items-center gap-2 px-3 py-1.5 rounded-md text-base border bg-black/60 md:px-4 md:py-2 md:text-xl"
              style={{ borderColor: `${moduleColor}33` }}
            >
              <ClockIcon className="h-4 w-4 md:h-6 md:w-6" style={{ color: moduleColor }} />
              <span className="font-medium text-sm md:text-lg">{time2str(mod.created)}</span>
            </div>

            <button
              onClick={handleSync}
              disabled={syncing}
              className="inline-flex items-center justify-center h-10 w-10 rounded-lg border-2 transition font-bold text-lg md:h-12 md:w-12 md:text-xl"
              style={{ borderColor: `${moduleColor}4D`, color: moduleColor, backgroundColor: syncing ? `${moduleColor}10` : 'transparent' }}
              title="Sync"
            >
              <ArrowPathIcon className={`h-5 w-5 md:h-7 md:w-7 ${syncing ? 'animate-spin' : ''}`} />
            </button>
          </div>
        </div>

        {/* Professional Tabs */}
        <div className="flex border-b border-white/10 bg-black/90 overflow-x-auto">
          {tabs.map(({ id, icon: Icon }) => {
            const active = activeTab === id
            return (
              <button
                key={id}
                onClick={() => setActiveTab(id)}
                className="group relative px-4 py-3 text-base font-bold flex items-center gap-2 transition-all whitespace-nowrap md:px-8 md:py-4 md:text-xl md:gap-3"
              >
                {active && (
                  <motion.div layoutId="activeTab" className="absolute inset-0" style={{ backgroundColor: `${moduleColor}10` }} />
                )}
                <Icon className="h-5 w-5 relative z-10 md:h-7 md:w-7" style={{ color: active ? moduleColor : `${moduleColor}80` }} />
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
            className={`mx-auto p-4 md:p-6 ${columnWidths[columnSize]}`}
          >
            {activeTab === 'app' && (
              mod.url_app ? (
                <div className="rounded-lg border border-white/10 overflow-hidden">
                  <ModuleApp mod={mod} moduleColor={moduleColor} />
                </div>
              ) : (
                <div className="h-[400px] flex items-center justify-center text-xl md:text-2xl text-white/70">
                  <div className="text-center">
                    <ComputerDesktopIcon className="h-16 w-16 mx-auto mb-4 opacity-70 md:h-20 md:w-20" />
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
