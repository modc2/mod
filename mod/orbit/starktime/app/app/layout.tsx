import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'starktime — stake time on Starknet',
  description: 'Lock STRK for time. Weight = amount x lock multiplier. Rewards stream pro-rata by weight.',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
