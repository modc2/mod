import type { Metadata } from 'next'
import './globals.css'

export const metadata: Metadata = {
  title: 'Wingman',
  description: 'Photo lineup for dating apps — face-aware, measured, nothing retouched.',
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  )
}
