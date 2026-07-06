import type { Metadata } from 'next'
import './globals.css'

export const metadata: Metadata = {
  title: 'mod-dns',
  description: 'Decentralized authoritative DNS — P2P record sync via Kademlia DHT + GossipSub',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body suppressHydrationWarning>{children}</body>
    </html>
  )
}
