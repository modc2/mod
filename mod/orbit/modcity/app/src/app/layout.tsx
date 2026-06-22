import type { Metadata } from 'next'
import './globals.css'

export const metadata: Metadata = {
  title: 'ModCity — Prefab buildings, snapped together like LEGO',
  description: 'A protocol for modular housing & cities. Snap factory-built modules onto a grid, re-skin into any architecture style, and build a tower in a season — not a decade.',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  )
}
