'use client'

import { UserHeader } from '@/block/header/UserHeader'
import { usePathname } from 'next/navigation'
import Link from 'next/link'
import { CubeIcon, UsersIcon, Bars3Icon, MagnifyingGlassIcon, PlusCircleIcon } from '@heroicons/react/24/outline'
import { useState, useEffect } from 'react'
import { useSearchContext } from '@/block/context/SearchContext'
import { useRouter } from 'next/navigation'

export function LoginHeader() {
  const pathname = usePathname()
  const isModsPage = pathname === '/mod/explore'
  const isUsersPage = pathname === '/user/explore'
  const [showMenu, setShowMenu] = useState(false)
  const [isNarrow, setIsNarrow] = useState(false)
  const [searchCollapsed, setSearchCollapsed] = useState(false)
  const { handleSearch } = useSearchContext()
  const router = useRouter()
  const [inputValue, setInputValue] = useState('')
  
  const modsColor = '#3b82f6'
  const usersColor = '#10b981'
  const createColor = '#a855f7'

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

  return (
    <header className="sticky top-0 z-50 w-full bg-black" style={{ borderColor: '#00ff0040' }}>
      <div className="flex items-center justify-between px-4 py-2">
        <div className="flex items-center gap-4">
          <div className="relative flex items-center gap-3">
            <div className="relative">
              {searchCollapsed ? (
                <button
                  onClick={() => setSearchCollapsed(false)}
                  className="p-3 rounded-lg bg-white/5 border border-white/10 hover:bg-white/10 transition-all active:scale-95"
                  style={{height: '48px', width: '48px'}}
                  title="Search"
                >
                  <MagnifyingGlassIcon className="w-6 h-6 text-gray-400" />
                </button>
              ) : (
                <div className="relative">
                  <MagnifyingGlassIcon className="absolute left-3 top-1/2 transform -translate-y-1/2 w-5 h-5 text-gray-400" />
                  <input
                    type="text"
                    value={inputValue}
                    onChange={handleInputChange}
                    onKeyDown={handleKeyDown}
                    onBlur={() => !inputValue && setSearchCollapsed(window.innerWidth < 1200)}
                    placeholder="Search mods..."
                    className="bg-white/5 border border-white/10 text-white pl-10 pr-4 py-2.5 rounded-lg text-base hover:bg-white/10 focus:outline-none focus:ring-2 focus:ring-green-500/50 focus:border-green-500/50 transition-all w-72"
                    autoFocus={!searchCollapsed}
                  />
                </div>
              )}
            </div>
            
            <Link
              href="/create"
              className="flex items-center gap-2 px-4 py-2.5 rounded-lg border transition-all duration-200 backdrop-blur-md hover:shadow-lg active:scale-95"
              style={{
                backgroundColor: `${createColor}25`,
                borderColor: `${createColor}80`,
                color: createColor,
                boxShadow: `0 0 10px ${createColor}20`,
              }}
              title="Create Module"
            >
              <PlusCircleIcon className="h-5 w-5" />
              <span className="font-bold text-sm">CREATE</span>
            </Link>
          </div>
        </div>
        
        <div className="flex items-center justify-end gap-3">
          {isNarrow && (
            <div className="relative">
              <button 
                onMouseEnter={() => setShowMenu(true)} 
                className="p-3 rounded-lg bg-white/5 border border-white/10 hover:bg-white/10 transition-all active:scale-95" 
                style={{height: '48px'}}
                title="Menu"
              >
                <Bars3Icon className="h-6 w-6 text-white" />
              </button>
              {showMenu && (
                <div 
                  className="absolute top-full right-0 mt-2 border border-white/10 bg-black/95 backdrop-blur-md rounded-lg shadow-xl min-w-[200px]"
                  onMouseEnter={() => setShowMenu(true)}
                  onMouseLeave={() => setShowMenu(false)}
                >
                  <nav className="flex flex-col p-2">
                    <Link
                      href="/mod/explore"
                      className={`flex items-center gap-2 px-4 py-2.5 rounded-lg border transition-all duration-200 mb-2 backdrop-blur-md ${
                        isModsPage ? 'shadow-xl active:scale-95' : 'hover:shadow-lg active:scale-95'
                      }`}
                      style={{
                        backgroundColor: isModsPage ? `${modsColor}50` : `${modsColor}25`,
                        borderColor: `${modsColor}80`,
                        color: modsColor,
                        boxShadow: isModsPage ? `0 0 20px ${modsColor}50, inset 0 2px 8px ${modsColor}30` : `0 0 10px ${modsColor}20`,
                      }}
                      title="Modules"
                    >
                      <CubeIcon className="h-5 w-5" />
                      <span className="font-bold text-sm">MODS</span>
                    </Link>
                    
                    <Link
                      href="/user/explore"
                      className={`flex items-center gap-2 px-4 py-2.5 rounded-lg border transition-all duration-200 backdrop-blur-md ${
                        isUsersPage ? 'shadow-xl active:scale-95' : 'hover:shadow-lg active:scale-95'
                      }`}
                      style={{
                        backgroundColor: isUsersPage ? `${usersColor}50` : `${usersColor}25`,
                        borderColor: `${usersColor}80`,
                        color: usersColor,
                        boxShadow: isUsersPage ? `0 0 20px ${usersColor}50, inset 0 2px 8px ${usersColor}30` : `0 0 10px ${usersColor}20`,
                      }}
                      title="Users"
                    >
                      <UsersIcon className="h-5 w-5" />
                      <span className="font-bold text-sm">USERS</span>
                    </Link>
                  </nav>
                </div>
              )}
            </div>
          )}
          
          <UserHeader />
        </div>
      </div>
    </header>
  )
}
