'use client'

import { LogoHeader } from './LogoHeader'
import { SearchHeader } from './SearchHeader'
import { UserHeader } from './UserHeader'
import { usePathname } from 'next/navigation'
import Link from 'next/link'
import { CubeIcon, UsersIcon, Bars3Icon } from '@heroicons/react/24/outline'
import { text2color } from '@/app/utils'
import { useState, useEffect } from 'react'

export function Header() {
  const pathname = usePathname()
  const isModsPage = pathname === '/mod/explore'
  const isUsersPage = pathname === '/user/explore'
  const [showMenu, setShowMenu] = useState(false)
  const [isNarrow, setIsNarrow] = useState(false)
  
  const modsColor = text2color('mods')
  const usersColor = text2color('users')

  useEffect(() => {
    const checkWidth = () => {
      setIsNarrow(window.innerWidth < 768)
    }
    checkWidth()
    window.addEventListener('resize', checkWidth)
    return () => window.removeEventListener('resize', checkWidth)
  }, [])

  return (
    <header className="sticky top-0 z-50 w-full border-b border-white/10 bg-black/95 backdrop-blur-sm">
      <div className="flex h-20 items-center justify-between px-4 md:px-6">
        <div className="flex items-center gap-6">
          <LogoHeader />
          
          {!isNarrow && (
            <nav className="flex items-center gap-2">
              <Link
                href="/mod/explore"
                className={`flex items-center gap-2 px-5 py-3 rounded-lg border transition-all duration-200 ${
                  isModsPage ? 'shadow-lg' : 'hover:shadow-md'
                }`}
                style={{
                  backgroundColor: isModsPage ? modsColor : 'transparent',
                  borderColor: modsColor,
                  color: isModsPage ? '#000000' : modsColor,
                }}
              >
                <CubeIcon className="h-6 w-6" />
                <span className="font-bold text-lg">MODS</span>
              </Link>
              
              <Link
                href="/user/explore"
                className={`flex items-center gap-2 px-5 py-3 rounded-lg border transition-all duration-200 ${
                  isUsersPage ? 'shadow-lg' : 'hover:shadow-md'
                }`}
                style={{
                  backgroundColor: isUsersPage ? usersColor : 'transparent',
                  borderColor: usersColor,
                  color: isUsersPage ? '#000000' : usersColor,
                }}
              >
                <UsersIcon className="h-6 w-6" />
                <span className="font-bold text-lg">USERS</span>
              </Link>
            </nav>
          )}

          {isNarrow && (
            <div className="relative">
              <button 
                onMouseEnter={() => setShowMenu(true)} 
                className="p-4 rounded-lg border border-white/20 hover:bg-white/10 transition-all" 
                style={{height: '56px'}}
              >
                <Bars3Icon className="h-6 w-6 text-white" />
              </button>
              {showMenu && (
                <div 
                  className="absolute top-full left-0 mt-2 border border-white/10 bg-black/95 backdrop-blur-sm rounded-lg shadow-xl min-w-[200px]"
                  onMouseEnter={() => setShowMenu(true)}
                  onMouseLeave={() => setShowMenu(false)}
                >
                  <nav className="flex flex-col p-2">
                    <Link
                      href="/mod/explore"
                      className={`flex items-center gap-2 px-5 py-3 rounded-lg border transition-all duration-200 mb-2 ${
                        isModsPage ? 'shadow-lg' : 'hover:shadow-md'
                      }`}
                      style={{
                        backgroundColor: isModsPage ? modsColor : 'transparent',
                        borderColor: modsColor,
                        color: isModsPage ? '#000000' : modsColor,
                      }}
                    >
                      <CubeIcon className="h-6 w-6" />
                      <span className="font-bold text-lg">MODS</span>
                    </Link>
                    
                    <Link
                      href="/user/explore"
                      className={`flex items-center gap-2 px-5 py-3 rounded-lg border transition-all duration-200 ${
                        isUsersPage ? 'shadow-lg' : 'hover:shadow-md'
                      }`}
                      style={{
                        backgroundColor: isUsersPage ? usersColor : 'transparent',
                        borderColor: usersColor,
                        color: isUsersPage ? '#000000' : usersColor,
                      }}
                    >
                      <UsersIcon className="h-6 w-6" />
                      <span className="font-bold text-lg">USERS</span>
                    </Link>
                  </nav>
                </div>
              )}
            </div>
          )}
        </div>
        
        <div className="flex items-center gap-4">
          <SearchHeader />
          <UserHeader />
        </div>
      </div>
    </header>
  )
}
