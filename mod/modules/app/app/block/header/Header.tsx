'use client'

import { LogoHeader } from './LogoHeader'
import { SearchHeader } from './SearchHeader'
import { UserHeader } from './UserHeader'
import { useSidebarContext } from '@/app/block/context/SidebarContext'
import { Bars3Icon } from '@heroicons/react/24/outline'

export function Header() {
  const { toggleSidebar } = useSidebarContext()

  return (
    <header className="sticky top-0 z-50 border-b border-white/10 bg-black/95 backdrop-blur-xl">
      <div className="mx-auto max-w-7xl px-4 sm:px-6">
        <div className="flex h-14 items-center justify-between gap-4">
          {/* Left: Logo + Sidebar Toggle */}
          <div className="flex items-center gap-3">
            <button
              onClick={toggleSidebar}
              className="rounded-lg p-2 text-white/70 hover:bg-white/5 hover:text-white transition-all"
              aria-label="Toggle sidebar"
            >
              <Bars3Icon className="h-5 w-5" />
            </button>
            <LogoHeader />
          </div>

          {/* Center: Compact Search */}
          <div className="flex-1 max-w-md">
            <SearchHeader />
          </div>

          {/* Right: User */}
          <div className="flex items-center">
            <UserHeader />
          </div>
        </div>
      </div>
    </header>
  )
}