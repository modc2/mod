import './globals.css';
import type { Metadata, Viewport } from 'next';
import Shell from '@/components/Shell';
import { NetworkProvider } from '@/lib/net';

export const metadata: Metadata = {
  title: 'Starknet explorer',
  description: 'Blocks, transactions, accounts and any contract on Starknet — plus the STRK20 privacy pool. Read-only.',
};
export const viewport: Viewport = { themeColor: '#07090f', width: 'device-width', initialScale: 1 };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <NetworkProvider>
          <Shell>{children}</Shell>
        </NetworkProvider>
      </body>
    </html>
  );
}
