import type { Metadata } from 'next'
import { Inter, JetBrains_Mono } from 'next/font/google'
import './globals.css'
import '@rainbow-me/rainbowkit/styles.css'
import { Providers } from './providers'
import { ToastContainer } from 'react-toastify'
import 'react-toastify/dist/ReactToastify.css'

const inter = Inter({ subsets: ['latin'], display: 'swap' })
const mono = JetBrains_Mono({ subsets: ['latin'], display: 'swap', variable: '--font-mono', weight: ['400', '500', '700'] })

export const metadata: Metadata = {
  title: 'PreFi — call the close, split the pot',
  description: 'A prediction pool on Hyperliquid and Bittensor. Deposit USDC on HyperEVM, stake on where an asset closes, the pot splits by dollars × accuracy every round.',
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="en" className={mono.variable}>
      <body className={inter.className}>
        <Providers>
          {children}
          <ToastContainer
            position="bottom-right"
            autoClose={4500}
            hideProgressBar
            newestOnTop
            closeOnClick
            pauseOnHover
            theme="dark"
          />
        </Providers>
      </body>
    </html>
  )
}
