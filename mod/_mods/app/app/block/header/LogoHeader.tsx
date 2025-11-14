'use client'

import Link from 'next/link'
import { CubeIcon } from '@heroicons/react/24/outline'
import { useSidebarContext } from '@/app/block/context/SidebarContext'
import { useRouter } from 'next/navigation'

export function LogoHeader() {
  const { toggleSidebar } = useSidebarContext()
  const router = useRouter()

  const handleClick = () => {
    router.push('/mod/explore')
  }

  return (
    <button
      onClick={handleClick}
      className="flex items-center gap-3 group transition-all hover:scale-105 active:scale-95"
    >
      <div className="p-2.5 rounded-xl bg-gradient-to-br from-purple-500/20 to-pink-500/20 border-2 group-hover:border-purple-400/60 transition-all group-hover:shadow-lg group-hover:shadow-purple-500/30">
        <CubeIcon className="h-10 w-10 text-purple-400 transition-colors" strokeWidth={2.5} />
      </div>
    </button>
  )
}