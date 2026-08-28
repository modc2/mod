import type { Metadata } from 'next'
import './globals.css'
import { Providers } from './providers'
import { ThemeBoot } from './theme'

export const metadata: Metadata = {
  title: 'BlocTime — Time-Weighted Staking',
  description: 'Stake tokens, earn BlocTime based on lock duration',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    // The default skin is server-rendered here and corrected to whatever the
    // visitor last picked by ThemeBoot, before paint — so the first frame is
    // already the right console. `suppressHydrationWarning` because that
    // script legitimately edits these two attributes.
    <html lang="en" data-theme="midnight" data-base="dark" suppressHydrationWarning>
      <head>
        <ThemeBoot />
      </head>
      <body suppressHydrationWarning>
        <Providers>
          {children}
        </Providers>
      </body>
    </html>
  )
}
