'use client'

import React, { createContext, useContext, useState, ReactNode } from 'react'

interface SearchFilters {
  searchTerm: string
  page?: number
  pageSize?: number
}

interface SearchContextType {
  searchFilters: SearchFilters
  setSearchFilters: (filters: SearchFilters) => void
  handleSearch: (term: string) => void
}

const SearchContext = createContext<SearchContextType | undefined>(undefined)

export function SearchProvider({ children }: { children: ReactNode }) {
  const [searchFilters, setSearchFilters] = useState<SearchFilters>({
    searchTerm: '',
    page: 1,
    pageSize: 20
  })

  const handleSearch = (term: string) => {
    setSearchFilters(prev => ({
      ...prev,
      searchTerm: term,
      page: 1
    }))
  }

  return (
    <SearchContext.Provider value={{ searchFilters, setSearchFilters, handleSearch }}>
      {children}
    </SearchContext.Provider>
  )
}

export function useSearchContext() {
  const context = useContext(SearchContext)
  if (!context) {
    throw new Error('useSearchContext must be used within SearchProvider')
  }
  return context
}