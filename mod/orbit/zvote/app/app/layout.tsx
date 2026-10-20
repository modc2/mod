import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'ztensor — anonymous consensus',
  description:
    'Anonymous voting & consensus for miner/validator networks. Private ballots, public books.',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
