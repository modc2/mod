"use client";

import { ToastContainer } from 'react-toastify'
import 'react-toastify/dist/ReactToastify.css'
import { ThemeProvider } from './theme'

export function Providers({ children }: { children: React.ReactNode }) {
  return (
    <ThemeProvider>
      {children}
      {/* Toast chrome is repainted per skin in globals.css; `theme="dark"`
          here only stops Toastify shipping its own white card. */}
      <ToastContainer
        position="bottom-right"
        autoClose={4000}
        hideProgressBar={false}
        newestOnTop
        closeOnClick
        theme="dark"
      />
    </ThemeProvider>
  )
}
