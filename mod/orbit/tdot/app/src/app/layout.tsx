import type { Metadata } from 'next'
import './globals.css'

export const metadata: Metadata = {
  title: 'Toronto Atlas — open-data GIS',
  description:
    'A browser GIS for Toronto: crime by neighbourhood, TTC transit, cycling, '
    + 'parks and civic data as map layers. Built entirely on public open data.',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="bg-[#0b0e14] antialiased">{children}</body>
    </html>
  )
}
