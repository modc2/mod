'use client'

import { LogoHeader } from './LogoHeader'
import { UserHeader } from './UserHeader'
import { NodeUrlSettings } from './NodeUrlSettings'
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
  
  const modsColor = '#3b82f6'
  const usersColor = '#10b981'

  useEffect(() => {
    const checkWidth = () => {
      setIsNarrow(window.innerWidth < 768)
    }
    checkWidth()
    window.addEventListener('resize', checkWidth)
    return () => window.removeEventListener('resize', checkWidth)
  }, [])

  return (
    <header className="sticky top-0 z-50 w-full  bg-black" style={{ borderColor: '#00ff0040' }}>
      <div className="flex items-center   justify-between">
        <div className=" items-center">
          <LogoHeader />
        </div>
        
        <div className="flex items-center  justify-end">
          {isNarrow && (
            <div className="relative">
              <button 
                onMouseEnter={() => setShowMenu(true)} 
                className="p-4 rounded-lg border-2 border-white/40 bg-white/15 hover:bg-white/20 transition-all active:scale-95" 
                style={{height: '56px'}}
                title="Menu"
              >
                <Bars3Icon className="h-6 w-6 text-white" />
              </button>
              {showMenu && (
                <div 
                  className="absolute top-full right-0 mt-2 border-2 border-white/20 bg-black/95 backdrop-blur-md rounded-lg shadow-xl min-w-[200px]"
                  onMouseEnter={() => setShowMenu(true)}
                  onMouseLeave={() => setShowMenu(false)}
                >
                  <nav className="flex flex-col p-2">
                    <Link
                      href="/mod/explore"
                      className={`flex items-center gap-2 px-5 py-3 rounded-lg border-2 transition-all duration-200 mb-2 backdrop-blur-md ${
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
                      <CubeIcon className="h-6 w-6" />
                      <span className="font-bold text-base">MODS</span>
                    </Link>
                    
                    <Link
                      href="/user/explore"
                      className={`flex items-center gap-2 px-5 py-3 rounded-lg border-2 transition-all duration-200 backdrop-blur-md ${
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
                      <UsersIcon className="h-6 w-6" />
                      <span className="font-bold text-base">USERS</span>
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
