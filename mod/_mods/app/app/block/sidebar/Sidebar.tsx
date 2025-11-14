'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { KeyIcon, UsersIcon, ChevronLeftIcon, ChevronRightIcon, FunnelIcon, CubeIcon, PlusIcon, ChatBubbleLeftRightIcon } from '@heroicons/react/24/outline'
import { motion, AnimatePresence } from 'framer-motion'
import { BackendSettings } from './BackendSettings'
import { useState, useRef, useEffect } from 'react'
import { useSidebarContext } from '@/app/block/context/SidebarContext'

const navigation = [
  { name: 'Mods', href: '/mod/explore', icon: CubeIcon },
  { name: 'Users', href: '/user/explore', icon: UsersIcon },
  { name: 'Create', href: '/create', icon: PlusIcon },
  { name: 'Chat', href: '/chat', icon: ChatBubbleLeftRightIcon },
]

const MIN_WIDTH = 80
const MAX_WIDTH = 400
const DEFAULT_WIDTH = 280

export function Sidebar() {
  const pathname = usePathname()
  const { isSidebarExpanded, toggleSidebar } = useSidebarContext()
  const [sidebarWidth, setSidebarWidth] = useState(() => {
    if (typeof window !== 'undefined') {
      return parseInt(localStorage.getItem('sidebar_width') || String(DEFAULT_WIDTH))
    }
    return DEFAULT_WIDTH
  })
  const [isResizing, setIsResizing] = useState(false)
  const sidebarRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (typeof window !== 'undefined') {
      localStorage.setItem('sidebar_width', String(sidebarWidth))
    }
  }, [sidebarWidth])

  const handleMouseDown = (e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setIsResizing(true)
  }

  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      if (!isResizing) return
      
      const newWidth = e.clientX
      if (newWidth >= MIN_WIDTH && newWidth <= MAX_WIDTH) {
        setSidebarWidth(newWidth)
      }
    }

    const handleMouseUp = () => {
      setIsResizing(false)
    }

    if (isResizing) {
      document.addEventListener('mousemove', handleMouseMove)
      document.addEventListener('mouseup', handleMouseUp)
      document.body.style.cursor = 'ew-resize'
      document.body.style.userSelect = 'none'
    }

    return () => {
      document.removeEventListener('mousemove', handleMouseMove)
      document.removeEventListener('mouseup', handleMouseUp)
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
    }
  }, [isResizing])

  const displayWidth = isSidebarExpanded ? sidebarWidth : MIN_WIDTH

  return (
    <>
      <motion.div
        ref={sidebarRef}
        initial={false}
        animate={{ width: displayWidth }}
        transition={{ duration: 0.3, ease: 'easeInOut' }}
        className="fixed left-0 top-20 h-[calc(100vh-5rem)] border-r border-white/10 bg-gradient-to-b from-black via-gray-950 to-black z-40 hover:border-green-500/50 transition-all shadow-2xl shadow-green-500/10"
        style={{ width: displayWidth }}
      >
        <div className="flex h-full flex-col relative">
          <div className="absolute top-2 right-2 z-50">
            <button
              onClick={(e) => {
                e.stopPropagation()
                toggleSidebar()
              }}
              className="p-2 rounded-lg hover:bg-green-500/20 text-gray-400 hover:text-green-400 transition-all duration-200 hover:scale-110 active:scale-95 border border-transparent hover:border-green-500/30"
              title={isSidebarExpanded ? 'Collapse sidebar' : 'Expand sidebar'}
            >
              {isSidebarExpanded ? (
                <ChevronLeftIcon className="h-5 w-5" />
              ) : (
                <ChevronRightIcon className="h-5 w-5" />
              )}
            </button>
          </div>

          <nav className="flex-1 px-3 py-4 pt-16 overflow-y-auto space-y-2">
            {navigation.map((item) => {
              const isActive = pathname === item.href
              return (
                <Link
                  key={item.name}
                  href={item.href}
                  className={`group relative flex items-center rounded-lg p-3 text-base font-semibold transition-all duration-200 ${
                    isActive
                      ? 'bg-green-500/20 text-green-400 shadow-lg shadow-green-500/20 border border-green-500/30'
                      : 'text-gray-400 hover:text-white hover:bg-white/10 border border-transparent hover:border-white/20'
                  } ${isSidebarExpanded ? 'gap-x-3' : 'justify-center'}`}
                  title={!isSidebarExpanded ? item.name : undefined}
                  onClick={(e) => e.stopPropagation()}
                >
                  <item.icon
                    className="shrink-0 transition-transform duration-200 group-hover:scale-110"
                    style={{
                      color: isActive ? '#4ade80' : '#9ca3af',
                      width: '1.75rem',
                      height: '1.75rem',
                      minWidth: '1.75rem',
                      minHeight: '1.75rem'
                    }}
                    aria-hidden="true"
                  />
                  <AnimatePresence>
                    {isSidebarExpanded && (
                      <motion.span
                        initial={{ opacity: 0, x: -10 }}
                        animate={{ opacity: 1, x: 0 }}
                        exit={{ opacity: 0, x: -10 }}
                        transition={{ duration: 0.2, ease: 'easeOut' }}
                        className="whitespace-nowrap text-base font-semibold"
                      >
                        {item.name}
                      </motion.span>
                    )}
                  </AnimatePresence>
                  {isActive && (
                    <motion.div
                      layoutId="activeNav"
                      className="absolute inset-0 bg-green-500/10 rounded-lg -z-10"
                      initial={false}
                      transition={{ type: 'spring', stiffness: 380, damping: 30 }}
                    />
                  )}
                </Link>
              )
            })}
          </nav>

          <div className="border-t border-white/10 p-3 bg-black/50">
            <AnimatePresence mode="wait">
              {isSidebarExpanded ? (
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

        {isSidebarExpanded && (
          <div
            className="resize-handle absolute right-0 top-0 bottom-0 w-1 cursor-ew-resize hover:bg-green-500/50 transition-colors z-50 active:bg-green-500"
            onMouseDown={handleMouseDown}
            onClick={(e) => e.stopPropagation()}
            style={{
              backgroundColor: isResizing ? 'rgba(34, 197, 94, 0.8)' : 'transparent',
            }}
          />
        )}
      </motion.div>

      <style jsx global>{`
        :root {
          --sidebar-width: ${displayWidth}px;
        }
      `}</style>
    </>
  )
}
