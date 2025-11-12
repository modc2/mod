'use client'
import { Sidebar } from './Sidebar'
import { useSidebarContext } from '@/app/block/context/SidebarContext'

export function ClientSidebar({ children }: { children: React.ReactNode }) {
  const { isSidebarExpanded } = useSidebarContext()

  return (
    <>
      <Sidebar />
      <main className="pt-24 transition-all duration-300" style={{ marginLeft: 'var(--sidebar-width, 64px)', paddingLeft: '2rem' }}>
        <div className="min-h-screen">
          {children}
        </div>
      </main>
      <style jsx global>{`
        :root {
          --sidebar-width: ${isSidebarExpanded ? '256px' : '64px'};
        }
      `}</style>
    </>
  )
}