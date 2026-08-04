import type { Metadata } from 'next'
import './globals.css'
import { Providers } from './providers'
import { ThemeBoot } from './theme'

export const metadata: Metadata = {
  metadataBase: new URL('https://modc2.com/openplay'),
  title: 'OpenPlay — Pickup games across the city',
  description: 'Soccer, hockey, basketball. Search your city and jump straight into the games happening near you. Free to play. Anyone can create a game — no sign-up needed.',
  openGraph: {
    title: 'OpenPlay — Pickup games across the city',
    description: 'Search your city and start exploring pickup games near you. Free by default. Anyone can start a game. Ditch the dozen WhatsApp groups.',
  },
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        {/* stamps the visitor's last world on <html> before first paint */}
        <ThemeBoot />
        <link
          rel="stylesheet"
          href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
          integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY="
          crossOrigin=""
        />
      </head>
      <body suppressHydrationWarning>
        <Providers>{children}</Providers>
      </body>
    </html>
  )
}
