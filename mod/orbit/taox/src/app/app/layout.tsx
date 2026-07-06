import './globals.css'

export const metadata = {
  title: 'Taox — ETH/SOL → TAO',
  description: 'Convert ETH and SOL to native TAO. MetaMask + SubWallet.',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  )
}
