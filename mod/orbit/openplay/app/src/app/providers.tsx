"use client";

import { ToastContainer } from 'react-toastify'
import 'react-toastify/dist/ReactToastify.css'
import { ThemeProvider } from './theme'

export function Providers({ children }: { children: React.ReactNode }) {
  return (
    <ThemeProvider>
      {/* the far cloud band — the near one rides on body::before */}
      <div className="level-deco" aria-hidden />
      {children}
      <ToastContainer position="top-center" autoClose={3500} hideProgressBar newestOnTop closeOnClick theme="dark" />
    </ThemeProvider>
  )
}
