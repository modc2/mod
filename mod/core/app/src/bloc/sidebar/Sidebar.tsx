'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import {  UsersIcon, CubeIcon,  HomeIcon, Cog6ToothIcon, TableCellsIcon } from '@heroicons/react/24/outline'
import { motion, AnimatePresence } from 'framer-motion'
import { useState, useRef, useEffect } from 'react'
import { useSidebarContext } from '@/bloc/context/SidebarContext'
import { useSplitScreenContext } from '@/bloc/context/SplitScreenContext'
import { Squares2X2Icon } from '@heroicons/react/24/outline'

const navigation = [
  { name: 'Home', href: '/', icon: HomeIcon },
  { name: 'Mods', href: '/mod/explore', icon: CubeIcon },
  { name: 'Users', href: '/user/explore', icon: UsersIcon },
  { name: 'Transactions', href: '/host', icon: TableCellsIcon },
]

const FIXED_WIDTH = 80

export function Sidebar() {
  const pathname = usePathname()
  const { isSidebarExpanded, toggleSidebar } = useSidebarContext()
  const [hoveredItem, setHoveredItem] = useState<string | null>(null)
  const [showServerSettings, setShowServerSettings] = useState(false)
  const { orientation, setOrientation } = useSplitScreenContext()

  return (
    <>
      <div
        className="fixed"
        style={{ width: FIXED_WIDTH, zIndex: 40 }}
      >
        <div className="flex h-full flex-col relative">
          <nav className="flex-1 px-3 py-4 pt-4 overflow-y-auto space-y-2">
            {navigation.map((item) => {
              const isActive = pathname === item.href
              return (
                <div
                  key={item.name}
                  className="relative"
                  onMouseEnter={() => setHoveredItem(item.name)}
                  onMouseLeave={() => setHoveredItem(null)}
                >
                  <Link
                    href={item.href}
                    className={`group relative flex items-center justify-center rounded-lg p-3 text-base font-semibold transition-all duration-200 ${
                      isActive
                        ? 'bg-green-500/20 text-green-400 shadow-lg shadow-green-500/20 border border-green-500/30'
                        : 'text-gray-400 hover:text-white hover:bg-white/10 border border-transparent hover:border-white/20'
                    }`}
                  >
                    <item.icon
                      className="shrink-0 transition-transform duration-200 group-hover:scale-110"
                      style={{
                        color: isActive ? '#4ade80' : '#9ca3af',
                        width: '2.5rem',
                        height: '2.5rem',
                        minWidth: '2.5rem',
                        minHeight: '2.5rem'
                      }}
                      aria-hidden="true"
                    />
                    {isActive && (
                      <motion.div
                        layoutId="activeNav"
                        className="absolute inset-0 bg-green-500/10 rounded-lg -z-10"
                        initial={false}
                        transition={{ type: 'spring', stiffness: 380, damping: 30 }}
                      />
                    )}
                  </Link>
                  <AnimatePresence>
                    {hoveredItem === item.name && (
                      <motion.div
                        initial={{ opacity: 0, x: -10 }}
                        animate={{ opacity: 1, x: 0 }}
                        exit={{ opacity: 0, x: -10 }}
                        transition={{ duration: 0.15 }}
                        className="fixed pointer-events-none"
                        style={{ 
                          zIndex: 99999,
                          left: `${FIXED_WIDTH + 8}px`,
                          top: 'auto'
                        }}
                      >
                        <div className="bg-gray-900 text-white px-3 py-2 rounded-lg shadow-xl border border-green-500/30 whitespace-nowrap text-sm font-medium">
                          {item.name}
                          <div className="absolute right-full top-1/2 -translate-y-1/2 border-8 border-transparent border-r-gray-900" />
                        </div>
                      </motion.div>
                    )}
                  </AnimatePresence>
                </div>
              )
            })}
          </nav>

          <div className="border-t border-white/10 p-3 bg-black/50 relative space-y-2">
            <div
              onMouseEnter={() => setHoveredItem('split')}
              onMouseLeave={() => setHoveredItem(null)}
            >
              <button
                onClick={() => setOrientation(orientation === 'vertical' ? 'horizontal' : 'vertical')}
                className="w-full p-2 rounded-md hover:bg-white/5 text-gray-400 hover:text-white transition-colors flex items-center justify-center"
              >
                <Squares2X2Icon className={`h-6 w-6 ${orientation === 'horizontal' ? 'rotate-90' : ''}`} />
              </button>
              <AnimatePresence>
                {hoveredItem === 'split' && (
                  <motion.div
                    initial={{ opacity: 0, x: -10 }}
                    animate={{ opacity: 1, x: 0 }}
                    exit={{ opacity: 0, x: -10 }}
                    transition={{ duration: 0.15 }}
                    className="fixed pointer-events-none"
                    style={{ 
                      zIndex: 99999,
                      left: `${FIXED_WIDTH + 8}px`,
                      bottom: '60px'
                    }}
                  >
                    <div className="bg-gray-900 text-white px-3 py-2 rounded-lg shadow-xl border border-green-500/30 whitespace-nowrap text-sm font-medium">
                      {orientation === 'vertical' ? 'Horizontal Split' : 'Vertical Split'}
                      <div className="absolute right-full top-1/2 -translate-y-1/2 border-8 border-transparent border-r-gray-900" />
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
            </div>
            <div
              onMouseEnter={() => setHoveredItem('settings')}
              onMouseLeave={() => setHoveredItem(null)}
            >
              <button
                onClick={() => setShowServerSettings(!showServerSettings)}
                className="w-full p-2 rounded-md hover:bg-white/5 text-gray-400 hover:text-white transition-colors flex items-center justify-center"
              >
                <Cog6ToothIcon className="h-6 w-6" />
              </button>
              <AnimatePresence>
                {hoveredItem === 'settings' && (
                  <motion.div
                    initial={{ opacity: 0, x: -10 }}
                    animate={{ opacity: 1, x: 0 }}
                    exit={{ opacity: 0, x: -10 }}
                    transition={{ duration: 0.15 }}
                    className="fixed pointer-events-none"
                    style={{ 
                      zIndex: 99999,
                      left: `${FIXED_WIDTH + 8}px`,
                      bottom: '12px'
                    }}
                  >
                    <div className="bg-gray-900 text-white px-3 py-2 rounded-lg shadow-xl border border-green-500/30 whitespace-nowrap text-sm font-medium">
                      API Settings
                      <div className="absolute right-full top-1/2 -translate-y-1/2 border-8 border-transparent border-r-gray-900" />
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
            </div>
            <AnimatePresence>
              {showServerSettings && (
                <motion.div
                  initial={{ opacity: 0, x: -20, width: 0 }}
                  animate={{ opacity: 1, x: 0, width: 320 }}
                  exit={{ opacity: 0, x: -20, width: 0 }}
                  transition={{ duration: 0.3, ease: 'easeInOut' }}
                  className="fixed bottom-0 bg-gray-900 border border-green-500/30 rounded-lg shadow-2xl overflow-hidden"
                  style={{ 
                    zIndex: 99999,
                    left: `${FIXED_WIDTH}px`
                  }}
                >
                  <ServerSettingsPanel onClose={() => setShowServerSettings(false)} />
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        </div>
      </div>

      <style jsx global>{`
        :root {
          --sidebar-width: ${FIXED_WIDTH}px;
        }
      `}</style>
    </>
  )
}

function ServerSettingsPanel({ onClose }: { onClose: () => void }) {
  const [inputUrl, setInputUrl] = useState<string>('')
  const DEFAULT_API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'

  useEffect(() => {
    const saved = localStorage.getItem('custom_node_url')
    if (saved) {
      setInputUrl(saved)
    } else {
      setInputUrl(DEFAULT_API_URL)
    }
  }, [])

  const handleSave = () => {
    if (inputUrl.trim()) {
      localStorage.setItem('custom_node_url', inputUrl.trim())
      window.location.reload()
    }
  }

  const handleReset = () => {
    localStorage.removeItem('custom_node_url')
    setInputUrl(DEFAULT_API_URL)
    window.location.reload()
  }

  return (
    <div className="p-4 space-y-3 w-80">
      <div className="flex items-center justify-between">
        <h3 className="text-white font-semibold text-base">Server Settings</h3>
        <button
          onClick={onClose}
          className="text-gray-400 hover:text-white transition-colors"
        >
          ✕
        </button>
      </div>
      <div className="space-y-2">
        <label className="text-sm text-gray-400">API URL</label>
        <input
          type="text"
          value={inputUrl}
          onChange={(e) => setInputUrl(e.target.value)}
          placeholder="Enter API URL"
          className="w-full bg-black/40 border border-white/10 text-white text-sm placeholder:text-white/40 px-3 py-2 rounded-md focus:outline-none focus:ring-2 focus:ring-green-500/50"
        />
      </div>
      <div className="flex gap-2">
        <button
          onClick={handleSave}
          className="flex-1 px-3 py-2 text-sm bg-green-500/20 text-green-400 border border-green-500/30 hover:bg-green-500/30 rounded-md transition-colors font-medium"
        >
          Save
        </button>
        <button
          onClick={handleReset}
          className="flex-1 px-3 py-2 text-sm border border-white/10 hover:bg-white/10 rounded-md transition-colors text-white font-medium"
        >
          Reset
        </button>
      </div>
    </div>
  )
}
