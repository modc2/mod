'use client'

import './globals.css'
import { Inter } from 'next/font/google'
import { Sidebar } from '@/block/sidebar/Sidebar'
import { Header } from '@/header/Header'
import { UserProvider } from '@/context'
import { SearchProvider } from '@/context/SearchContext'
import { SidebarProvider } from '@/context/SidebarContext'

const inter = Inter({ subsets: ['latin'] })

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="en">
      <body className={inter.className}>
        <UserProvider>
          <SearchProvider>
            <SidebarProvider>
              <div className="flex h-screen bg-black">
                <Sidebar />
                <div className="flex-1 flex flex-col" style={{ marginLeft: '80px' }}>
                  <Header />
                  <main className="flex-1 overflow-auto">
                    {children}
                  </main>
                </div>
              </div>
            </SidebarProvider>
          </SearchProvider>
        </UserProvider>
      </body>
    </html>
  )
}
