'use client'

import { Filter } from 'lucide-react'
import { useState } from 'react'
import { useUserContext } from '@/app/context'

type SortKey = 'recent' | 'name' | 'author' | 'balance' | 'updated' | 'created'

interface ModCardSettingsProps {
  sort: SortKey
  onSortChange: (sort: SortKey) => void
  columns: number
  onColumnsChange: (columns: number) => void
  userFilter: string
  onUserFilterChange: (filter: string) => void
  showMyModsOnly: boolean
  onShowMyModsOnlyChange: (show: boolean) => void
}

export const ModCardSettings = ({
  sort,
  onSortChange,
  columns,
  onColumnsChange,
  userFilter,
  onUserFilterChange,
  showMyModsOnly,
  onShowMyModsOnlyChange
}: ModCardSettingsProps) => {
  const [showFilters, setShowFilters] = useState(false)
  const { user } = useUserContext()

  return (
    <div className="flex items-center gap-3">
      <button
        onClick={() => setShowFilters(!showFilters)}
        className="flex items-center gap-2 px-4 py-2 bg-gradient-to-r from-blue-500/20 via-purple-500/20 to-pink-500/20 border border-blue-500/40 rounded-lg backdrop-blur-xl hover:from-blue-500/30 hover:via-purple-500/30 hover:to-pink-500/30 transition-all shadow-lg shadow-blue-500/20"
      >
        <Filter className="w-5 h-5 text-blue-300" />
        <span className="text-sm font-bold text-blue-300 uppercase">Filters</span>
      </button>

      {showFilters && (
        <div className="flex items-center gap-3">
          <select
            value={sort}
            onChange={(e) => onSortChange(e.target.value as SortKey)}
            className="px-4 py-2 bg-black/60 border border-purple-500/40 rounded-lg text-white font-mono text-sm backdrop-blur-xl focus:border-purple-500/60 outline-none"
          >
            <option value="recent">Recent</option>
            <option value="name">Name</option>
            <option value="author">Author</option>
            <option value="updated">Updated</option>
            <option value="created">Created</option>
          </select>

          <select
            value={columns}
            onChange={(e) => onColumnsChange(Number(e.target.value))}
            className="px-4 py-2 bg-black/60 border border-purple-500/40 rounded-lg text-white font-mono text-sm backdrop-blur-xl focus:border-purple-500/60 outline-none"
          >
            <option value={1}>1 Column</option>
            <option value={2}>2 Columns</option>
            <option value={3}>3 Columns</option>
            <option value={4}>4 Columns</option>
          </select>

          {user && (
            <label className="flex items-center gap-2 px-4 py-2 bg-black/60 border border-emerald-500/40 rounded-lg backdrop-blur-xl cursor-pointer hover:border-emerald-500/60 transition-all">
              <input
                type="checkbox"
                checked={showMyModsOnly}
                onChange={(e) => onShowMyModsOnlyChange(e.target.checked)}
                className="w-4 h-4 accent-emerald-500"
              />
              <span className="text-sm font-bold text-emerald-300 uppercase">My Mods Only</span>
            </label>
          )}
        </div>
      )}
    </div>
  )
}