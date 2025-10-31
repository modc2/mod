'use client'

import React, { useEffect, useMemo, useState } from 'react'
import { Client } from '@/app/block/client/client'
import { Loading } from '@/app/block/Loading'
import ModuleCard from '@/app/block/mod/ModuleCard'
import { ModuleType } from '@/app/block/types/block/mod'
import { Footer } from '@/app/block/Footer'
import { useSearchContext } from '@/app/block/context/SearchContext'
import { useUserContext } from '@/app/block/context/UserContext'
import { Plus, X, RotateCcw, Filter } from 'lucide-react'


type SortKey = 'recent' | 'name' | 'author'
type FilterKey = 'all' | 'available' | 'unavailable'



export default function Modules() {
  const { keyInstance } = useUserContext()
  const client = useMemo(() => new Client(undefined, keyInstance), [keyInstance])
  const { searchFilters } = useSearchContext()

  const [mods, setMods] = useState<ModuleType[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [sort, setSort] = useState<SortKey>('recent')
  const [keyFilter, setKeyFilter] = useState<FilterKey>('all')

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

  const filterModules = (list: ModuleType[]) => {
    switch (keyFilter) {
      case 'available':
        return list.filter((m) => m.key && m.key.trim() !== '')
      case 'unavailable':
        return list.filter((m) => !m.key || m.key.trim() === '')
      case 'all':
      default:
        return list
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
      const filtered = filterModules(sorted)
      setMods(filtered)
    } catch (err: any) {
      setError(err?.message || 'Failed to load modules')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchAll()
  }, [keyInstance, searchTerm, sort, keyFilter])

  const filteredCount = useMemo(() => {
    const available = mods.filter((m) => m.key && m.key.trim() !== '').length
    return { all: mods.length, available, unavailable: mods.length - available }
  }, [mods])

  return (
    <div className="min-h-screen bg-black text-white">
      {/* Ultra-minimal header */}
      <header className="sticky top-0 z-50 border-b border-white/10 bg-black/95 backdrop-blur-xl">
        <div className="mx-auto max-w-2xl px-6 py-4">
          <div className="flex items-center justify-between mb-3">
            <h1 className="text-2xl font-light tracking-tight">Modules</h1>
            <div className="flex items-center gap-3">
              <select
                value={sort}
                onChange={(e) => setSort(e.target.value as SortKey)}
                className="bg-white/5 border border-white/10 text-white/90 px-3 py-1.5 rounded-lg text-sm hover:bg-white/10 focus:outline-none focus:ring-2 focus:ring-white/20 transition-all cursor-pointer"
              >
                <option value="recent">Recent</option>
                <option value="name">Name</option>
                <option value="author">Author</option>
              </select>
            </div>
          </div>

          {/* Filter pills */}
          <div className="flex items-center gap-2">
            <Filter size={14} className="text-white/40" />
            <button
              onClick={() => setKeyFilter('all')}
              className={`px-3 py-1 rounded-full text-xs font-medium transition-all ${
                keyFilter === 'all'
                  ? 'bg-white text-black'
                  : 'bg-white/5 text-white/60 hover:bg-white/10 hover:text-white/90'
              }`}
            >
              All <span className="text-white/50">({filteredCount.all})</span>
            </button>
            <button
              onClick={() => setKeyFilter('available')}
              className={`px-3 py-1 rounded-full text-xs font-medium transition-all ${
                keyFilter === 'available'
                  ? 'bg-emerald-500 text-white'
                  : 'bg-white/5 text-white/60 hover:bg-white/10 hover:text-white/90'
              }`}
            >
              Available <span className="text-white/50">({filteredCount.available})</span>
            </button>
            <button
              onClick={() => setKeyFilter('unavailable')}
              className={`px-3 py-1 rounded-full text-xs font-medium transition-all ${
                keyFilter === 'unavailable'
                  ? 'bg-rose-500 text-white'
                  : 'bg-white/5 text-white/60 hover:bg-white/10 hover:text-white/90'
              }`}
            >
              No Key <span className="text-white/50">({filteredCount.unavailable})</span>
            </button>
          </div>

          {searchTerm && (
            <div className="mt-3 text-sm text-white/50">
              Searching for <span className="text-white/90 font-medium">"{searchTerm}"</span>
            </div>
          )}
        </div>
      </header>

      {/* Refined error state */}
      {error && (
        <div className="mx-auto max-w-2xl px-6 mt-6">
          <div className="p-4 border border-rose-500/20 bg-rose-500/10 rounded-xl flex items-start justify-between">
            <div className="flex-1">
              <div className="text-rose-400 font-medium mb-1">Error</div>
              <div className="text-rose-300/80 text-sm">{error}</div>
            </div>
            <div className="flex gap-2 ml-4">
              <button
                onClick={fetchAll}
                className="flex items-center gap-1.5 px-3 py-1.5 border border-rose-400/30 rounded-lg text-rose-300 hover:bg-rose-500/20 transition-colors text-sm"
              >
                <RotateCcw size={14} />
                Retry
              </button>
              <button
                onClick={() => setError(null)}
                className="p-1.5 border border-rose-400/30 rounded-lg text-rose-300 hover:bg-rose-500/20 transition-colors"
              >
                <X size={14} />
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Main content */}
      <main className="flex-1 px-6 py-8" role="main">
        {!loading && mods.length === 0 && !error && (
          <div className="mx-auto max-w-2xl text-center py-20">
            <div className="text-white/40 text-lg mb-6 font-light">
              {searchTerm
                ? 'No modules match your search.'
                : keyFilter === 'available'
                ? 'No modules with keys available.'
                : keyFilter === 'unavailable'
                ? 'All modules have keys.'
                : 'No modules yet.'}
            </div>
            {keyFilter === 'all' && !searchTerm && (
              <button className="inline-flex items-center gap-2 border border-white/20 text-white/90 px-6 py-3 rounded-xl hover:bg-white/5 hover:border-white/40 transition-all font-light">
                <Plus size={18} />
                Create Your First Module
              </button>
            )}
          </div>
        )}

        {loading && (
          <div className="flex justify-center py-20">
            <Loading />
          </div>
        )}

        {/* Single-column, ultra-clean list */}
        <div className="mx-auto max-w-2xl space-y-3">
          {mods.map((mod) => (
            <div
              key={mod.key}
              className="transform hover:scale-[1.01] transition-all duration-200 ease-out"
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