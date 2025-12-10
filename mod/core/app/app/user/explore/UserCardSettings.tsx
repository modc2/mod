'use client'

import { Filter } from 'lucide-react'
import { useState } from 'react'

type SortKey = 'recent' | 'name' | 'balance' | 'modules'

interface UserCardSettingsProps {
  sort: SortKey
  onSortChange: (sort: SortKey) => void
  columns: number
  onColumnsChange: (columns: number) => void
}

export const UserCardSettings = ({
  sort,
  onSortChange,
  columns,
  onColumnsChange
}: UserCardSettingsProps) => {
  const [showFilters, setShowFilters] = useState(false)

  return (
    <div className="flex items-center gap-3">
      <div className="flex items-center gap-2 px-4 py-2 bg-gradient-to-r from-purple-500/20 via-blue-500/20 to-pink-500/20 border border-purple-500/40 rounded-lg backdrop-blur-xl shadow-lg">
        <input
          type="text"
          placeholder="Search users..."
          className="bg-transparent border-none outline-none text-white placeholder-white/50 font-mono text-sm w-64"
          disabled
        />
      </div>
      
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
            <option value="balance">Balance</option>
            <option value="modules">Modules</option>
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
        </div>
      )}
    </div>
  )
}