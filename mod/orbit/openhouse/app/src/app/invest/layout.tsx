import type { Metadata } from 'next'

export const metadata: Metadata = {
  title: 'Invest — OpenHouse',
  description: 'The building, the float and the mint form. Fractional property shares on Base Sepolia.',
}

export default function Layout({ children }: { children: React.ReactNode }) {
  return children
}
