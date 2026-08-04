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
    // data-mode carries the skin (see the DIGITAL block in globals.css). Paper
    // is the default; the blocking script below upgrades it to the saved mode
    // before the first paint, so a digital-mode visitor never sees a flash of
    // pastel paper. The nav chip in page.tsx writes the same key.
    <html lang="en" data-mode="paper" suppressHydrationWarning>
      <head>
        <script
          dangerouslySetInnerHTML={{
            __html: 'try{if(localStorage.getItem("openhouse_mode")==="digital")document.documentElement.setAttribute("data-mode","digital")}catch(e){}',
          }}
        />
      </head>
      <body suppressHydrationWarning>
        <Providers>
          {children}
        </Providers>
      </body>
    </html>
  )
}
