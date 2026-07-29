import type { Metadata, Viewport } from 'next'
import localFont from 'next/font/local'
import './globals.css'

/**
 * Press Start 2P, the NES-era display face, vendored rather than pulled from
 * Google's CDN so a build never depends on the network. It covers basic Latin
 * only — anything outside that (·, ↗, ², –) falls through to the stack below
 * per glyph instead of rendering as tofu, so pixel-font labels stick to ASCII.
 */
const pixel = localFont({
  src: '../fonts/PressStart2P.ttf',
  weight: '400',
  display: 'swap',
  variable: '--font-pixel',
  fallback: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
})

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
  themeColor: '#0a0a18',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={pixel.variable}>
      <body className="overscroll-none bg-[#0a0a18] antialiased">{children}</body>
    </html>
  )
}
