'use client'

import { useState, FormEvent } from 'react'
import { useSearchContext } from '@/app/block/context/SearchContext'
import { MagnifyingGlassIcon } from '@heroicons/react/24/outline'

export function SearchHeader() {
  const { handleSearch } = useSearchContext()
  const [inputValue, setInputValue] = useState('')

  const onSubmit = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    const trimmed = inputValue.trim()
    if (trimmed) {
      handleSearch(trimmed)
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      e.preventDefault()
      const trimmed = inputValue.trim()
      if (trimmed) {
        handleSearch(trimmed)
      }
    }
  }

  return (
    <form onSubmit={onSubmit} className="flex items-center gap-2">
      <div className="relative">
        <input
          type="text"
          value={inputValue}
          onChange={(e) => setInputValue(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Search mods..."
          className="bg-white/10 border-3 border-white/40 text-white px-6 py-4 pl-14 rounded-xl text-xl font-bold hover:bg-white/15 hover:border-white/50 focus:outline-none focus:ring-3 focus:ring-white/60 focus:border-white/60 transition-all w-80 shadow-xl shadow-black/30"
        />
        <MagnifyingGlassIcon className="absolute left-4 top-1/2 -translate-y-1/2 w-6 h-6 text-white/80" />
      </div>
    </form>
  )
}