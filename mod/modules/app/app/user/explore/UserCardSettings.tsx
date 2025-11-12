'use client'

import { useState, useEffect } from 'react'
import { ChevronDown } from 'lucide-react'

type SortKey = 'recent' | 'name' | 'balance' | 'modules'

interface UserCardSettingsProps {
  sort: SortKey
  onSortChange: (sort: SortKey) => void
  columns: number
  onColumnsChange: (columns: number) => void
}

export function UserCardSettings({ sort, onSortChange, columns, onColumnsChange }: UserCardSettingsProps) {
  const sortOptions: { key: SortKey; label: string }[] = [
    { key: 'recent', label: 'Recent' },
    { key: 'name', label: 'Name' },
    { key: 'balance', label: 'Balance' },
    { key: 'modules', label: 'Modules' }
  ]

  return (
    <div className="flex items-center gap-3 px-4 py-2 bg-gradient-to-r from-purple-500/10 via-pink-500/10 to-blue-500/10 border border-purple-500/30 rounded-lg backdrop-blur-xl">
      {/* Sort Dropdown */}
      <div className="relative">
        <select
          value={sort}
          onChange={(e) => onSortChange(e.target.value as SortKey)}
          className="appearance-none bg-purple-500/20 text-purple-300 border border-purple-500/40 rounded-lg px-3 py-1.5 pr-8 text-sm font-bold uppercase cursor-pointer hover:bg-purple-500/30 transition-all focus:outline-none focus:ring-2 focus:ring-purple-400/50"
        >
          {sortOptions.map((option) => (
            <option key={option.key} value={option.key} className="bg-black">
              {option.label}
            </option>
          ))}
        </select>
        <ChevronDown className="absolute right-2 top-1/2 -translate-y-1/2 w-4 h-4 text-purple-300 pointer-events-none" />
      </div>

      {/* Columns Dropdown */}
      <div className="relative">
        <select
          value={columns}
          onChange={(e) => onColumnsChange(parseInt(e.target.value))}
          className="appearance-none bg-green-500/20 text-green-300 border border-green-500/40 rounded-lg px-3 py-1.5 pr-8 text-sm font-bold uppercase cursor-pointer hover:bg-green-500/30 transition-all focus:outline-none focus:ring-2 focus:ring-green-400/50"
        >
          {[1, 2, 3, 4].map((num) => (
            <option key={num} value={num} className="bg-black">
              {num} Col{num > 1 ? 's' : ''}
            </option>
          ))}
        </select>
        <ChevronDown className="absolute right-2 top-1/2 -translate-y-1/2 w-4 h-4 text-green-300 pointer-events-none" />
      </div>
    </div>
  )
}