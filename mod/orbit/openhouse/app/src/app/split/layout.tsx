import type { Metadata } from 'next'

export const metadata: Metadata = {
  title: 'The Split — OpenHouse',
  description: 'Where every rent payment goes: 1-5% protocol fee, the rest split between renter equity and owner income by the owner\'s chosen model.',
}

export default function Layout({ children }: { children: React.ReactNode }) {
  return children
}
