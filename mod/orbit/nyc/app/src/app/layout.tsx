import type { Metadata } from 'next'
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

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={pixel.variable}>
      <body className="bg-[#0a0a18] antialiased">{children}</body>
    </html>
  )
}
