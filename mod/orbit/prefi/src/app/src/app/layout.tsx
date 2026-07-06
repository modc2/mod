import type { Metadata } from 'next'
import { Inter } from 'next/font/google'
import './globals.css'
import '@rainbow-me/rainbowkit/styles.css'
import { Providers } from './providers'
import { ToastContainer } from 'react-toastify'
import 'react-toastify/dist/ReactToastify.css'

const inter = Inter({ subsets: ['latin'] })

export const metadata: Metadata = {
  title: 'PreFi - Trade, Earn PREFI, Stake for Weekly Yields',
  description: 'Trade assets on Uniswap V3 via Base. Profit goes to treasury, earn PREFI tokens 1:1, lock for staketime to claim weekly treasury distributions.',
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="en">
      <body className={inter.className}>
        <Providers>
          {children}
          <ToastContainer
            position="top-right"
            autoClose={4000}
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
