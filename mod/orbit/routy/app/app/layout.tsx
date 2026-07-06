import type { Metadata } from 'next'
import './globals.css'

export const metadata: Metadata = {
  title: 'routy',
  description: 'Local gateway router — unified proxy for all mod apps and APIs',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  )
}
