import type { Metadata } from 'next'
import './globals.css'
import { Providers } from './providers'

export const metadata: Metadata = {
  metadataBase: new URL('https://modc2.com/openevent'),
  title: 'OpenEvent — every event, every platform, one place',
  description: 'One live map of what\'s happening near you — pulled across Luma, Eventbrite and Meetup — and one composer to broadcast your event to all of them at once.',
  openGraph: {
    title: 'OpenEvent — every event, every platform, one place',
    description: 'Stop checking five apps. One feed for discovery, one composer to broadcast everywhere.',
  },
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
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
