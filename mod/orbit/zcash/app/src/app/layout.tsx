import type { Metadata } from 'next'

export const metadata: Metadata = {
  title: 'Zcash — explorer, wallet, bridge',
  description: 'Zcash explorer, transparent wallet and cross-chain bridge',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body style={{ margin: 0, fontFamily: 'system-ui, -apple-system, sans-serif', background: '#0a0a0f', color: '#e0e0e0' }}>
        {children}
      </body>
    </html>
  )
}
