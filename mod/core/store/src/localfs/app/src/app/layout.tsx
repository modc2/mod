import type { Metadata } from 'next'
import './globals.css'

export const metadata: Metadata = {
  title: 'LocalFS — Content-Addressable Storage',
  description: 'Browser interface for the LocalFS content-addressable filesystem',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body suppressHydrationWarning>{children}</body>
    </html>
  )
}
