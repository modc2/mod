'use client'

import Link from 'next/link'
import { CubeIcon } from '@heroicons/react/24/outline'
import { useSidebarContext } from '@/app/block/context/SidebarContext'

export function LogoHeader() {
  const { toggleSidebar } = useSidebarContext()

  return (
    <button
      onClick={toggleSidebar}
      className="flex items-center gap-3 group transition-all hover:scale-105 active:scale-95"
    >
      <div className="p-2 rounded-xl bg-gradient-to-br from-purple-500/20 to-pink-500/20 border-2 group-hover:border-purple-400/60 transition-all group-hover:shadow-lg group-hover:shadow-purple-500/30">
        <CubeIcon className="h-8 w-8 text-purple-400 transition-colors" strokeWidth={2.5} />
      </div>
    </button>
  )
}