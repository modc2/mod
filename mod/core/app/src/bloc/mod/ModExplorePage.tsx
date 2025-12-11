'use client'

import React, { useEffect, useMemo, useState } from 'react'
import { Loading } from '@/bloc/ui/Loading'
import ModCard from './ModCard'
import { ModCardSettings } from './ModCardSettings'
import { ModuleType } from '@/bloc/types'
import { useSearchContext } from '@/bloc/context/SearchContext'
import { useUserContext } from '@/bloc/context'
import { X, RotateCcw, Sparkles, Maximize2, Minimize2 } from 'lucide-react'

type SortKey = 'recent' | 'name' | 'author' | 'balance' | 'updated' | 'created'

export default function Modules() {
  const { client, user } = useUserContext()
  const { searchFilters } = useSearchContext()

  const [mods, setMods] = useState<ModuleType[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [sort, setSort] = useState<SortKey>(() => {
    if (typeof window !== 'undefined') {
      return (localStorage.getItem('mod_explorer_sort') as SortKey) || 'recent'
    }
    return 'recent'
  })
  const [columns, setColumns] = useState<number>(() => {
    if (typeof window !== 'undefined') {
      return parseInt(localStorage.getItem('mod_explorer_columns') || '2')
    }
    return 2
  })
  const [userFilter, setUserFilter] = useState<string>(() => {
    if (typeof window !== 'undefined') {
      return localStorage.getItem('mod_explorer_user_filter') || ''
    }
    return ''
  })
  const [showMyModsOnly, setShowMyModsOnly] = useState<boolean>(() => {
    if (typeof window !== 'undefined') {
      return localStorage.getItem('mod_explorer_my_mods_only') === 'true'
    }
    return false
  })
  const [showLocalOnly, setShowLocalOnly] = useState<boolean>(() => {
    if (typeof window !== 'undefined') {
      return localStorage.getItem('mod_explorer_local_only') === 'true'
    }
    return false
  })
  const [showOnchainOnly, setShowOnchainOnly] = useState<boolean>(() => {
    if (typeof window !== 'undefined') {
      return localStorage.getItem('mod_explorer_onchain_only') === 'true'
    }
    return false
  })
  const [isFullscreen, setIsFullscreen] = useState(false)

  const searchTerm = searchFilters.searchTerm?.trim() || ''

  useEffect(() => {
    if (typeof window !== 'undefined') {
      localStorage.setItem('mod_explorer_sort', sort)
    }
  }, [sort])

  useEffect(() => {
    if (typeof window !== 'undefined') {
      localStorage.setItem('mod_explorer_columns', columns.toString())
    }
  }, [columns])

  useEffect(() => {
    if (typeof window !== 'undefined') {
      localStorage.setItem('mod_explorer_user_filter', userFilter)
    }
  }, [userFilter])

  useEffect(() => {
    if (typeof window !== 'undefined') {
      localStorage.setItem('mod_explorer_my_mods_only', showMyModsOnly.toString())
    }
  }, [showMyModsOnly])

  useEffect(() => {
    if (typeof window !== 'undefined') {
      localStorage.setItem('mod_explorer_local_only', showLocalOnly.toString())
    }
  }, [showLocalOnly])

  useEffect(() => {
    if (typeof window !== 'undefined') {
      localStorage.setItem('mod_explorer_onchain_only', showOnchainOnly.toString())
    }
  }, [showOnchainOnly])

  const sortModules = (list: ModuleType[]) => {
    switch (sort) {
      case 'name':
        return [...list].sort((a, b) => (a.name || '').localeCompare(b.name || ''))
      case 'author':
        return [...list].sort((a, b) => (a.key || '').localeCompare(b.key || ''))
      case 'updated':
        return [...list].sort((a, b) => (b.updated || 0) - (a.updated || 0))
      case 'created':
        return [...list].sort((a, b) => (b.created || 0) - (a.created || 0))
      case 'recent':
      default:
        return [...list].sort((a, b) => (b.updated || b.created || 0) - (a.updated || a.created || 0))
    }
  }

  const filterModsBySearch = (list: ModuleType[], term: string) => {
    if (!term) return list
    const lowerTerm = term.toLowerCase()
    return list.filter(mod => 
      (mod.name?.toLowerCase().includes(lowerTerm)) ||
      (mod.key?.toLowerCase().includes(lowerTerm)) ||
      (mod.desc?.toLowerCase().includes(lowerTerm))
    )
  }

  const filterModsByUser = (list: ModuleType[], userKey: string) => {
    if (!userKey) return list
    const lowerUserKey = userKey.toLowerCase()
    return list.filter(mod => mod.key?.toLowerCase().includes(lowerUserKey))
  }

  const filterMyMods = (list: ModuleType[]) => {
    if (!showMyModsOnly || !user) return list
    return list.filter(mod => mod.key === user.key)
  }

  const filterLocalMods = (list: ModuleType[]) => {
    if (!showLocalOnly) return list
    return list.filter(mod => mod.network === 'local' || !mod.network)
  }

  const filterOnchainMods = (list: ModuleType[]) => {
    if (!showOnchainOnly) return list
    return list.filter(mod => mod.network && mod.network !== 'local')
  }

  const fetchAll = async () => {
    setLoading(true)
    setError(null)
    try {
      if (!client) {
        setError('Client not initialized')
        return
      }
      const raw = (await client.call('mods', {})) as ModuleType[]
      const allMods = Array.isArray(raw) ? raw : []
      let filtered = filterModsBySearch(allMods, searchTerm)
      filtered = filterModsByUser(filtered, userFilter)
      filtered = filterMyMods(filtered)
      filtered = filterLocalMods(filtered)
      filtered = filterOnchainMods(filtered)
      const sorted = sortModules(filtered)
      setMods(sorted)
    } catch (err: any) {
      console.error('Error fetching modules:', err)
      setError(err?.message || 'Failed to load modules')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchAll()
  }, [client, searchTerm, sort, userFilter, showMyModsOnly, showLocalOnly, showOnchainOnly])

  const gridColsClass = {
    1: 'grid-cols-1',
    2: 'grid-cols-1 md:grid-cols-2',
    3: 'grid-cols-1 md:grid-cols-2 lg:grid-cols-3',
    4: 'grid-cols-1 md:grid-cols-2 lg:grid-cols-4'
  }[columns] || 'grid-cols-1 md:grid-cols-2'

  const toggleFullscreen = () => {
    setIsFullscreen(!isFullscreen)
  }

  return (
    <div className={`${isFullscreen ? 'fixed inset-0 z-[9999]' : 'min-h-screen'} bg-gradient-to-br from-black via-gray-900 to-black text-white transition-all duration-300`}>
      <main className="flex-1 px-2 pt-0 pb-0" role="main">
        <div className="mx-auto max-w-7xl mb-4">
          <div className="flex items-center justify-between gap-4">
            <ModCardSettings
              sort={sort}
              onSortChange={setSort}
              columns={columns}
              onColumnsChange={setColumns}
              userFilter={userFilter}
              onUserFilterChange={setUserFilter}
              showMyModsOnly={showMyModsOnly}
              onShowMyModsOnlyChange={setShowMyModsOnly}
              showLocalOnly={showLocalOnly}
              onShowLocalOnlyChange={setShowLocalOnly}
              showOnchainOnly={showOnchainOnly}
              onShowOnchainOnlyChange={setShowOnchainOnly}
            />
            <button
              onClick={toggleFullscreen}
              className="flex items-center gap-2 px-4 py-2 bg-gradient-to-r from-blue-500/20 via-purple-500/20 to-pink-500/20 border border-blue-500/40 rounded-lg backdrop-blur-xl hover:from-blue-500/30 hover:via-purple-500/30 hover:to-pink-500/30 transition-all shadow-lg shadow-blue-500/20"
              title={isFullscreen ? 'Exit Fullscreen' : 'Enter Fullscreen'}
            >
              {isFullscreen ? (
                <>
                  <Minimize2 className="w-5 h-5 text-blue-300" />
                  <span className="text-sm font-bold text-blue-300 uppercase">Exit</span>
                </>
              ) : (
                <>
                  <Maximize2 className="w-5 h-5 text-blue-300" />
                  <span className="text-sm font-bold text-blue-300 uppercase">Expand</span>
                </>
              )}
            </button>
          </div>
        </div>

        {error && (
          <div className="mx-auto max-w-7xl mb-4">
            <div className="p-4 border-2 border-red-500/60 bg-gradient-to-r from-red-500/20 to-pink-500/20 rounded-xl flex items-start justify-between backdrop-blur-xl shadow-lg">
              <div className="flex-1">
                <div className="text-red-300 font-bold mb-1 text-lg uppercase tracking-wide">ERROR</div>
                <div className="text-red-200/90 text-sm font-medium">{error}</div>
              </div>
              <div className="flex gap-2 ml-4">
                <button
                  onClick={fetchAll}
                  className="flex items-center gap-2 px-4 py-2 border border-red-400/60 rounded-lg text-red-300 hover:bg-red-500/30 transition-all font-bold text-sm uppercase"
                >
                  <RotateCcw size={16} strokeWidth={2.5} />
                  RETRY
                </button>
                <button
                  onClick={() => setError(null)}
                  className="p-2 border border-red-400/60 rounded-lg text-red-300 hover:bg-red-500/30 transition-all"
                >
                  <X size={16} strokeWidth={2.5} />
                </button>
              </div>
            </div>
          </div>
        )}

        {!loading && mods.length === 0 && !error && (
          <div className="mx-auto max-w-4xl text-center py-12">
            <div className="mb-6 inline-bloc p-6 bg-gradient-to-br from-purple-500/20 via-pink-500/20 to-blue-500/20 rounded-2xl border-2 border-purple-500/40 shadow-xl backdrop-blur-xl">
              <Sparkles className="w-16 h-16 text-purple-300" strokeWidth={2} />
            </div>
            <div className="text-purple-300 text-3xl mb-6 font-black uppercase tracking-wide">
              {searchTerm || userFilter || showMyModsOnly || showLocalOnly || showOnchainOnly ? 'NO MODULES MATCH YOUR FILTERS' : 'NO MODULES YET'}
            </div>
          </div>
        )}

        {loading && (
          <div className="flex justify-center py-12">
            <Loading />
          </div>
        )}

        <div className={`mx-auto max-w-7xl grid ${gridColsClass} gap-6 ${isFullscreen ? 'overflow-y-auto max-h-[calc(100vh-120px)]' : ''}`}>
          {mods.map((mod) => (
            <div
              key={`${mod.name}-${mod.key}`}
              className="transform hover:scale-[1.02] transition-all duration-300 ease-out"
            >
              <ModCard mod={mod} card_enabled={true} />
            </div>
          ))}
        </div>
      </main>

    </div>
  )
}
