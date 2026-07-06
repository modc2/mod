export const metadata = { title: 'Basebuilder', description: 'Forkable module template' }

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body style={{ margin: 0, fontFamily: 'monospace', background: '#0a0a0a', color: '#e0e0e0' }}>
        {children}
      </body>
    </html>
  )
}
