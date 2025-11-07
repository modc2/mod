'use client'

import React, { useEffect, useMemo, useState } from 'react'
import { Client } from '@/app/block/client/client'
import { Loading } from '@/app/block/Loading'
import ModCard from './ModCard'
import { ModuleType } from '@/app/types'
import { Footer } from '@/app/block/footer/Footer'
import { useSearchContext } from '@/app/block/context/SearchContext'
import { useUserContext } from '@/app/block/context/UserContext'
import { ModSettings } from '@/app/block/settings/ModSettings'
import { Plus, X, RotateCcw, Sparkles, ChevronDown, ChevronUp, Zap, Coins } from 'lucide-react'

type SortKey = 'recent' | 'name' | 'author' | 'balance'

export default function Modules() {
  const { keyInstance } = useUserContext()
  const client = useMemo(() => new Client(undefined, keyInstance), [keyInstance])
  const { searchFilters } = useSearchContext()

  const [mods, setMods] = useState<ModuleType[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [sort, setSort] = useState<SortKey>('recent')
  const [columns, setColumns] = useState<number>(2)
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const [modSettings, setModSettings] = useState({ visibility: 'public', autoApprove: false, maxUsers: 100, permissions: ['read'], rateLimit: 10 })

  const searchTerm = searchFilters.searchTerm?.trim() || ''

  const sortModules = (list: ModuleType[]) => {
    switch (sort) {
      case 'name':
        return [...list].sort((a, b) => (a.name || '').localeCompare(b.name || ''))
      case 'author':
        return [...list].sort((a, b) => (a.key || '').localeCompare(b.key || ''))
      case 'balance':
        return [...list].sort((a, b) => (b.balance || 0) - (a.balance || 0))
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

  const fetchAll = async () => {
    if (!keyInstance) return
    setLoading(true)
    setError(null)
    try {
      const raw = (await client.call('mods', {})) as ModuleType[]
      const allMods = Array.isArray(raw) ? raw : []
      const filtered = filterModsBySearch(allMods, searchTerm)
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
  }, [keyInstance, searchTerm, sort])

  const gridColsClass = {
    1: 'grid-cols-1',
    2: 'grid-cols-1 md:grid-cols-2',
    3: 'grid-cols-1 md:grid-cols-2 lg:grid-cols-3',
    4: 'grid-cols-1 md:grid-cols-2 lg:grid-cols-4'
  }[columns] || 'grid-cols-1 md:grid-cols-2'

  return (
    <div className="min-h-screen bg-gradient-to-br from-black via-gray-900 to-black text-white">
      <main className="flex-1 px-6 py-8" role="main">
        <div className="mx-auto max-w-7xl mb-8">
          <div className="bg-gradient-to-r from-purple-500/10 via-pink-500/10 to-blue-500/10 border-2 border-purple-500/30 rounded-2xl shadow-2xl shadow-purple-500/20 overflow-hidden backdrop-blur-xl">
            <button
              onClick={() => setAdvancedOpen(!advancedOpen)}
              className="w-full flex items-center justify-between p-6 hover:bg-purple-500/5 transition-all group"
            >
              <div className="flex items-center gap-4">
                <div className="p-3 bg-gradient-to-br from-purple-500/30 to-pink-500/30 border-2 border-purple-400/50 rounded-xl group-hover:scale-110 group-hover:rotate-3 transition-all duration-300 shadow-lg shadow-purple-500/30">
                  <Sparkles className="text-purple-300" size={28} strokeWidth={2.5} />
                </div>
                <div className="text-left">
                  <div className="text-purple-300 font-black text-2xl uppercase tracking-wider drop-shadow-lg">SETTINGS</div>
                  <div className="text-purple-400/70 font-bold text-sm uppercase tracking-wide">Customize your vibe</div>
                </div>
              </div>
              <div className="p-3 bg-gradient-to-br from-purple-500/30 to-pink-500/30 border-2 border-purple-400/50 rounded-xl group-hover:rotate-180 transition-all duration-500 shadow-lg shadow-purple-500/30">
                {advancedOpen ? <ChevronUp className="text-purple-300" size={28} strokeWidth={2.5} /> : <ChevronDown className="text-purple-300" size={28} strokeWidth={2.5} />}
              </div>
            </button>

            {advancedOpen && (
              <div className="border-t-2 border-purple-500/30 p-6 bg-black/40 backdrop-blur-sm animate-in slide-in-from-top duration-300">
                <div className="flex flex-col md:flex-row items-start md:items-center justify-between gap-8">
                  <div className="flex-1 w-full">
                    <div className="text-purple-300 font-black text-sm uppercase tracking-wider mb-4 flex items-center gap-2">
                      <Zap className="text-purple-400" size={18} strokeWidth={2.5} />
                      SORT BY
                    </div>
                    <div className="flex flex-wrap gap-3">
                      {(['recent', 'name', 'author', 'balance'] as SortKey[]).map((s) => (
                        <button
                          key={s}
                          onClick={() => setSort(s)}
                          className={`px-6 py-3 rounded-xl font-black text-base uppercase transition-all duration-300 flex items-center gap-2 ${
                            sort === s
                              ? 'bg-gradient-to-r from-purple-500 to-pink-500 text-white border-2 border-purple-300 shadow-2xl shadow-purple-500/50 scale-110'
                              : 'bg-purple-500/20 text-purple-300 border-2 border-purple-500/40 hover:bg-purple-500/30 hover:scale-105 hover:border-purple-400/60'
                          }`}
                        >
                          {s === 'balance' && <Coins size={18} strokeWidth={2.5} />}
                          {s}
                        </button>
                      ))}
                    </div>
                  </div>

                  <div className="flex-1 w-full">
                    <div className="text-blue-300 font-black text-sm uppercase tracking-wider mb-4 flex items-center gap-2">
                      <Zap className="text-blue-400" size={18} strokeWidth={2.5} />
                      COLUMNS
                    </div>
                    <div className="flex flex-wrap gap-3">
                      {[1, 2, 3, 4].map((num) => (
                        <button
                          key={num}
                          onClick={() => setColumns(num)}
                          className={`w-14 h-14 rounded-xl font-black text-2xl transition-all duration-300 ${
                            columns === num
                              ? 'bg-gradient-to-br from-blue-500 to-cyan-500 text-white border-2 border-blue-300 shadow-2xl shadow-blue-500/50 scale-110'
                              : 'bg-blue-500/20 text-blue-300 border-2 border-blue-500/40 hover:bg-blue-500/30 hover:scale-105 hover:border-blue-400/60'
                          }`}
                        >
                          {num}
                        </button>
                      ))}
                    </div>
                  </div>

                </div>
              </div>
            )}
          </div>
        </div>

        {error && (
          <div className="mx-auto max-w-7xl mb-6">
            <div className="p-5 border-2 border-red-500/60 bg-gradient-to-r from-red-500/20 to-pink-500/20 rounded-2xl flex items-start justify-between backdrop-blur-xl shadow-2xl shadow-red-500/30 animate-in slide-in-from-top duration-300">
              <div className="flex-1">
                <div className="text-red-300 font-black mb-2 text-2xl uppercase tracking-wider drop-shadow-lg">ERROR</div>
                <div className="text-red-200/90 text-lg font-bold">{error}</div>
              </div>
              <div className="flex gap-3 ml-4">
                <button
                  onClick={fetchAll}
                  className="flex items-center gap-2 px-5 py-3 border-2 border-red-400/60 rounded-xl text-red-300 hover:bg-red-500/30 hover:scale-105 transition-all font-bold uppercase tracking-wide shadow-lg"
                >
                  <RotateCcw size={20} strokeWidth={2.5} />
                  RETRY
                </button>
                <button
                  onClick={() => setError(null)}
                  className="p-3 border-2 border-red-400/60 rounded-xl text-red-300 hover:bg-red-500/30 hover:scale-105 transition-all shadow-lg"
                >
                  <X size={20} strokeWidth={2.5} />
                </button>
              </div>
            </div>
          </div>
        )}

        {!loading && mods.length === 0 && !error && (
          <div className="mx-auto max-w-4xl text-center py-24">
            <div className="mb-8 inline-block p-8 bg-gradient-to-br from-purple-500/20 via-pink-500/20 to-blue-500/20 rounded-3xl border-2 border-purple-500/40 shadow-2xl shadow-purple-500/30 backdrop-blur-xl animate-pulse">
              <Sparkles className="w-24 h-24 text-purple-300" strokeWidth={2} />
            </div>
            <div className="text-purple-300 text-4xl mb-8 font-black uppercase tracking-wide drop-shadow-2xl">
              {searchTerm ? 'NO MODULES MATCH YOUR SEARCH' : 'NO MODULES YET'}
            </div>
            {!searchTerm && (
              <button className="inline-flex items-center gap-4 border-2 border-purple-500/50 bg-gradient-to-r from-purple-500/20 via-pink-500/20 to-blue-500/20 text-purple-300 px-10 py-5 rounded-2xl hover:bg-purple-500/30 hover:border-purple-400/70 hover:shadow-2xl hover:shadow-purple-500/40 hover:scale-105 transition-all duration-300 font-black text-2xl uppercase tracking-wide group backdrop-blur-xl">
                <Plus size={32} strokeWidth={2.5} className="group-hover:rotate-90 transition-transform duration-300" />
                CREATE FIRST MODULE
              </button>
            )}
          </div>
        )}

        {loading && (
          <div className="flex justify-center py-24">
            <Loading />
          </div>
        )}

        <div className={`mx-auto max-w-7xl grid ${gridColsClass} gap-6`}>
          {mods.map((mod) => (
            <div
              key={`${mod.name}-${mod.key}`}
              className="transform hover:scale-[1.02] transition-all duration-300 ease-out"
            >
              <ModCard mod={mod} />
            </div>
          ))}
        </div>
      </main>

      <Footer />
    </div>
  )
}