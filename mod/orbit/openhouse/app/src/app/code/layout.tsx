import type { Metadata } from 'next'

export const metadata: Metadata = {
  title: 'Code — OpenHouse',
  description: 'The Solidity that holds the shares, the module that splits the rent, the API and the MCP tool server. No black box.',
}

export default function Layout({ children }: { children: React.ReactNode }) {
  return children
}
