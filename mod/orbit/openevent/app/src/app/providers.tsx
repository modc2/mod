"use client";

import { ToastContainer } from 'react-toastify'
import 'react-toastify/dist/ReactToastify.css'

export function Providers({ children }: { children: React.ReactNode }) {
  return (
    <>
      {children}
      <ToastContainer position="top-center" autoClose={3500} hideProgressBar newestOnTop closeOnClick theme="dark" />
    </>
  )
}
