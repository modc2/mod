import type { Metadata } from 'next'
import { Inter } from 'next/font/google'
import './globals.css'
import { Header } from '@/app/block/header/Header'
import { UserProvider } from '@/app/block/context/UserContext'
import { SearchProvider } from '@/app/block/context/SearchContext'
import { ClientSidebar } from '@/app/block/sidebar/ClientSidebar'
import "react-responsive-modal/styles.css"
import "@/app/globals.css"
import { SidebarProvider } from '@/app/block/context/SidebarContext'

export const metadata: Metadata = {
  title: "dhub",
  description: "dhub - the hub for mods. built with commune-ai/commune",
  robots: 'all',
  icons: [{ rel: 'icon', url: '/favicon.ico' }]
}

const inter = Inter({ subsets: ['latin'] })

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode
}>) {
  return (
    <html lang="en">
      <body className={`${inter.className} h-full relative bg-black`}>
        <UserProvider>
          <SearchProvider>
            <SidebarProvider>
              <Header />
              <ClientSidebar>
                {children}
              </ClientSidebar>
            </SidebarProvider>
          </SearchProvider>
        </UserProvider>
      </body>
    </html>
  )
}