import type { Metadata } from 'next'

export const metadata: Metadata = {
  title: 'Proofs — OpenHouse',
  description: 'Prove the rent was paid without saying who paid it: a bank runs a Cairo statement over the private payment, NEAR (WASM) and Ethereum verifiers check the fact, and the chain learns only that this home, this month, was paid.',
}

export default function Layout({ children }: { children: React.ReactNode }) {
  return children
}
