'use client'

import React, { useEffect, useMemo, useState } from 'react'
import { Client } from '@/app/block/client/client'
import { Loading } from '@/app/block/Loading'
import ModuleCard from '@/app/mods/ModuleCard'
import { ModuleType } from '@/app/types'
import { Footer } from '@/app/block/Footer'
import { useSearchContext } from '@/app/block/context/SearchContext'
import { useUserContext } from '@/app/block/context/UserContext'
import { Plus, X, RotateCcw, Sparkles, Globe } from 'lucide-react'

type SortKey = 'recent' | 'name' | 'author'

export default function Modules() {
  const { keyInstance } = useUserContext()
  const client = useMemo(() => new Client(undefined, keyInstance), [keyInstance])
  const { searchFilters } = useSearchContext()

  const [mods, setMods] = useState<ModuleType[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [sort, setSort] = useState<SortKey>('recent')

  const searchTerm = searchFilters.searchTerm?.trim() || ''

  const sortModules = (list: ModuleType[]) => {
    switch (sort) {
      case 'name':
        return [...list].sort((a, b) => (a.name || '').localeCompare(b.name || ''))
      case 'author':
        return [...list].sort((a, b) => (a.author || '').localeCompare(b.author || ''))
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
      (mod.key?.toLowerCase().includes(lowerTerm))
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
      setError(err?.message || 'Failed to load modules')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchAll()
  }, [keyInstance, searchTerm, sort])

  return (
    <div className="min-h-screen bg-black text-white">

      {error && (
        <div className="mx-auto max-w-4xl px-6 mt-6">
          <div className="p-5 border border-rose-500/30 bg-gradient-to-br from-rose-500/10 to-rose-600/5 rounded-2xl flex items-start justify-between backdrop-blur-sm">
            <div className="flex-1">
              <div className="text-rose-400 font-semibold mb-2 text-lg">Error</div>
              <div className="text-rose-300/80 text-sm">{error}</div>
            </div>
            <div className="flex gap-2 ml-4">
              <button
                onClick={fetchAll}
                className="flex items-center gap-2 px-4 py-2 border border-rose-400/40 rounded-xl text-rose-300 hover:bg-rose-500/20 hover:border-rose-400/60 transition-all text-sm font-medium"
              >
                <RotateCcw size={16} />
                Retry
              </button>
              <button
                onClick={() => setError(null)}
                className="p-2 border border-rose-400/40 rounded-xl text-rose-300 hover:bg-rose-500/20 hover:border-rose-400/60 transition-all"
              >
                <X size={16} />
              </button>
            </div>
          </div>
        </div>
      )}

      <main className="flex-1 px-6 py-10" role="main">
        {!loading && mods.length === 0 && !error && (
          <div className="mx-auto max-w-4xl text-center py-24">
            <div className="mb-6 inline-block p-4 bg-white/5 rounded-2xl border border-white/10">
              <Sparkles className="w-12 h-12 text-white/30" />
            </div>
            <div className="text-white/50 text-xl mb-8 font-light">
              {searchTerm ? 'No modules match your search.' : 'No modules yet.'}
            </div>
            {!searchTerm && (
              <button className="inline-flex items-center gap-3 border border-white/20 bg-gradient-to-r from-white/5 to-white/10 text-white px-8 py-4 rounded-xl hover:bg-white/10 hover:border-white/40 hover:shadow-xl hover:shadow-white/5 transition-all font-medium text-lg group">
                <Plus size={20} className="group-hover:rotate-90 transition-transform duration-300" />
                Create Your First Module
              </button>
            )}
          </div>
        )}

        {loading && (
          <div className="flex justify-center py-24">
            <Loading />
          </div>
        )}

        <div className="mx-auto max-w-4xl space-y-4">
          {mods.map((mod) => (
            <div
              key={mod.key}
              className="transform hover:scale-[1.02] transition-all duration-300 ease-out"
            >
              <ModuleCard mod={mod} />
            </div>
          ))}
        </div>
      </main>

      <Footer />
    </div>
  )
}