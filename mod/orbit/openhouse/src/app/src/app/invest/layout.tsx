import type { Metadata } from 'next'

export const metadata: Metadata = {
  title: 'The House — OpenHouse',
  description: 'The live house: who owns how much of it, a rent check you can write with the split previewed, the rent ledger, and the investor float. Simulated on this node.',
}

export default function Layout({ children }: { children: React.ReactNode }) {
  return children
}
