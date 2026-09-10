import type { Metadata } from 'next'

export const metadata: Metadata = {
  title: 'Monero — explorer, wallet, scanner',
  description: 'Monero explorer, encrypted wallet, local view-key scanner and swaps',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body style={{ margin: 0, fontFamily: 'system-ui, -apple-system, sans-serif', background: '#0e0d10', color: '#ece8ee' }}>
        {children}
      </body>
    </html>
  )
}
