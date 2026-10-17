import type { Metadata } from 'next'

export const metadata: Metadata = {
  title: 'The Landscape — OpenHouse',
  description: 'Every other on-chain housing project, sorted by who ends up owning the house — with sources, live numbers, and where the field is ahead of us.',
}

export default function Layout({ children }: { children: React.ReactNode }) {
  return children
}
