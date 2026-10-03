import type { Metadata } from 'next'

export const metadata: Metadata = {
  title: 'Testnet — OpenHouse',
  description: 'Run the OpenHouse walkthroughs in a sandbox: a first rent payment, rent paid by bank transfer from any bank, a statement import, a city pausing payments — every step a real MCP tool call.',
}

export default function Layout({ children }: { children: React.ReactNode }) {
  return children
}
