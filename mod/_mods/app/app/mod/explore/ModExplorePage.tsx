'use client'

import React, { useEffect, useMemo, useState } from 'react'
import { Client } from '@/app/block/client/client'
import { Loading } from '@/app/block/Loading'
import ModCard from './ModCard'
import { ModCardSettings } from './ModCardSettings'
import { ModuleType } from '@/app/types'
import { Footer } from '@/app/block/footer/Footer'
import { useSearchContext } from '@/app/block/context/SearchContext'
import { useUserContext } from '@/app/block/context/UserContext'
import { X, RotateCcw, Sparkles } from 'lucide-react'

type SortKey = 'recent' | 'name' | 'author' | 'balance' | 'updated' | 'created'

export default function Modules() {
  const { client } = useUserContext()
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
  }, [client, searchTerm, sort, userFilter])

  const gridColsClass = {
    1: 'grid-cols-1',
    2: 'grid-cols-1 md:grid-cols-2',
    3: 'grid-cols-1 md:grid-cols-2 lg:grid-cols-3',
    4: 'grid-cols-1 md:grid-cols-2 lg:grid-cols-4'
  }[columns] || 'grid-cols-1 md:grid-cols-2'

  return (
    <div className="min-h-screen bg-gradient-to-br from-black via-gray-900 to-black text-white">
      <main className="flex-1 px-2 py-0" role="main">
        <div className="mx-auto max-w-7xl mb-4">
          <ModCardSettings
            sort={sort}
            onSortChange={setSort}
            columns={columns}
            onColumnsChange={setColumns}
            userFilter={userFilter}
            onUserFilterChange={setUserFilter}
          />
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
            <div className="mb-6 inline-block p-6 bg-gradient-to-br from-purple-500/20 via-pink-500/20 to-blue-500/20 rounded-2xl border-2 border-purple-500/40 shadow-xl backdrop-blur-xl">
              <Sparkles className="w-16 h-16 text-purple-300" strokeWidth={2} />
            </div>
            <div className="text-purple-300 text-3xl mb-6 font-black uppercase tracking-wide">
              {searchTerm || userFilter ? 'NO MODULES MATCH YOUR FILTERS' : 'NO MODULES YET'}
            </div>
          </div>
        )}

        {loading && (
          <div className="flex justify-center py-12">
            <Loading />
          </div>
        )}

        <div className={`mx-auto max-w-7xl grid ${gridColsClass} gap-6`}>
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

      <Footer />
    </div>
  )
}
