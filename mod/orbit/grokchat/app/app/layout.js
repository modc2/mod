import './globals.css'

export const metadata = {
  title: 'grokchat',
  description: 'Chat with Grok through the grokbot module',
}

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  )
}
