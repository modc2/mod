'use client'

import { useEffect, useMemo, useState } from 'react'
import { Client } from '@/app/block/client/client'
import { Loading } from '@/app/block/Loading'
import ModuleCard from '@/app/block/mod/ModuleCard'
import { ModuleType } from '@/app/block/types/block/mod'
import { Footer } from '@/app/block/Footer'
import { useSearchContext } from '@/app/block/context/SearchContext'
import { useUserContext } from '@/app/block/context/UserContext'
import { Plus, X } from 'lucide-react'

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
      const list = Array.isArray(raw) ? sortModules(raw) : []
      setMods(list)
    } catch (err: any) {
      setError(err?.message || 'Failed to load mods')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchAll()
  }, [keyInstance, searchTerm, sort])

  return (
    <div className="min-h-screen bg-black text-green-500 font-mono">
      <header className="sticky top-0 z-40 border-b border-green-900/40 bg-black/80 backdrop-blur">
        <div className="mx-auto px-3 py-2 flex items-center gap-3">
          <div className="flex-1 truncate">
            <span className="tracking-wide">mods</span>
            <span className="mx-2 text-green-400/70">•</span>
            <span className="text-green-400/80">{mods.length}</span>
            {searchTerm && (
              <>
                <span className="mx-2 text-green-400/70">•</span>
                <span className="text-green-400/70">query:</span>
                <span className="ml-1">{searchTerm}</span>
              </>
            )}
          </div>
        </div>
      </header>

      {error && (
        <div className="mx-auto px-3">
          <div className="mt-3 flex items-center justify-between p-2 border border-red-500 text-red-400 rounded">
            <span>error: {error}</span>
            <div className="flex items-center gap-2">
              <button onClick={fetchAll} className="px-2 py-1 border border-red-500 rounded hover:bg-red-900/20">
                retry
              </button>
              <button onClick={() => setError(null)} className="px-2 py-1 border border-red-500 rounded hover:bg-red-900/20">
                <X size={14} />
              </button>
            </div>
          </div>
        </div>
      )}

      <main className="p-3" role="main">
        {!loading && mods.length === 0 && !error && (
          <div className="mx-auto max-w-3xl my-20 text-center">
            <div className="text-green-300/80 mb-3">
              {searchTerm ? 'no mods match your search.' : 'no mods yet.'}
            </div>
            <button
              className="inline-flex items-center gap-2 border border-green-600 rounded px-3 py-2 hover:bg-green-900/30"
            >
              <Plus size={16} /> create your first mod
            </button>
          </div>
        )}

        {loading && (
          <div className="py-10 text-center">
            <Loading />
          </div>
        )}

        <div
          role="list"
          className="grid gap-4 mx-auto"
          style={{
            gridTemplateColumns: `repeat(auto-fill, minmax(280px, 1fr))`,
            maxWidth: '100%',
          }}
        >
          {mods.map((m) => (
            <div key={m.key} role="listitem">
              <ModuleCard mod={m} />
            </div>
          ))}
        </div>
      </main>

      <Footer />
    </div>
  )
}
