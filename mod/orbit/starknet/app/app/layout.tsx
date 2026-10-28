import './globals.css';
import type { Metadata, Viewport } from 'next';
import Shell from '@/components/Shell';
import { NetworkProvider } from '@/lib/net';

export const metadata: Metadata = {
  title: 'Starknet explorer',
  description: 'Blocks, transactions, accounts and any contract on Starknet — plus the STRK20 privacy pool. Read-only.',
};
export const viewport: Viewport = { themeColor: '#05030b', width: 'device-width', initialScale: 1 };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="" />
        <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Chakra+Petch:wght@500;600;700&family=JetBrains+Mono:wght@400;600;700&display=swap" />
      </head>
      <body>
        <NetworkProvider>
          <Shell>{children}</Shell>
        </NetworkProvider>
      </body>
    </html>
  );
}
