'use client'

import { useState, FormEvent, useEffect } from 'react'
import { useSearchContext } from '@/app/block/context/SearchContext'
import { MagnifyingGlassIcon, XMarkIcon } from '@heroicons/react/24/outline'
import { useRouter } from 'next/navigation'

export function SearchHeader() {
  const { handleSearch } = useSearchContext()
  const router = useRouter()
  const [inputValue, setInputValue] = useState('')
  const [isExpanded, setIsExpanded] = useState(true)

  const onSubmit = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    const trimmed = inputValue.trim()
    handleSearch(trimmed)
    router.push('/mod/explore')
  }

  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const value = e.target.value
    setInputValue(value)
    if (value === '') {
      handleSearch('')
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      e.preventDefault()
      const trimmed = inputValue.trim()
      handleSearch(trimmed)
      router.push('/mod/explore')
    }
    if (e.key === 'Escape') {
      setInputValue('')
      handleSearch('')
    }
  }

  const toggleExpand = () => {
    setIsExpanded(!isExpanded)
    if (isExpanded) {
      setInputValue('')
      handleSearch('')
    }
  }

  return (
    <form onSubmit={onSubmit} className="flex items-center justify-center">
      {!isExpanded ? (
        <button
          type="button"
          onClick={toggleExpand}
          className="p-3 rounded-lg border-2 border-green-500/50 bg-black/80 hover:bg-black/90 hover:border-green-500 transition-all hover:scale-105 active:scale-95 shadow-md"
          style={{height: '56px', width: '56px'}}
          title="Search"
        >
          <MagnifyingGlassIcon className="w-6 h-6 text-white" />
        </button>
      ) : (
        <div className="relative animate-in fade-in slide-in-from-left-2 duration-300">
          <input
            type="text"
            value={inputValue}
            onChange={handleInputChange}
            onKeyDown={handleKeyDown}
            placeholder="Search mods..."
            className="bg-black/90 border-2 border-green-500/50 text-white px-4 py-3 pl-12 pr-12 rounded-lg text-lg font-normal hover:bg-black hover:border-green-500 focus:outline-none focus:ring-2 focus:ring-green-500/70 focus:border-green-500 transition-all shadow-md backdrop-blur-sm"
            style={{height: '56px', width: '400px'}}
          />
          <MagnifyingGlassIcon className="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 text-white/80" />
          <button
            type="button"
            onClick={toggleExpand}
            className="absolute right-4 top-1/2 -translate-y-1/2 p-1 hover:bg-white/10 rounded transition-all hover:scale-110 active:scale-95"
            title="Close search"
          >
            <XMarkIcon className="w-5 h-5 text-white/80" />
          </button>
        </div>
      )}
    </form>
  )
}