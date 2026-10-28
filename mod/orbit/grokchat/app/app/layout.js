import './globals.css'

// The grokbot mark — same favicon as the grokbot console.
const MARK =
  "data:image/svg+xml,%3Csvg xmlns=%27http://www.w3.org/2000/svg%27 viewBox=%270 0 8 8%27%3E%3Crect width=%278%27 height=%278%27 fill=%27%230a0b0f%27/%3E%3Cpath d=%27M1 1h2l4 6h-2z%27 fill=%27%23e8eaf2%27/%3E%3Cpath d=%27M5 1h2l-4 6h-2z%27 fill=%27%236ee7ff%27/%3E%3C/svg%3E"

export const metadata = {
  title: 'grokchat — your grokbots, and everyone else’s',
  description: 'Chat with Grok and grokbots through the grokbot module',
  icons: { icon: MARK },
}

export const viewport = { width: 'device-width', initialScale: 1 }

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  )
}
