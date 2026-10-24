import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'zkworld — ZK contract workbench',
  description: 'Make, vibecode and test Cairo/Starknet contracts. Each contract is its own submod.',
  icons: {
    icon: "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Cpath d='M16 3 L3 27 L19 27 Z' fill='%23ff8a6b'/%3E%3Cpath d='M16 3 L19 27 L29 27 Z' fill='%23a3381f'/%3E%3C/svg%3E",
  },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
