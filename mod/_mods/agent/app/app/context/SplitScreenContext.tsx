'use client'

import { createContext, useContext, useState, ReactNode } from 'react'

type Orientation = 'vertical' | 'horizontal'

interface SplitScreenContextType {
  isSplitScreen: boolean
  toggleSplitScreen: () => void
  leftPanelUrl: string
  rightPanelUrl: string
  setLeftPanelUrl: (url: string) => void
  setRightPanelUrl: (url: string) => void
  orientation: Orientation
  setOrientation: (orientation: Orientation) => void
  isCollapsed: boolean
  setIsCollapsed: (collapsed: boolean) => void
}

const SplitScreenContext = createContext<SplitScreenContextType | undefined>(undefined)

export function SplitScreenProvider({ children }: { children: ReactNode }) {
  const [isSplitScreen, setIsSplitScreen] = useState(false)
  const [leftPanelUrl, setLeftPanelUrl] = useState('/')
  const [rightPanelUrl, setRightPanelUrl] = useState('/mod/explore')
  const [orientation, setOrientation] = useState<Orientation>('vertical')
  const [isCollapsed, setIsCollapsed] = useState(false)

  const toggleSplitScreen = () => setIsSplitScreen(!isSplitScreen)

  return (
    <SplitScreenContext.Provider value={{
      isSplitScreen,
      toggleSplitScreen,
      leftPanelUrl,
      rightPanelUrl,
      setLeftPanelUrl,
      setRightPanelUrl,
      orientation,
      setOrientation,
      isCollapsed,
      setIsCollapsed
    }}>
      {children}
    </SplitScreenContext.Provider>
  )
}

export function useSplitScreenContext() {
  const context = useContext(SplitScreenContext)
  if (!context) throw new Error('useSplitScreenContext must be used within SplitScreenProvider')
  return context
}