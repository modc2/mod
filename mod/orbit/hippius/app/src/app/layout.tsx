import type { Metadata } from 'next'
import './globals.css'

export const metadata: Metadata = {
  title: 'Hippius — BYOK Storage Console',
  description:
    'Bring-your-own-key console for Hippius decentralized S3 storage. Your keys stay in your browser.',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  )
}
