import type { Metadata } from 'next'

export const metadata: Metadata = {
  title: 'NEAR — OpenHouse',
  description: 'The NEAR side of OpenHouse: a WASM verifier read live off NEAR, and a rent rail that books native NEAR transfers as renter equity.',
}

export default function Layout({ children }: { children: React.ReactNode }) {
  return children
}
