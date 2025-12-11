'use client'

import { UserHeader } from './UserHeader'
import { NodeUrlSettings } from './NodeUrlSettings'
import { usePathname } from 'next/navigation'
import Link from 'next/link'
import { CubeIcon, UsersIcon, Bars3Icon, MagnifyingGlassIcon, Squares2X2Icon, XMarkIcon, ArrowsRightLeftIcon, ArrowsUpDownIcon } from '@heroicons/react/24/outline'
import { text2color } from '@/bloc/utils'
import { useState, useEffect } from 'react'
import { useSearchContext } from '@/bloc/context/SearchContext'
import { useSplitScreenContext } from '@/bloc/context/SplitScreenContext'
import { useRouter } from 'next/navigation'

export function Header() {
  const pathname = usePathname()
  const isModsPage = pathname === '/mod/explore'
  const isUsersPage = pathname === '/user/explore'
  const [showMenu, setShowMenu] = useState(false)
  const [isNarrow, setIsNarrow] = useState(false)
  const [searchCollapsed, setSearchCollapsed] = useState(false)
  const { handleSearch } = useSearchContext()
  const { isSplitScreen, toggleSplitScreen, orientation, setOrientation } = useSplitScreenContext()
  const router = useRouter()
  const [inputValue, setInputValue] = useState('')
  
  const modsColor = '#3b82f6'
  const usersColor = '#10b981'

  useEffect(() => {
    const checkWidth = () => {
      const width = window.innerWidth
      setIsNarrow(width < 768)
      setSearchCollapsed(width < 1200)
    }
    checkWidth()
    window.addEventListener('resize', checkWidth)
    return () => window.removeEventListener('resize', checkWidth)
  }, [])

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

  const handleLeftSplit = () => {
    setOrientation('vertical')
    if (!isSplitScreen) {
      toggleSplitScreen()
    }
  }

  const handleTopSplit = () => {
    setOrientation('horizontal')
    if (!isSplitScreen) {
      toggleSplitScreen()
    }
  }

  return (
    <header className="sticky top-0 z-50 w-full bg-black" style={{ borderColor: '#00ff0040' }}>
      <div className="flex items-center justify-between px-4 py-2">
        <div className="flex items-center gap-4">
          <div className="relative flex items-center">
            <div className="relative">
              {searchCollapsed ? (
                <button
                  onClick={() => setSearchCollapsed(false)}
                  className="p-3 rounded-lg border-2 transition-all active:scale-95"
                  style={{
                    height: '64px',
                    width: '64px',
                    backgroundColor: 'rgba(0, 0, 0, 0.2)',
                    borderColor: 'rgba(239, 220, 11, 0.6)'
                  }}
                  title="Search"
                >
                  <MagnifyingGlassIcon className="w-8 h-8" style={{ color: '#d8cc1bff' }} />
                </button>
              ) : (
                <div className="relative">
                  <MagnifyingGlassIcon className="absolute left-4 top-1/2 transform -translate-y-1/2 w-7 h-7" style={{ color: '#d3d30bff' }} />
                  <input
                    type="text"
                    value={inputValue}
                    onChange={handleInputChange}
                    onKeyDown={handleKeyDown}
                    onBlur={() => !inputValue && setSearchCollapsed(window.innerWidth < 1200)}
                    placeholder="Search mods..."
                    className="border-2 text-white pl-14 pr-5 py-3.5 rounded-lg text-xl hover:shadow-lg focus:outline-none focus:ring-2 transition-all w-80"
                    style={{
                      backgroundColor: 'rgba(15, 15, 15, 0.2)',
                      borderColor: 'rgba(239, 220, 11, 0.6)',
                      fontFamily: "'Courier New', 'Consolas', 'Monaco', monospace"
                    }}
                    autoFocus={!searchCollapsed}
                  />
                </div>
              )}
            </div>
          </div>
        </div>
        
        <div className="flex items-center justify-end gap-3">
          <UserHeader />
        </div>
      </div>
    </header>
  )
}
