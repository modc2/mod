"use client";

import { Grid3x3, Grid2x2, LayoutGrid, Filter } from 'lucide-react'
import { OwnerFilter } from './explore/OwnerFilter'

type SortKey = 'recent' | 'name' | 'author' | 'balance' | 'updated' | 'created'

interface ModCardSettingsProps {
  sort: SortKey
  onSortChange: (sort: SortKey) => void
  columns: number
  onColumnsChange: (columns: number) => void
  owners?: string[]
  selectedOwners?: string[]
  onToggleOwner?: (owner: string) => void
  onClearFilters?: () => void
  userKey?: string
}

export function ModCardSettings({
  sort,
  onSortChange,
  columns,
  onColumnsChange,
  owners = [],
  selectedOwners = [],
  onToggleOwner = () => {},
  onClearFilters = () => {},
  userKey,
}: ModCardSettingsProps) {
  return (
    <div className="font-mono flex items-center gap-3 flex-shrink-0" style={{ fontFamily: 'IBM Plex Mono, Courier New, monospace' }}>
      {/* Sort */}
      <div className="flex items-center gap-2 flex-shrink-0">
        <label className="text-[10px] font-black uppercase tracking-[0.15em] flex-shrink-0" style={{ color: 'var(--text-tertiary)' }}>
          SORT
        </label>
        <select
          value={sort}
          onChange={(e) => onSortChange(e.target.value as SortKey)}
          className="px-2.5 py-1.5 text-[12px] font-mono font-bold focus:outline-none rounded-lg transition-colors flex-shrink-0"
          style={{ backgroundColor: 'var(--bg-input)', border: '1.5px solid var(--border-color)', color: 'var(--text-primary)' }}
        >
          <option value="recent">Recent</option>
          <option value="name">Name</option>
          <option value="author">Author</option>
          <option value="updated">Updated</option>
          <option value="created">Created</option>
        </select>
      </div>

      <div className="w-px h-5 flex-shrink-0" style={{ backgroundColor: 'var(--border-color)' }} />

      {/* Columns */}
      <div className="flex items-center gap-1.5 flex-shrink-0">
        <label className="text-[10px] font-black uppercase tracking-[0.15em] flex-shrink-0" style={{ color: 'var(--text-tertiary)' }}>
          COLS
        </label>
        <div className="flex gap-px">
          {[1, 2, 3, 4].map((num) => (
            <button
              key={num}
              onClick={() => onColumnsChange(num)}
              className={`p-1.5 transition-all rounded-md ${
                columns === num
                  ? 'bg-blue-500/20 text-blue-400'
                  : ''
              }`}
              style={columns === num
                ? { border: '1.5px solid rgba(59,130,246,0.5)' }
                : { border: '1.5px solid var(--border-color)', color: 'var(--text-tertiary)', backgroundColor: 'var(--bg-input)' }
              }
            >
              {num === 1 && <LayoutGrid className="w-3.5 h-3.5" />}
              {num === 2 && <Grid2x2 className="w-3.5 h-3.5" />}
              {num === 3 && <Grid3x3 className="w-3.5 h-3.5" />}
              {num === 4 && <Filter className="w-3.5 h-3.5" />}
            </button>
          ))}
        </div>
      </div>

      {/* Owner filter */}
      {owners.length > 0 && (
        <>
          <div className="w-px h-5 flex-shrink-0" style={{ backgroundColor: 'var(--border-color)' }} />
          <div className="flex-shrink-0">
            <OwnerFilter
              owners={owners}
              selectedOwners={selectedOwners}
              onToggleOwner={onToggleOwner}
              onClearFilters={onClearFilters}
              userKey={userKey}
            />
          </div>
        </>
      )}
    </div>
  )
}
