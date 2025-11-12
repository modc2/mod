'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { KeyIcon, UsersIcon, ChevronLeftIcon, ChevronRightIcon, MagnifyingGlassIcon, FunnelIcon, CubeIcon } from '@heroicons/react/24/outline'
import { motion, AnimatePresence } from 'framer-motion'
import { BackendSettings } from './BackendSettings'
import { useState, FormEvent } from 'react'
import { useSearchContext } from '@/app/block/context/SearchContext'
import { useRouter } from 'next/navigation'
import { useSidebarContext } from '@/app/block/context/SidebarContext'

const navigation = [
  { name: 'Mods', href: '/mod/explore', icon: CubeIcon },
  { name: 'Users', href: '/user/explore', icon: UsersIcon },
]

export function Sidebar() {
  const pathname = usePathname()
  const { handleSearch } = useSearchContext()
  const router = useRouter()
  const { isSidebarExpanded, toggleSidebar } = useSidebarContext()
  const [inputValue, setInputValue] = useState('')
  const [isSearchExpanded, setIsSearchExpanded] = useState(false)

  const onSubmit = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    const trimmed = inputValue.trim()
    handleSearch(trimmed)
    router.push('/mod/explore')
  }

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

  const handleSearchIconClick = () => {
    setIsSearchExpanded(!isSearchExpanded)
    if (!isSearchExpanded) {
      setTimeout(() => {
        const input = document.querySelector('input[type="text"]') as HTMLInputElement
        if (input) input.focus()
      }, 100)
    }
  }

  const effectiveExpanded = isSidebarExpanded || isSearchExpanded
  const sidebarWidth = isSearchExpanded ? 320 : (isSidebarExpanded ? 256 : 64)

  return (
    <motion.div
      initial={false}
      animate={{ width: sidebarWidth }}
      transition={{ duration: 0.3, ease: 'easeInOut' }}
      className="fixed left-0 top-20 h-[calc(100vh-5rem)] border-r border-white/10 bg-black z-40"
    >
      <div className="flex h-full flex-col">

        <div className="px-3 py-4 border-b border-white/10">
          <div className="relative flex items-center">
            <button
              onClick={handleSearchIconClick}
              className="shrink-0 p-2 rounded-md text-gray-400 hover:text-white hover:bg-white/5 transition-all"
              title="Search"
            >
              <MagnifyingGlassIcon className="w-6 h-6" />
            </button>
            <AnimatePresence>
              {isSearchExpanded && (
                <motion.input
                  initial={{ opacity: 0, width: 0 }}
                  animate={{ opacity: 1, width: 'calc(100% - 40px)' }}
                  exit={{ opacity: 0, width: 0 }}
                  transition={{ duration: 0.3 }}
                  type="text"
                  value={inputValue}
                  onChange={handleInputChange}
                  onKeyDown={handleKeyDown}
                  placeholder="Search mods..."
                  className="ml-2 bg-white/5 border border-white/10 text-white px-3 py-2 rounded-lg text-base hover:bg-white/10 focus:outline-none focus:ring-2 focus:ring-green-500/50 focus:border-green-500/50 transition-all"
                />
              )}
            </AnimatePresence>
          </div>
        </div>

        <nav className="flex-1 px-3 py-4 overflow-y-auto space-y-1">
          {navigation.map((item) => {
            const isActive = pathname === item.href
            return (
              <Link
                key={item.name}
                href={item.href}
                className={`group relative flex items-center gap-x-3 rounded-md p-2 text-base font-semibold transition-all ${
                  isActive
                    ? 'bg-green-500/10 text-green-400'
                    : 'text-gray-400 hover:text-white hover:bg-white/5'
                }`}
                title={!effectiveExpanded ? item.name : undefined}
              >
                <item.icon
                  className={`shrink-0 w-6 h-6 ${
                    isActive ? 'text-green-400' : 'text-gray-400 group-hover:text-white'
                  }`}
                  aria-hidden="true"
                />
                <AnimatePresence>
                  {effectiveExpanded && (
                    <motion.span
                      initial={{ opacity: 0, width: 0 }}
                      animate={{ opacity: 1, width: 'auto' }}
                      exit={{ opacity: 0, width: 0 }}
                      transition={{ duration: 0.2 }}
                      className="overflow-hidden whitespace-nowrap"
                    >
                      {item.name}
                    </motion.span>
                  )}
                </AnimatePresence>
                {isActive && (
                  <motion.div
                    layoutId="activeNav"
                    className="absolute inset-0 bg-green-500/10 rounded-md -z-10"
                    initial={false}
                    transition={{ type: 'spring', stiffness: 380, damping: 30 }}
                  />
                )}
              </Link>
            )
          })}
        </nav>

        <div className="border-t border-white/10 p-3">
          <AnimatePresence mode="wait">
            {effectiveExpanded ? (
              <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.2 }}
              >
                <BackendSettings />
              </motion.div>
            ) : (
              <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.2 }}
                className="flex justify-center"
              >
                <BackendSettings />
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </div>
    </motion.div>
  )
}
