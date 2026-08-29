import type { Metadata } from 'next'

export const metadata: Metadata = {
  title: 'Cap Table — OpenHouse',
  description: 'Who owns what and what has been distributed. Public by default.',
}

export default function Layout({ children }: { children: React.ReactNode }) {
  return children
}
