import type { Metadata } from 'next'
import './globals.css'
import { Providers } from './providers'

export const metadata: Metadata = {
  metadataBase: new URL('https://modc2.com/openhouse'),
  title: 'OpenHouse — Own the Skyline',
  description: 'Fractional property ownership on-chain. Buy a slice, earn dividends, own the building. No broker, no buzzer.',
  openGraph: {
    title: 'OpenHouse — Own the Skyline',
    description: 'Real estate built generational wealth for everyone who could afford the door. OpenHouse hands the rest of us a key.',
  },
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body suppressHydrationWarning>
        <Providers>
          {children}
        </Providers>
      </body>
    </html>
  )
}
