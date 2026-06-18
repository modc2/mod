"use client"

import { useEffect } from 'react'
import { usePathname } from 'next/navigation'
import { ToastContainer } from 'react-toastify'
import 'react-toastify/dist/ReactToastify.css'
import { UserProvider } from '@/context'
import { SearchProvider } from '@/context/SearchContext'
import {
  SplitScreenProvider,
  useSplitScreenContext,
} from '@/context/SplitScreenContext'
import { ControlPanelProvider } from '@/context/ControlPanelContext'
import { MarketCreditProvider } from '@/context/MarketCreditContext'
import {
  LayoutProvider,
  useLayoutContext,
} from '@/context/LayoutContext'
import { ThemeProvider, useTheme } from '@/context/ThemeContext'
import { SplitScreenControls } from '@/components/SplitScreenControls'
import { MetaMaskProvider } from '@/wallet/MetaMaskProvider'
import { ThemeInitializer } from '@/themes/ThemeInitializer'
import { TopBar } from '@/header/TopBar'
import { EditSidebar, EDIT_SIDEBAR_WIDTH } from '@/mod/edit/EditSidebar'
import { AgentPanel, AGENT_PANEL_WIDTH } from '@/agent/AgentPanel'

function ThemedToast() {
  const { effectiveTheme } = useTheme()
  return (
    <ToastContainer
      position="bottom-right"
      autoClose={3000}
      hideProgressBar={false}
      newestOnTop={true}
      closeOnClick
      rtl={false}
      pauseOnFocusLoss
      draggable
      pauseOnHover
      theme={effectiveTheme === 'dark' ? 'dark' : 'light'}
      style={{ zIndex: 9999 }}
    />
  )
}



function LayoutContent({ children }: { children: React.ReactNode }) {
  const {
    isSplitScreen,
    leftPanelUrl,
    rightPanelUrl,
    setLeftPanelUrl,
    orientation,
    isCollapsed,
  } = useSplitScreenContext()

  const { isHeaderMode, isHeaderCollapsed, isEditSidebarOpen, isAgentSidebarOpen } = useLayoutContext()
  const pathname = usePathname()

  useEffect(() => {
    if (isSplitScreen && leftPanelUrl === pathname) {
      setLeftPanelUrl(pathname)
    }
  }, [pathname, isSplitScreen, leftPanelUrl, setLeftPanelUrl])

  const isVertical = orientation === 'vertical'
  const containerClass = isVertical ? 'flex' : 'flex flex-col'
  const panelClass = 'flex-1'
  const borderClass = isVertical
    ? 'border-r-2 border-green-500/30'
    : 'border-b-2 border-green-500/30'

  return (
    <div className="flex h-screen transition-all duration-200" style={{ paddingTop: isHeaderCollapsed ? '6px' : '64px', paddingLeft: isHeaderCollapsed ? '0px' : isAgentSidebarOpen ? `calc(var(--sidebar-width, 0px) + ${AGENT_PANEL_WIDTH}px)` : 'var(--sidebar-width, 0px)', paddingRight: isEditSidebarOpen ? `${EDIT_SIDEBAR_WIDTH}px` : '0px', backgroundColor: 'var(--bg-primary)', transition: 'padding-top 0.2s ease, padding-left 0.2s ease, padding-right 0.2s ease' }}>
      {/* Top bar */}
      <TopBar />
      {/* Edit sidebar */}
      <EditSidebar />
      {/* Agent panel */}
      <AgentPanel />

      {/* Main content */}
      <div className="flex-1 flex flex-col">
        <main className="flex-1 overflow-auto flex">
          {isSplitScreen ? (
            <div className={`w-full h-full ${containerClass}`}>
              <div
                className={`${isCollapsed ? 'hidden' : panelClass} overflow-auto ${borderClass}`}
              >
                {children}
              </div>

              <div className={`${panelClass} overflow-auto`}>
                <iframe
                  src={rightPanelUrl}
                  className="w-full h-full border-0"
                  title="Split Screen Panel"
                />
              </div>
            </div>
          ) : (
            <div className="flex-1 overflow-auto">{children}</div>
          )}
        </main>

        <SplitScreenControls />
      </div>
    </div>
  )
}

export default function Providers({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <ThemeProvider>
      <ThemeInitializer />
      <MetaMaskProvider>
        <UserProvider>
          <MarketCreditProvider>
            <SearchProvider>
              <SplitScreenProvider>
                <ControlPanelProvider>
                  <LayoutProvider>
                    <LayoutContent>{children}</LayoutContent>
                    <ThemedToast />
                  </LayoutProvider>
                </ControlPanelProvider>
              </SplitScreenProvider>
            </SearchProvider>
          </MarketCreditProvider>
        </UserProvider>
      </MetaMaskProvider>
    </ThemeProvider>
  )
}
