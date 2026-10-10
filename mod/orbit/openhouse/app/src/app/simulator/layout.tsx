import type { Metadata } from 'next'

export const metadata: Metadata = {
  title: 'Simulator — OpenHouse',
  description: 'Scrub the timeline and watch your equity fill up. Home price, payment, protocol fee and rent credit, projected month by month.',
}

export default function Layout({ children }: { children: React.ReactNode }) {
  return children
}
