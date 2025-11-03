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
          className="bg-white/5 border border-white/10 text-white px-4 py-2 pl-10 rounded-xl text-sm hover:bg-white/10 hover:border-white/20 focus:outline-none focus:ring-2 focus:ring-white/20 transition-all w-64"
        />
        <MagnifyingGlassIcon className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-white/50" />
      </div>
    </form>
  )
}