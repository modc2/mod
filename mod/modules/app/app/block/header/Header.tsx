'use client'

import { LogoHeader } from './LogoHeader'
import { SearchHeader } from './SearchHeader'
import { UserHeader } from './UserHeader'
import { usePathname } from 'next/navigation'
import Link from 'next/link'
import { CubeIcon, UsersIcon } from '@heroicons/react/24/outline'

const text2color = (text: string): string => {
  if (!text) return '#00ff00'
  let hash = 0
  for (let i = 0; i < text.length; i++) hash = text.charCodeAt(i) + ((hash << 5) - hash)
  const golden_ratio = 0.618033988749895
  const hue = (hash * golden_ratio * 360) % 360
  const saturation = 65 + (Math.abs(hash >> 8) % 35)
  const lightness = 50 + (Math.abs(hash >> 16) % 20)
  return `hsl(${hue}, ${saturation}%, ${lightness}%)`
}

export function Header() {
  const pathname = usePathname()
  const isModsPage = pathname === '/mods'
  const isUsersPage = pathname === '/users'
  
  const modsColor = text2color('mods')
  const usersColor = text2color('users')

  return (
    <header className="sticky top-0 z-50 w-full border-b border-white/10 bg-black/95 backdrop-blur-sm">
      <div className="flex h-16 items-center justify-between px-4 md:px-6">
        <div className="flex items-center gap-6">
          <LogoHeader />
          
          <nav className="flex items-center gap-2">
            <Link
              href="/mods"
              className={`flex items-center gap-2 px-4 py-2 rounded-lg border transition-all duration-200 ${
                isModsPage ? 'shadow-lg' : 'hover:shadow-md'
              }`}
              style={{
                backgroundColor: isModsPage ? modsColor : 'transparent',
                borderColor: modsColor,
                color: isModsPage ? '#000000' : modsColor,
              }}
            >
              <CubeIcon className="h-5 w-5" />
              <span className="font-bold text-sm">MODS</span>
            </Link>
            
            <Link
              href="/users"
              className={`flex items-center gap-2 px-4 py-2 rounded-lg border transition-all duration-200 ${
                isUsersPage ? 'shadow-lg' : 'hover:shadow-md'
              }`}
              style={{
                backgroundColor: isUsersPage ? usersColor : 'transparent',
                borderColor: usersColor,
                color: isUsersPage ? '#000000' : usersColor,
              }}
            >
              <UsersIcon className="h-5 w-5" />
              <span className="font-bold text-sm">USERS</span>
            </Link>
          </nav>
        </div>
        
        <div className="flex items-center gap-4">
          <SearchHeader />
          <UserHeader />
        </div>
      </div>
    </header>
  )
}
