import type { Metadata } from 'next'
import './globals.css'

export const metadata: Metadata = {
  metadataBase: new URL('https://modc2.com/openbnb'),
  title: 'OpenBnB — stays, with the rules in the open',
  description:
    'An open short-stay marketplace. Hosts list places, guests book nights, and every rule of the market — fees, minimums, pricing, who may book — is set by its owner in the open, live.',
  openGraph: {
    title: 'OpenBnB — stays, with the rules in the open',
    description: 'Book a place. Read the house rules that priced it. Run your own market.',
  },
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body suppressHydrationWarning>{children}</body>
    </html>
  )
}
