'use client'

import { useSplitScreenContext } from '@/block/context/SplitScreenContext'
import { Squares2X2Icon, XMarkIcon, ArrowsPointingInIcon, ArrowsPointingOutIcon } from '@heroicons/react/24/outline'
import { useState } from 'react'

export function SplitScreenControls() {
  const { isSplitScreen, toggleSplitScreen } = useSplitScreenContext()
  const [orientation, setOrientation] = useState<'vertical' | 'horizontal'>('vertical')
  const [isCollapsed, setIsCollapsed] = useState(false)

  const toggleOrientation = () => {
    setOrientation(prev => prev === 'vertical' ? 'horizontal' : 'vertical')
  }

  const toggleCollapse = () => {
    setIsCollapsed(!isCollapsed)
  }

  if (!isSplitScreen) return null

  return (
    <div className="fixed bottom-4 right-4 z-50 flex gap-2">
      <button
        onClick={toggleOrientation}
        className="p-3 rounded-lg border-2 border-green-500/50 bg-green-500/20 hover:bg-green-500/30 text-green-400 transition-all active:scale-95"
        title={`Switch to ${orientation === 'vertical' ? 'Horizontal' : 'Vertical'} Split`}
      >
        <Squares2X2Icon className={`w-6 h-6 ${orientation === 'horizontal' ? 'rotate-90' : ''}`} />
      </button>
      <button
        onClick={toggleCollapse}
        className="p-3 rounded-lg border-2 border-green-500/50 bg-green-500/20 hover:bg-green-500/30 text-green-400 transition-all active:scale-95"
        title={isCollapsed ? 'Expand Panel' : 'Collapse Panel'}
      >
        {isCollapsed ? <ArrowsPointingOutIcon className="w-6 h-6" /> : <ArrowsPointingInIcon className="w-6 h-6" />}
      </button>
      <button
        onClick={toggleSplitScreen}
        className="p-3 rounded-lg border-2 border-red-500/50 bg-red-500/20 hover:bg-red-500/30 text-red-400 transition-all active:scale-95"
        title="Close Split Screen"
      >
        <XMarkIcon className="w-6 h-6" />
      </button>
    </div>
  )
}

export function useSplitScreenOrientation() {
  const [orientation, setOrientation] = useState<'vertical' | 'horizontal'>('vertical')
  const [isCollapsed, setIsCollapsed] = useState(false)
  
  return { orientation, setOrientation, isCollapsed, setIsCollapsed }
}