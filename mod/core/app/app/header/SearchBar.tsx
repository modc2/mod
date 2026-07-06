"use client";

import { useState, useEffect, useRef, useCallback } from 'react'
import { MagnifyingGlassIcon, XMarkIcon } from '@heroicons/react/24/outline'
import { useSearchContext } from '@/context/SearchContext'
import { userContext } from '@/context'
import { useRouter, usePathname } from 'next/navigation'
import { motion, AnimatePresence } from 'framer-motion'

interface ModSuggestion {
  name: string
  key?: string
}

export function SearchBar() {
  const { handleSearch, searchFilters } = useSearchContext()
  const { client } = userContext()
  const router = useRouter()
  const pathname = usePathname()
  const [inputValue, setInputValue] = useState('')
  const [isExpanded, setIsExpanded] = useState(false)
  const [suggestions, setSuggestions] = useState<ModSuggestion[]>([])
  const [showSuggestions, setShowSuggestions] = useState(false)
  const [selectedIndex, setSelectedIndex] = useState(-1)
  const inputRef = useRef<HTMLInputElement>(null)
  const dropdownRef = useRef<HTMLDivElement>(null)
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    setInputValue(searchFilters.searchTerm || '')
  }, [searchFilters.searchTerm])

  // Close suggestions on outside click
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setShowSuggestions(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  const fetchSuggestions = useCallback(async (term: string) => {
    if (!client || term.length < 1) {
      setSuggestions([])
      setShowSuggestions(false)
      return
    }
    try {
      const raw = await client.call('mods', { search: term, page: 0, page_size: 8 })
      const results = Array.isArray(raw) ? raw : []
      setSuggestions(results.map((m: any) => ({ name: m.name, key: m.key })))
      setShowSuggestions(results.length > 0)
      setSelectedIndex(-1)
    } catch {
      setSuggestions([])
      setShowSuggestions(false)
    }
  }, [client])

  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const value = e.target.value
    setInputValue(value)
    handleSearch(value)

    // Debounced suggestion fetch
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => {
      fetchSuggestions(value.trim())
    }, 200)
  }

  const selectMod = (modName: string) => {
    setShowSuggestions(false)
    setSuggestions([])
    setInputValue('')
    handleSearch('')
    setIsExpanded(false)
    inputRef.current?.blur()
    router.push(`/mod/${modName}`)
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (showSuggestions && suggestions.length > 0) {
      if (e.key === 'ArrowDown') {
        e.preventDefault()
        setSelectedIndex(prev => (prev < suggestions.length - 1 ? prev + 1 : 0))
        return
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault()
        setSelectedIndex(prev => (prev > 0 ? prev - 1 : suggestions.length - 1))
        return
      }
      if (e.key === 'Enter' && selectedIndex >= 0) {
        e.preventDefault()
        selectMod(suggestions[selectedIndex].name)
        return
      }
    }

    if (e.key === 'Enter') {
      e.preventDefault()
      const trimmed = inputValue.trim()
      handleSearch(trimmed)
      setShowSuggestions(false)
      if (pathname !== '/mods') {
        router.push('/mods')
      }
    }
    if (e.key === 'Escape') {
      setInputValue('')
      handleSearch('')
      setIsExpanded(false)
      setShowSuggestions(false)
    }
  }

  const handleClear = () => {
    setInputValue('')
    handleSearch('')
    setSuggestions([])
    setShowSuggestions(false)
  }

  const handleSearchClick = () => {
    const trimmed = inputValue.trim()
    if (trimmed) {
      handleSearch(trimmed)
      setShowSuggestions(false)
      if (pathname !== '/mods') {
        router.push('/mods')
      }
    }
  }

  return (
    <div className="relative flex items-center">
      <AnimatePresence>
        {isExpanded ? (
          <motion.div
            initial={{ width: 0, opacity: 0 }}
            animate={{ width: '320px', opacity: 1 }}
            exit={{ width: 0, opacity: 0 }}
            transition={{ duration: 0.3, ease: 'easeInOut' }}
            className="flex items-center gap-2 overflow-visible"
          >
            <div className="relative flex-1" ref={dropdownRef}>
              <input
                ref={inputRef}
                type="text"
                value={inputValue}
                onChange={handleInputChange}
                onKeyDown={handleKeyDown}
                onFocus={() => { if (suggestions.length > 0) setShowSuggestions(true) }}
                placeholder="Search mods..."
                autoFocus
                className="w-full px-4 py-3 pr-20 bg-black/50 border-2 border-yellow-500/40 rounded-xl text-white placeholder-gray-400 focus:outline-none focus:border-yellow-500/60 backdrop-blur-xl transition-all"
                style={{
                  boxShadow: '0 0 12px rgba(239, 220, 11, 0.2)'
                }}
              />
              <div className="absolute right-2 top-1/2 -translate-y-1/2 flex items-center gap-1">
                {inputValue && (
                  <button
                    onClick={handleClear}
                    className="p-1.5 hover:bg-white/10 rounded-lg transition-all"
                  >
                    <XMarkIcon className="w-4 h-4 text-gray-400" />
                  </button>
                )}
                <button
                  onClick={handleSearchClick}
                  className="p-1.5 hover:bg-yellow-500/20 rounded-lg transition-all"
                >
                  <MagnifyingGlassIcon className="w-4 h-4" style={{ color: '#d8cc1bff' }} />
                </button>
              </div>

              {/* Suggestions dropdown */}
              {showSuggestions && suggestions.length > 0 && (
                <div
                  className="absolute top-full left-0 right-0 mt-1 rounded-xl border-2 border-yellow-500/30 bg-black/90 backdrop-blur-xl overflow-hidden z-50"
                  style={{ boxShadow: '0 8px 24px rgba(0, 0, 0, 0.6), 0 0 12px rgba(239, 220, 11, 0.15)' }}
                >
                  {suggestions.map((mod, i) => (
                    <button
                      key={mod.name}
                      onMouseDown={(e) => {
                        e.preventDefault()
                        selectMod(mod.name)
                      }}
                      onMouseEnter={() => setSelectedIndex(i)}
                      className="w-full px-4 py-2.5 text-left flex items-center gap-3 transition-all"
                      style={{
                        backgroundColor: selectedIndex === i ? 'rgba(239, 220, 11, 0.1)' : 'transparent',
                        borderBottom: i < suggestions.length - 1 ? '1px solid rgba(255,255,255,0.05)' : 'none',
                      }}
                    >
                      <MagnifyingGlassIcon className="w-3.5 h-3.5 shrink-0" style={{ color: 'rgba(239, 220, 11, 0.5)' }} />
                      <span className="text-sm font-medium text-white truncate">{mod.name}</span>
                      {mod.key && (
                        <span className="ml-auto text-xs text-gray-500 truncate max-w-[80px]">
                          {mod.key.slice(0, 8)}...
                        </span>
                      )}
                    </button>
                  ))}
                </div>
              )}
            </div>
            <button
              onClick={() => { setIsExpanded(false); setShowSuggestions(false) }}
              className="p-3 rounded-xl border-2 border-white/20 hover:border-white/40 hover:bg-white/10 transition-all"
              style={{ height: '48px', width: '48px' }}
            >
              <XMarkIcon className="w-5 h-5 text-gray-400" />
            </button>
          </motion.div>
        ) : (
          <motion.button
            initial={{ scale: 0.9, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            exit={{ scale: 0.9, opacity: 0 }}
            onClick={() => setIsExpanded(true)}
            className="p-3 rounded-xl border-2 transition-all active:scale-95 backdrop-blur-xl"
            style={{
              height: '60px',
              width: '60px',
              backgroundColor: 'rgba(239, 220, 11, 0.1)',
              borderColor: 'rgba(239, 220, 11, 0.4)',
              boxShadow: '0 0 12px rgba(239, 220, 11, 0.2)'
            }}
            title="Search"
          >
            <MagnifyingGlassIcon className="w-8 h-8" style={{ color: '#d8cc1bff' }} />
          </motion.button>
        )}
      </AnimatePresence>
    </div>
  )
}
