import type { Metadata } from 'next'

export const metadata: Metadata = {
  title: 'Simulate — OpenHouse',
  description: 'Play twenty years of the deal through the real code: rent by bank transfer, equity month by month, quarterly closes, the refusal at the end. Real code, fake money.',
}

export default function Layout({ children }: { children: React.ReactNode }) {
  return children
}
