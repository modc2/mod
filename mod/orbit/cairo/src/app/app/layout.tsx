import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'cairo — ZK contract workbench',
  description: 'Make, vibecode and test Cairo/Starknet contracts. Each contract is its own submod.',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
