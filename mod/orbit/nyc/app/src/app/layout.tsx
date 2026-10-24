import type { Metadata, Viewport } from 'next'
import './globals.css'

export const metadata: Metadata = {
  title: 'NYC Atlas — open-data GIS',
  description:
    'A browser GIS for New York City: housing prices, transit, parks and civic '
    + 'data as map layers. Built entirely on public open data.',
}

/**
 * The map is the page, so the layout claims the whole screen — including the
 * area behind a notch, which `cover` hands over in exchange for the app taking
 * responsibility for insets (see the `.safe-*` classes). Pinch-zoom is left
 * enabled: the HUD is small type, and blocking the browser's own zoom to keep
 * a layout tidy is not a trade worth making.
 */
export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  viewportFit: 'cover',
  themeColor: '#0a0d14',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="overscroll-none bg-[#0a0d14] antialiased">{children}</body>
    </html>
  )
}
