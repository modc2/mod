import type { Metadata } from 'next'
import './globals.css'
import ThemeProvider from './components/ThemeProvider'

export const metadata: Metadata = {
  title: 'Toronto Atlas — open-data GIS',
  description:
    'A browser GIS for Toronto: crime by neighbourhood, TTC transit, cycling, '
    + 'parks and civic data as map layers. Built entirely on public open data.',
}

/**
 * The theme is applied before React boots.
 *
 * `data-theme`/`data-base` are server-rendered with the default and corrected
 * from localStorage by the blocking script below, so the first paint already
 * has the final palette — no flash of the wrong theme after hydration. The id
 * list here must track THEMES in src/lib/theme.ts (the L set is the light-base
 * ids, which is what drives data-base).
 */
const THEME_BOOT =
  'try{var t=localStorage.getItem("tdot_theme"),'
  + 'A=["dark","day","paper","ttc","matrix","neon","ember","abyss","win95","contrast"],'
  + 'L=["day","paper","win95","contrast"];'
  + 'if(t&&A.indexOf(t)>=0){var d=document.documentElement;'
  + 'd.setAttribute("data-theme",t);'
  + 'd.setAttribute("data-base",L.indexOf(t)>=0?"light":"dark")}}catch(e){}'

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" data-theme="dark" data-base="dark" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOT }} />
      </head>
      <body className="bg-bg antialiased">
        <ThemeProvider>{children}</ThemeProvider>
      </body>
    </html>
  )
}
