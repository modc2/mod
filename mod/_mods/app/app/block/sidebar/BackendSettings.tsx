'use client'
import { useState, useEffect } from 'react'
import { Cog6ToothIcon } from '@heroicons/react/24/outline'
import { useSidebarContext } from '@/app/block/context/SidebarContext'

const DEFAULT_API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'

export function BackendSettings() {
  const { isSidebarExpanded } = useSidebarContext()
  const [isOpen, setIsOpen] = useState(false)
  const [inputUrl, setInputUrl] = useState<string>('')

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

  if (!isSidebarExpanded) {
    return (
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="p-2 rounded-md hover:bg-white/5 text-gray-400 hover:text-white transition-colors"
        title="API Settings"
      >
        <Cog6ToothIcon className="h-6 w-6" />
      </button>
    )
  }

  return (
    <div className="space-y-2">
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="w-full flex items-center gap-2 p-2 rounded-md hover:bg-white/5 text-gray-400 hover:text-white transition-colors"
      >
        <Cog6ToothIcon className="h-5 w-5" />
        <span className="text-sm font-semibold">API Settings</span>
      </button>
      
      {isOpen && (
        <div className="space-y-2 p-2 bg-white/5 rounded-md">
          <input
            type="text"
            value={inputUrl}
            onChange={(e) => setInputUrl(e.target.value)}
            placeholder="Enter API URL"
            className="w-full bg-black/40 border border-white/10 text-white text-sm placeholder:text-white/40 px-2 py-1.5 rounded-md focus:outline-none focus:ring-2 focus:ring-green-500/50"
          />
          <div className="flex gap-2">
            <button
              onClick={handleSave}
              className="flex-1 px-2 py-1.5 text-xs bg-green-500/20 text-green-400 border border-green-500/30 hover:bg-green-500/30 rounded-md transition-colors"
            >
              Save
            </button>
            <button
              onClick={handleReset}
              className="flex-1 px-2 py-1.5 text-xs border border-white/10 hover:bg-white/10 rounded-md transition-colors"
            >
              Reset
            </button>
          </div>
        </div>
      )}
    </div>
  )
}