'use client'

import { useSearchContext } from '@/app/block/context/SearchContext'
import { MagnifyingGlassIcon } from '@heroicons/react/24/outline'

export function SearchHeader() {
  const { searchFilters, setSearchFilters } = useSearchContext()

  return (
    <div className="relative w-full">
      <MagnifyingGlassIcon className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-white/40 pointer-events-none" />
      <input
        type="text"
        placeholder="Search..."
        value={searchFilters.searchTerm || ''}
        onChange={(e) => setSearchFilters({ ...searchFilters, searchTerm: e.target.value })}
        className="w-full h-9 bg-white/5 border border-white/10 rounded-lg pl-9 pr-3 text-sm text-white placeholder-white/40 focus:outline-none focus:ring-2 focus:ring-white/20 focus:border-white/20 transition-all"
      />
    </div>
  )
}