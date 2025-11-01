'use client'

import React, { useEffect, useMemo, useState } from 'react'
import { Client } from '@/app/block/client/client'
import { Loading } from '@/app/block/Loading'
import ModuleCard from '@/app/mods/ModuleCard'
import { ModuleType } from '@/app/types'
import { Footer } from '@/app/block/Footer'
import { useSearchContext } from '@/app/block/context/SearchContext'
import { useUserContext } from '@/app/block/context/UserContext'
import { Plus, X, RotateCcw, Sparkles } from 'lucide-react'

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
        return [...list].sort((a, b) => (b.time || 0) - (a.time || 0))
    }
  }

  const fetchAll = async () => {
    if (!keyInstance) return
    setLoading(true)
    setError(null)
    try {
      const params = searchTerm ? { search: searchTerm } : {}
      const raw = (await client.call('mods', params)) as ModuleType[]
      const sorted = Array.isArray(raw) ? sortModules(raw) : []
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
      <header className="sticky top-0 z-50 border-b border-white/10 bg-black/95 backdrop-blur-xl">
        <div className="mx-auto max-w-4xl px-6 py-6">
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-3">
              <div className="p-2 bg-gradient-to-br from-green-500/20 to-blue-500/20 rounded-xl border border-white/10">
                <Sparkles className="w-6 h-6 text-green-400" />
              </div>
              <h1 className="text-3xl font-bold bg-gradient-to-r from-white to-white/60 bg-clip-text text-transparent">Modules</h1>
            </div>
            <div className="flex items-center gap-3">
              <select
                value={sort}
                onChange={(e) => setSort(e.target.value as SortKey)}
                className="bg-white/5 border border-white/10 text-white/90 px-4 py-2 rounded-xl text-sm hover:bg-white/10 hover:border-white/20 focus:outline-none focus:ring-2 focus:ring-white/20 transition-all cursor-pointer font-medium"
              >
                <option value="recent">Recent</option>
                <option value="name">Name</option>
                <option value="author">Author</option>
              </select>
            </div>
          </div>

          {searchTerm && (
            <div className="mt-3 px-4 py-2 bg-white/5 border border-white/10 rounded-xl inline-block">
              <span className="text-sm text-white/50">Searching for </span>
              <span className="text-sm text-white font-semibold">"{searchTerm}"</span>
            </div>
          )}
        </div>
      </header>

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