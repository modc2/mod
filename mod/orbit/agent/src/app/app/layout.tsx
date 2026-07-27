import type { Metadata } from 'next'
import './globals.css'

export const metadata: Metadata = {
  title: 'Agent — Mod Agent OS',
  description: 'Autonomous coding agent OS — skills, agents, chains, and a shared library market.',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  )
}
