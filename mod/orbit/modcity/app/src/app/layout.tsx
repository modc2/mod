import type { Metadata } from 'next'
import './globals.css'
import { Nav, Footer } from '@/components/site'

export const metadata: Metadata = {
  title: 'ModCity — Prefab buildings, snapped together like LEGO',
  description: 'A protocol for modular housing & cities. Snap factory-built modules onto a grid, re-skin into any architecture style, and build a tower in a season — not a decade.',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <main className="min-h-screen relative overflow-x-hidden flex flex-col">
          <Nav />
          <div className="flex-1">{children}</div>
          <Footer />
        </main>
      </body>
    </html>
  )
}
