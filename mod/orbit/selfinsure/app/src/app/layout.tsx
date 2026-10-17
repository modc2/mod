import type { Metadata } from 'next'
import './globals.css'
import { SiteNav, SiteFooter } from '@/components/chrome'

export const metadata: Metadata = {
  title: 'selfinsure — member-owned insurance mutuals',
  description:
    'Insurance as a mutual the members own: operator fee hard-capped and published on chain, surplus returned pro rata, claims adjudicated by agents, oracle optional.',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <SiteNav />
        <main>{children}</main>
        <SiteFooter />
      </body>
    </html>
  )
}
