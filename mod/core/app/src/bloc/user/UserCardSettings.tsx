'use client'

import { ChevronDown, ChevronUp } from 'lucide-react'
import { useState, useEffect } from 'react'

type SortKey = 'recent' | 'name' | 'balance' | 'modules'

interface UserCardSettingsProps {
  sort: SortKey
  onSortChange: (sort: SortKey) => void
  columns: number
  onColumnsChange: (columns: number) => void
  userFilter?: string
  onUserFilterChange?: (filter: string) => void
  showMyUsersOnly?: boolean
  onShowMyUsersOnlyChange?: (show: boolean) => void
}

export const UserCardSettings = ({
  sort,
  onSortChange,
  columns,
  onColumnsChange,
  userFilter = '',
  onUserFilterChange,
  showMyUsersOnly = false,
  onShowMyUsersOnlyChange
}: UserCardSettingsProps) => {
  const [isExpanded, setIsExpanded] = useState(false)

  useEffect(() => {
    if (typeof window !== 'undefined') {
      const saved = localStorage.getItem('user_filters_expanded')
      setIsExpanded(saved === 'true')
    }
  }, [])

  const toggleExpanded = () => {
    const newState = !isExpanded
    setIsExpanded(newState)
    if (typeof window !== 'undefined') {
      localStorage.setItem('user_filters_expanded', String(newState))
    }
  }

  return (
    <div className="relative z-50">
      <div className="bg-black border-2 border-white/20 rounded-xl overflow-hidden">
        <button
          onClick={toggleExpanded}
          className="px-4 py-3 flex items-center gap-2 bg-black hover:bg-white/5 transition-colors whitespace-nowrap"
        >
          <span className="text-white font-bold uppercase tracking-wider" style={{ fontFamily: "'Courier New', 'Consolas', 'Monaco', monospace" }}>
            Filters
          </span>
          {isExpanded ? (
            <ChevronUp className="w-5 h-5 text-white" />
          ) : (
            <ChevronDown className="w-5 h-5 text-white" />
          )}
        </button>

        {isExpanded && (
          <div className="absolute top-full left-0 mt-2 p-4 bg-black border-2 border-white/20 rounded-xl shadow-2xl backdrop-blur-xl min-w-[400px]">
            <div className="flex flex-col gap-4">
              <div className="flex items-center gap-4">
                <div className="flex items-center gap-2">
                  <label className="text-white text-sm font-bold uppercase whitespace-nowrap" style={{ fontFamily: "'Courier New', 'Consolas', 'Monaco', monospace" }}>
                    Sort:
                  </label>
                  <select
                    value={sort}
                    onChange={(e) => onSortChange(e.target.value as SortKey)}
                    className="px-3 py-2 bg-black border border-white/20 rounded-lg text-white focus:outline-none focus:border-white/40 transition-colors"
                    style={{ fontFamily: "'Courier New', 'Consolas', 'Monaco', monospace" }}
                  >
                    <option value="recent">Recent</option>
                    <option value="balance">Balance</option>
                    <option value="modules">Modules</option>
                  </select>
                </div>

                <div className="flex items-center gap-2">
                  <label className="text-white text-sm font-bold uppercase whitespace-nowrap" style={{ fontFamily: "'Courier New', 'Consolas', 'Monaco', monospace" }}>
                    Columns:
                  </label>
                  <select
                    value={columns}
                    onChange={(e) => onColumnsChange(Number(e.target.value))}
                    className="px-3 py-2 bg-black border border-white/20 rounded-lg text-white focus:outline-none focus:border-white/40 transition-colors"
                    style={{ fontFamily: "'Courier New', 'Consolas', 'Monaco', monospace" }}
                  >
                    <option value={1}>1</option>
                    <option value={2}>2</option>
                    <option value={3}>3</option>
                    <option value={4}>4</option>
                  </select>
                </div>
              </div>

              {onUserFilterChange && (
                <div className="flex items-center gap-2">
                  <label className="text-white text-sm font-bold uppercase whitespace-nowrap" style={{ fontFamily: "'Courier New', 'Consolas', 'Monaco', monospace" }}>
                    User:
                  </label>
                  <input
                    type="text"
                    value={userFilter}
                    onChange={(e) => onUserFilterChange(e.target.value)}
                    placeholder="Filter by user key..."
                    className="flex-1 px-3 py-2 bg-black border border-white/20 rounded-lg text-white placeholder-white/40 focus:outline-none focus:border-white/40 transition-colors"
                    style={{ fontFamily: "'Courier New', 'Consolas', 'Monaco', monospace" }}
                  />
                </div>
              )}

              {onShowMyUsersOnlyChange && (
                <div className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    id="myUsersOnly"
                    checked={showMyUsersOnly}
                    onChange={(e) => onShowMyUsersOnlyChange(e.target.checked)}
                    className="w-4 h-4 bg-black border border-white/20 rounded focus:ring-2 focus:ring-white/40"
                  />
                  <label htmlFor="myUsersOnly" className="text-white text-sm font-bold uppercase cursor-pointer" style={{ fontFamily: "'Courier New', 'Consolas', 'Monaco', monospace" }}>
                    My Users Only
                  </label>
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
