"use client";
import { createContext, useContext, useState, useEffect, ReactNode } from 'react'
import { usePathname } from 'next/navigation'

interface LayoutContextType {
  isHeaderMode: boolean
  toggleLayout: () => void
  isHeaderCollapsed: boolean
  setHeaderCollapsed: (collapsed: boolean) => void
  toggleHeaderCollapsed: () => void
  isEditSidebarOpen: boolean
  toggleEditSidebar: () => void
  setEditSidebarOpen: (open: boolean) => void
  isAgentSidebarOpen: boolean
  toggleAgentSidebar: () => void
  setAgentSidebarOpen: (open: boolean) => void
  isTerminalMode: boolean
  toggleTerminalMode: () => void
  setTerminalMode: (on: boolean) => void
}

const LayoutContext = createContext<LayoutContextType | undefined>(undefined)

export function LayoutProvider({ children }: { children: ReactNode }) {
  const [isHeaderMode, setIsHeaderMode] = useState(true)
  const [isHeaderCollapsed, setIsHeaderCollapsed] = useState(false)
  const [isEditSidebarOpen, setIsEditSidebarOpen] = useState(false)
  const [isAgentSidebarOpen, setIsAgentSidebarOpen] = useState(false)
  const [isTerminalMode, setIsTerminalMode] = useState(false)
  const pathname = usePathname()

  // Hydrate terminal mode from localStorage after mount to avoid SSR mismatch
  useEffect(() => {
    const saved = localStorage.getItem('sidebar_terminal_mode')
    if (saved === 'true') setIsTerminalMode(true)
  }, [])

  // Keep header visible on module pages (module info shows in header)
  useEffect(() => {
    setIsHeaderCollapsed(false)
    setIsEditSidebarOpen(false)
  }, [pathname])

  const toggleLayout = () => {
    setIsHeaderMode(!isHeaderMode)
  }

  const setHeaderCollapsed = (collapsed: boolean) => {
    setIsHeaderCollapsed(collapsed)
  }

  const toggleHeaderCollapsed = () => {
    setIsHeaderCollapsed(prev => !prev)
  }

  const toggleEditSidebar = () => {
    setIsEditSidebarOpen(prev => !prev)
  }

  const toggleAgentSidebar = () => {
    setIsAgentSidebarOpen(prev => !prev)
  }

  const toggleTerminalMode = () => {
    setIsTerminalMode(prev => {
      const next = !prev
      localStorage.setItem('sidebar_terminal_mode', String(next))
      return next
    })
  }

  const setTerminalMode = (on: boolean) => {
    setIsTerminalMode(on)
    localStorage.setItem('sidebar_terminal_mode', String(on))
  }

  return (
    <LayoutContext.Provider value={{ isHeaderMode, toggleLayout, isHeaderCollapsed, setHeaderCollapsed, toggleHeaderCollapsed, isEditSidebarOpen, toggleEditSidebar, setEditSidebarOpen: (open: boolean) => setIsEditSidebarOpen(open), isAgentSidebarOpen, toggleAgentSidebar, setAgentSidebarOpen: (open: boolean) => setIsAgentSidebarOpen(open), isTerminalMode, toggleTerminalMode, setTerminalMode }}>
      {children}
    </LayoutContext.Provider>
  )
}

export function useLayoutContext() {
  const context = useContext(LayoutContext)
  if (context === undefined) {
    throw new Error('useLayoutContext must be used within a LayoutProvider')
  }
  return context
}
