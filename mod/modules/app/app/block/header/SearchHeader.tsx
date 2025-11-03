'use client'

import { useState, FormEvent, useEffect } from 'react'
import { useSearchContext } from '@/app/block/context/SearchContext'
import { MagnifyingGlassIcon, XMarkIcon } from '@heroicons/react/24/outline'

export function SearchHeader() {
  const { handleSearch } = useSearchContext()
  const [inputValue, setInputValue] = useState('')
  const [isNarrow, setIsNarrow] = useState(false)
  const [isExpanded, setIsExpanded] = useState(false)

  useEffect(() => {
    const checkWidth = () => {
      setIsNarrow(window.innerWidth < 640)
    }
    checkWidth()
    window.addEventListener('resize', checkWidth)
    return () => window.removeEventListener('resize', checkWidth)
  }, [])

  const onSubmit = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    const trimmed = inputValue.trim()
    if (trimmed) {
      handleSearch(trimmed)
    } else {
      handleSearch('')
    }
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
      if (trimmed) {
        handleSearch(trimmed)
      } else {
        handleSearch('')
      }
    }
    if (e.key === 'Escape') {
      setIsExpanded(false)
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

  if (isNarrow) {
    return (
      <form onSubmit={onSubmit} className="flex items-center">
        {!isExpanded ? (
          <button
            type="button"
            onClick={toggleExpand}
            className="p-3 rounded-lg border border-white/40 bg-white/10 hover:bg-white/15 hover:border-white/50 transition-all"
            style={{height: '56px', width: '56px'}}
          >
            <MagnifyingGlassIcon className="w-6 h-6 text-white" />
          </button>
        ) : (
          <div className="fixed inset-0 z-50 bg-black/95 backdrop-blur-sm flex items-start pt-4 px-4">
            <div className="relative w-full">
              <input
                type="text"
                value={inputValue}
                onChange={handleInputChange}
                onKeyDown={handleKeyDown}
                placeholder="Search mods..."
                autoFocus
                className="w-full bg-white/10 border-3 border-white/40 text-white px-6 py-4 pl-14 pr-14 rounded-xl text-xl font-bold hover:bg-white/15 hover:border-white/50 focus:outline-none focus:ring-3 focus:ring-white/60 focus:border-white/60 transition-all shadow-xl shadow-black/30"
                style={{height: '56px'}}
              />
              <MagnifyingGlassIcon className="absolute left-4 top-1/2 -translate-y-1/2 w-6 h-6 text-white/80" />
              <button
                type="button"
                onClick={toggleExpand}
                className="absolute right-4 top-1/2 -translate-y-1/2 p-1 hover:bg-white/10 rounded transition-all"
              >
                <XMarkIcon className="w-6 h-6 text-white/80" />
              </button>
            </div>
          </div>
        )}
      </form>
    )
  }

  return (
    <form onSubmit={onSubmit} className="flex items-center gap-2">
      <div className="relative">
        <input
          type="text"
          value={inputValue}
          onChange={handleInputChange}
          onKeyDown={handleKeyDown}
          placeholder="Search mods..."
          className="bg-white/10 border-3 border-white/40 text-white px-6 py-4 pl-14 rounded-xl text-xl font-bold hover:bg-white/15 hover:border-white/50 focus:outline-none focus:ring-3 focus:ring-white/60 focus:border-white/60 transition-all shadow-xl shadow-black/30"
          style={{height: '56px', width: '320px'}}
        />
        <MagnifyingGlassIcon className="absolute left-4 top-1/2 -translate-y-1/2 w-6 h-6 text-white/80" />
      </div>
    </form>
  )
}