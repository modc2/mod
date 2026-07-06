"use client";

import { ToastContainer } from 'react-toastify'
import 'react-toastify/dist/ReactToastify.css'
import { WalletProvider } from './lib/wallet'

export function Providers({ children }: { children: React.ReactNode }) {
  return (
    <WalletProvider>
      {children}
      <ToastContainer
        position="top-right"
        autoClose={4000}
        hideProgressBar={false}
        newestOnTop
        closeOnClick
        theme="dark"
      />
    </WalletProvider>
  )
}
