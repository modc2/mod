"use client";

import React, { useEffect, useState, useCallback, useRef } from 'react'
import ModCard from '../ModCard'
import { ModCardSettings } from '../ModCardSettings'
import { ModuleType } from '@/types'
import { useSearchContext } from '@/context/SearchContext'
import { userContext } from '@/context'
import { RotateCcw, X } from 'lucide-react'
import { CubeIcon, ArrowPathIcon } from '@heroicons/react/24/outline'
import Link from 'next/link'

type SortKey = 'recent' | 'name' | 'author' | 'balance' | 'updated' | 'created'

/** Sync mods from backend into config/mods.json via the API route */
export async function syncModsConfig(token?: string) {
  try {
    await fetch('/api/mods', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token: token || '' }),
    })
  } catch {}
}

export function clearModsCache() {
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new Event('mods-cache-cleared'))
  }
}

export default function ModExplorePage() {
  const { client, user } = userContext()
  const { searchFilters, handleSearch } = useSearchContext()

  const [mods, setMods] = useState<ModuleType[]>([])
  const [allMods, setAllMods] = useState<ModuleType[]>([])
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

  const [selectedOwners, setSelectedOwners] = useState<string[]>([])
  const [ownerInitialized, setOwnerInitialized] = useState(false)

  // Default to filtering by the logged-in user's key
  useEffect(() => {
    if (!ownerInitialized && user?.key) {
      setSelectedOwners([user.key])
      setOwnerInitialized(true)
    }
  }, [user?.key, ownerInitialized])
  const [visibleCount, setVisibleCount] = useState(20)
  const [totalMods, setTotalMods] = useState(0)
  const [refreshKey, setRefreshKey] = useState(0)
  const scrollContainerRef = useRef<HTMLDivElement>(null)

  const searchTermToUse = searchFilters.searchTerm?.trim() || ''

  // Listen for cache-clear events (fired after create/fork/build) and tx events
  useEffect(() => {
    const handleCacheClear = () => setRefreshKey(k => k + 1)
    const handleTx = () => {
      // Sync from backend on transaction events
      if (client?.token) {
        syncModsConfig(client.token).then(() => setRefreshKey(k => k + 1))
      } else {
        setRefreshKey(k => k + 1)
      }
    }
    window.addEventListener('mods-cache-cleared', handleCacheClear)
    window.addEventListener('mod:tx', handleTx)
    return () => {
      window.removeEventListener('mods-cache-cleared', handleCacheClear)
      window.removeEventListener('mod:tx', handleTx)
    }
  }, [client])

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

  const sortModules = useCallback((list: ModuleType[]) => {
    const sortOrder = typeof window !== 'undefined'
      ? (localStorage.getItem('mod_explorer_sort_order') || 'desc')
      : 'desc'

    const sorted = [...list]

    switch (sort) {
      case 'name':
        sorted.sort((a, b) => (a.name || '').localeCompare(b.name || ''))
        break
      case 'author':
        sorted.sort((a, b) => (a.key || '').localeCompare(b.key || ''))
        break
      case 'updated':
        sorted.sort((a, b) => (b.updated || 0) - (a.updated || 0))
        break
      case 'created':
        sorted.sort((a, b) => (b.created || 0) - (a.created || 0))
        break
      case 'recent':
      default:
        sorted.sort((a, b) => (b.updated || b.created || 0) - (a.updated || a.created || 0))
    }

    return sortOrder === 'asc' ? sorted.reverse() : sorted
  }, [sort])

  const fetchAll = useCallback(async () => {
    setError(null)
    setLoading(true)
    try {
      // Read from cached config/mods.json
      let res = await fetch('/api/mods')
      let data = (await res.json()) as ModuleType[]

      // If cache is empty, sync from backend and save to config
      if (data.length === 0) {
        res = await fetch('/api/mods', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ token: client?.token || '' }),
        })
        data = (await res.json()) as ModuleType[]
      }

      // Deduplicate by name+key
      const seen = new Set<string>()
      data = data.filter(mod => {
        const k = `${(mod.name || '').toLowerCase()}:${(mod.key || '').toLowerCase()}`
        if (seen.has(k)) return false
        seen.add(k)
        return true
      })

      setAllMods(data)
    } catch (err: any) {
      console.error('Error fetching modules:', err)
      setError(err?.message || 'Failed to load modules')
    } finally {
      setLoading(false)
    }
  }, [client, refreshKey])

  useEffect(() => {
    fetchAll()
  }, [fetchAll])

  // Derive displayed mods from allMods based on filters, sort, pagination
  useEffect(() => {
    let filtered = [...allMods]

    if (searchTermToUse) {
      const term = searchTermToUse.toLowerCase()
      filtered = filtered.filter(m =>
        m.name?.toLowerCase().includes(term) ||
        m.key?.toLowerCase().includes(term)
      )
    }

    if (selectedOwners.length === 1) {
      filtered = filtered.filter(m =>
        m.key?.toLowerCase() === selectedOwners[0].toLowerCase()
      )
    }

    const sorted = sortModules(filtered)
    setTotalMods(sorted.length)

    setMods(sorted.slice(0, visibleCount))
  }, [allMods, searchTermToUse, selectedOwners, sortModules, visibleCount])

  const uniqueOwners = Array.from(new Set([
    ...(user?.key ? [user.key] : []),
    ...allMods.map(m => m.key).filter(Boolean) as string[],
    ...selectedOwners,
  ])).sort()

  useEffect(() => {
    setVisibleCount(20)
  }, [searchTermToUse, selectedOwners, sort])

  // Infinite scroll: load more when user scrolls near the bottom
  useEffect(() => {
    const container = scrollContainerRef.current
    if (!container) return

    const handleScroll = () => {
      const { scrollTop, scrollHeight, clientHeight } = container
      if (scrollHeight - scrollTop - clientHeight < 300) {
        setVisibleCount(prev => {
          if (prev >= totalMods) return prev
          return prev + 20
        })
      }
    }

    container.addEventListener('scroll', handleScroll, { passive: true })
    return () => container.removeEventListener('scroll', handleScroll)
  }, [totalMods])


  const toggleOwner = (owner: string) => {
    setSelectedOwners(prev =>
      prev.includes(owner) ? prev.filter(o => o !== owner) : [...prev, owner]
    )
  }

  const clearOwnerFilters = () => {
    setSelectedOwners([])
  }

  const gridColsClass = {
    1: 'grid-cols-1',
    2: 'grid-cols-1 md:grid-cols-2',
    3: 'grid-cols-1 md:grid-cols-2 lg:grid-cols-3',
    4: 'grid-cols-1 md:grid-cols-2 lg:grid-cols-4'
  }[columns] || 'grid-cols-1 md:grid-cols-2'

  return (
    <div
      ref={scrollContainerRef}
      className="h-screen overflow-y-auto"
      style={{
        fontFamily: 'var(--font-digital), monospace',
        backgroundColor: 'var(--bg-primary)',
        color: 'var(--text-primary)',
      }}
    >
      <div className="relative max-w-7xl mx-auto px-6 pt-4 pb-12 z-20">

        {/* Sort / Cols / Filter / Create toolbar */}
        <div className="flex items-center gap-2.5 mb-5 flex-wrap" style={{ fontFamily: 'var(--font-digital), monospace' }}>
          <ModCardSettings
            sort={sort}
            onSortChange={setSort}
            columns={columns}
            onColumnsChange={setColumns}
            owners={uniqueOwners}
            selectedOwners={selectedOwners}
            onToggleOwner={toggleOwner}
            onClearFilters={clearOwnerFilters}
            userKey={user?.key}
          />
          <div className="flex-1" />
          <Link
            href="/create"
            className="shrink-0 px-4 py-1.5 text-[11px] font-bold uppercase tracking-wider transition-all flex items-center rounded-full"
            style={{
              background: 'linear-gradient(135deg, rgba(167, 139, 250, 0.15), rgba(103, 232, 249, 0.08))',
              color: 'var(--text-primary)',
              fontFamily: 'var(--font-digital)',
              border: '1px solid rgba(167, 139, 250, 0.2)',
            }}
            onMouseEnter={e => { e.currentTarget.style.borderColor = 'rgba(167, 139, 250, 0.4)'; e.currentTarget.style.background = 'linear-gradient(135deg, rgba(167, 139, 250, 0.25), rgba(103, 232, 249, 0.12))' }}
            onMouseLeave={e => { e.currentTarget.style.borderColor = 'rgba(167, 139, 250, 0.2)'; e.currentTarget.style.background = 'linear-gradient(135deg, rgba(167, 139, 250, 0.15), rgba(103, 232, 249, 0.08))' }}
          >
            + CREATE
          </Link>
        </div>

        {error && (
          <div className="mb-8">
            <div className="border-4 flex items-start justify-between" style={{ borderColor: 'var(--border-strong)', backgroundColor: 'var(--bg-secondary)' }}>
              <div className="flex-1 px-6 py-5">
                <div className="flex items-center gap-3 mb-2">
                  <div className="w-3 h-3 bg-red-400 animate-pulse" />
                  <span className="text-red-400 font-bold text-base uppercase tracking-wider" style={{ fontFamily: 'var(--font-digital)' }}>▸ ERROR</span>
                </div>
                <div className="text-red-400 text-base font-bold" style={{ fontFamily: 'var(--font-digital)' }}>{error}</div>
              </div>
              <div className="flex gap-3 p-4">
                <button
                  onClick={fetchAll}
                  className="flex items-center gap-2 px-5 py-3 border-4 text-red-400 transition-all text-sm font-bold uppercase tracking-wider"
                  style={{ borderColor: 'var(--border-strong)', backgroundColor: 'var(--bg-primary)', fontFamily: 'var(--font-digital)' }}
                >
                  <RotateCcw size={16} />
                  RETRY
                </button>
                <button
                  onClick={() => setError(null)}
                  className="p-3 border-4 text-red-400 transition-all"
                  style={{ borderColor: 'var(--border-strong)', backgroundColor: 'var(--bg-primary)' }}
                >
                  <X size={16} />
                </button>
              </div>
            </div>
          </div>
        )}

        {!loading && mods.length === 0 && !error && (
          <div className="flex flex-col items-center justify-center py-24 font-mono border-4" style={{ backgroundColor: 'var(--bg-secondary)', borderColor: 'var(--border-strong)' }}>
            <div className="w-16 h-16 flex items-center justify-center mb-6 border-4" style={{ backgroundColor: 'var(--bg-input)', borderColor: 'var(--border-strong)' }}>
              <CubeIcon className="w-8 h-8" style={{ color: 'var(--text-primary)' }} />
            </div>
            <p className="text-lg font-bold uppercase tracking-wider" style={{ color: 'var(--text-secondary)', fontFamily: 'var(--font-digital)' }}>
              {searchTermToUse || selectedOwners.length > 0 ? '▸ NO MODULES MATCH YOUR FILTERS' : '▸ NO MODULES YET'}
            </p>
            {(searchTermToUse || selectedOwners.length > 0) && (
              <button
                onClick={() => { handleSearch(''); clearOwnerFilters() }}
                className="mt-8 px-8 py-4 text-base font-bold uppercase tracking-wider transition-all border-4"
                style={{ backgroundColor: 'var(--bg-input)', color: 'var(--text-primary)', borderColor: 'var(--border-strong)', fontFamily: 'var(--font-digital)' }}
              >
                ▸ CLEAR FILTERS
              </button>
            )}
          </div>
        )}

        {loading && mods.length === 0 && (
          <div className="flex items-center justify-center py-24 font-mono">
            <div className="flex flex-col items-center gap-5">
              <div className="w-16 h-16 border-4 flex items-center justify-center" style={{ borderColor: 'var(--border-strong)', backgroundColor: 'var(--bg-secondary)' }}>
                <ArrowPathIcon className="w-8 h-8 animate-spin" style={{ color: 'var(--text-primary)' }} />
              </div>
              <span className="text-lg font-bold uppercase tracking-wider" style={{ color: 'var(--text-secondary)', fontFamily: 'var(--font-digital)' }}>▸ LOADING MODULES...</span>
            </div>
          </div>
        )}

        <div className={`grid ${gridColsClass} gap-5`}>
          {mods.map((mod, index) => (
            <div
              key={`${mod.name}-${mod.key}`}
              className="animate-fade-in"
              style={{ animationDelay: `${index * 30}ms` }}
            >
              <ModCard mod={mod} card_enabled={true} />
            </div>
          ))}
        </div>

        {mods.length > 0 && mods.length < totalMods && (
          <div className="flex items-center justify-center py-8">
            <ArrowPathIcon className="w-5 h-5 animate-spin" style={{ color: 'var(--text-tertiary)' }} />
            <span className="ml-2 text-xs font-bold uppercase" style={{ color: 'var(--text-tertiary)', fontFamily: 'var(--font-digital)' }}>
              {mods.length} / {totalMods}
            </span>
          </div>
        )}

        {mods.length > 0 && mods.length >= totalMods && (
          <div className="flex items-center justify-center py-8">
            <span className="text-xs font-bold uppercase" style={{ color: 'var(--text-tertiary)', fontFamily: 'var(--font-digital)' }}>
              {totalMods} total
            </span>
          </div>
        )}
      </div>
    </div>
  )
}
