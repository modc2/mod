'use client'

import React, { useState } from 'react'
import { Settings, ChevronDown, ChevronUp } from 'lucide-react'

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

export function ModCardSettings({
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
  onShowOnchainOnlyChange,
}: ModCardSettingsProps) {
  const [isExpanded, setIsExpanded] = useState(false)

  return (
    <div className="w-full">
      <button
        onClick={() => setIsExpanded(!isExpanded)}
        className="flex items-center gap-2 px-4 py-2 bg-gradient-to-r from-purple-500/20 to-pink-500/20 border-2 border-purple-500/40 rounded-lg hover:border-purple-400/60 transition-all backdrop-blur-xl shadow-lg"
      >
        <Settings size={18} className="text-purple-300" strokeWidth={2.5} />
        <span className="text-purple-200 font-bold text-sm uppercase tracking-wide">Filters</span>
        {isExpanded ? <ChevronUp size={16} className="text-purple-300" /> : <ChevronDown size={16} className="text-purple-300" />}
      </button>

      {isExpanded && (
        <div className="flex flex-wrap items-center gap-3 mt-3 p-4 bg-gradient-to-r from-purple-500/10 to-pink-500/10 border-2 border-purple-500/30 rounded-lg backdrop-blur-xl">
          {/* Sort */}
          <div className="flex items-center gap-2">
            <label className="text-purple-200 text-xs font-bold uppercase">Sort:</label>
            <select
              value={sort}
              onChange={(e) => onSortChange(e.target.value as SortKey)}
              className="px-3 py-1.5 bg-black/40 border border-purple-500/40 rounded-lg text-purple-200 text-sm font-medium focus:outline-none focus:border-purple-400/60 transition-all"
            >
              <option value="recent">Recent</option>
              <option value="name">Name</option>
              <option value="author">Author</option>
              <option value="updated">Updated</option>
              <option value="created">Created</option>
            </select>
          </div>

          {/* Columns */}
          <div className="flex items-center gap-2">
            <label className="text-purple-200 text-xs font-bold uppercase">Columns:</label>
            <select
              value={columns}
              onChange={(e) => onColumnsChange(parseInt(e.target.value))}
              className="px-3 py-1.5 bg-black/40 border border-purple-500/40 rounded-lg text-purple-200 text-sm font-medium focus:outline-none focus:border-purple-400/60 transition-all"
            >
              <option value="1">1</option>
              <option value="2">2</option>
              <option value="3">3</option>
              <option value="4">4</option>
            </select>
          </div>

          {/* User Filter */}
          <div className="flex items-center gap-2">
            <label className="text-purple-200 text-xs font-bold uppercase">User:</label>
            <input
              type="text"
              value={userFilter}
              onChange={(e) => onUserFilterChange(e.target.value)}
              placeholder="Filter by user..."
              className="px-3 py-1.5 bg-black/40 border border-purple-500/40 rounded-lg text-purple-200 text-sm placeholder-purple-400/50 focus:outline-none focus:border-purple-400/60 transition-all w-40"
            />
          </div>

          {/* My Mods Only */}
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={showMyModsOnly}
              onChange={(e) => onShowMyModsOnlyChange(e.target.checked)}
              className="w-4 h-4 rounded border-purple-500/40 bg-black/40 text-purple-500 focus:ring-purple-500/40"
            />
            <span className="text-purple-200 text-xs font-bold uppercase">My Mods</span>
          </label>

          {/* Local Only */}
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={showLocalOnly}
              onChange={(e) => onShowLocalOnlyChange(e.target.checked)}
              className="w-4 h-4 rounded border-purple-500/40 bg-black/40 text-purple-500 focus:ring-purple-500/40"
            />
            <span className="text-purple-200 text-xs font-bold uppercase">Local</span>
          </label>

          {/* Onchain Only */}
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={showOnchainOnly}
              onChange={(e) => onShowOnchainOnlyChange(e.target.checked)}
              className="w-4 h-4 rounded border-purple-500/40 bg-black/40 text-purple-500 focus:ring-purple-500/40"
            />
            <span className="text-purple-200 text-xs font-bold uppercase">Onchain</span>
          </label>
        </div>
      )}
    </div>
  )
}
