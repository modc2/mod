import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'zQuorum — anonymous quorums',
  description:
    'A group reaches a quorum without revealing who voted. Pedersen commitments in, nullifiers out — private ballots, public outcomes.',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
