'use client'

import { Filter } from 'lucide-react'
import { useState } from 'react'
import { useUserContext } from '@/block/context'
import { HomeIcon, GlobeAltIcon } from '@heroicons/react/24/outline'

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
  showLocalOnly: boolean
  onShowLocalOnlyChange: (show: boolean) => void
  showOnchainOnly: boolean
  onShowOnchainOnlyChange: (show: boolean) => void
}

export const ModCardSettings = ({
  sort,
  onSortChange,
  columns,
  onColumnsChange,
  userFilter,
  onUserFilterChange,
  showMyModsOnly,
  onShowMyModsOnlyChange,
  showLocalOnly,
  onShowLocalOnlyChange,
  showOnchainOnly,
  onShowOnchainOnlyChange
}: ModCardSettingsProps) => {
  const [showFilters, setShowFilters] = useState(false)
  const { user } = useUserContext()

  return (
    <div className="flex items-center gap-3">
      <button
        onClick={() => setShowFilters(!showFilters)}
        className="flex items-center gap-2 px-4 py-2 bg-gradient-to-r from-blue-500/20 via-purple-500/20 to-pink-500/20 border-2 border-blue-500/50 rounded-xl backdrop-blur-xl hover:from-blue-500/30 hover:via-purple-500/30 hover:to-pink-500/30 hover:border-blue-400/70 transition-all duration-300 shadow-lg shadow-blue-500/20 hover:shadow-blue-500/40"
      >
        <Filter className="w-5 h-5 text-blue-300" strokeWidth={2.5} />
        <span className="text-sm font-black text-blue-300 uppercase tracking-wider">Filters</span>
      </button>

      {showFilters && (
        <div className="flex items-center gap-3 animate-in fade-in slide-in-from-left-2 duration-300">
          <select
            value={sort}
            onChange={(e) => onSortChange(e.target.value as SortKey)}
            className="px-4 py-2 bg-gradient-to-br from-black/80 to-gray-900/80 border-2 border-purple-500/50 rounded-xl text-white font-mono font-bold text-sm backdrop-blur-xl focus:border-purple-400/70 outline-none hover:border-purple-400/60 transition-all duration-300 shadow-lg shadow-purple-500/20 cursor-pointer"
          >
            <option value="recent">⚡ Recent</option>
            <option value="name">📝 Name</option>
            <option value="author">👤 Author</option>
            <option value="updated">🔄 Updated</option>
            <option value="created">✨ Created</option>
          </select>

          <select
            value={columns}
            onChange={(e) => onColumnsChange(Number(e.target.value))}
            className="px-4 py-2 bg-gradient-to-br from-black/80 to-gray-900/80 border-2 border-purple-500/50 rounded-xl text-white font-mono font-bold text-sm backdrop-blur-xl focus:border-purple-400/70 outline-none hover:border-purple-400/60 transition-all duration-300 shadow-lg shadow-purple-500/20 cursor-pointer"
          >
            <option value={1}>📱 1 Column</option>
            <option value={2}>📊 2 Columns</option>
            <option value={3}>🎯 3 Columns</option>
            <option value={4}>🚀 4 Columns</option>
          </select>

          {user && (
            <label className="flex items-center gap-2 px-4 py-2 bg-gradient-to-br from-black/80 to-gray-900/80 border-2 border-emerald-500/50 rounded-xl backdrop-blur-xl cursor-pointer hover:border-emerald-400/70 hover:shadow-emerald-500/40 transition-all duration-300 shadow-lg shadow-emerald-500/20 group">
              <input
                type="checkbox"
                checked={showMyModsOnly}
                onChange={(e) => onShowMyModsOnlyChange(e.target.checked)}
                className="w-4 h-4 accent-emerald-500 cursor-pointer"
              />
              <span className="text-sm font-black text-emerald-300 uppercase tracking-wider group-hover:text-emerald-200 transition-colors">My Mods</span>
            </label>
          )}

          <label className="flex items-center gap-2 px-4 py-2 bg-gradient-to-br from-black/80 to-gray-900/80 border-2 border-yellow-600/50 rounded-xl backdrop-blur-xl cursor-pointer hover:border-yellow-500/70 hover:shadow-yellow-600/40 transition-all duration-300 shadow-lg shadow-yellow-600/20 group">
            <input
              type="checkbox"
              checked={showLocalOnly}
              onChange={(e) => onShowLocalOnlyChange(e.target.checked)}
              className="w-4 h-4 accent-yellow-600 cursor-pointer"
            />
            <HomeIcon className="w-5 h-5 text-yellow-600 group-hover:text-yellow-500 transition-colors" strokeWidth={2.5} />
            <span className="text-sm font-black text-yellow-600 uppercase tracking-wider group-hover:text-yellow-500 transition-colors">Offchain</span>
          </label>

          <label className="flex items-center gap-2 px-4 py-2 bg-gradient-to-br from-black/80 to-gray-900/80 border-2 border-cyan-500/50 rounded-xl backdrop-blur-xl cursor-pointer hover:border-cyan-400/70 hover:shadow-cyan-500/40 transition-all duration-300 shadow-lg shadow-cyan-500/20 group">
            <input
              type="checkbox"
              checked={showOnchainOnly}
              onChange={(e) => onShowOnchainOnlyChange(e.target.checked)}
              className="w-4 h-4 accent-cyan-500 cursor-pointer"
            />
            <GlobeAltIcon className="w-5 h-5 text-cyan-400 group-hover:text-cyan-300 transition-colors" strokeWidth={2.5} />
            <span className="text-sm font-black text-cyan-400 uppercase tracking-wider group-hover:text-cyan-300 transition-colors">Onchain</span>
          </label>
        </div>
      )}
    </div>
  )
}