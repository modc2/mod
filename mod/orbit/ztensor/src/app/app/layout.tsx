import type { Metadata, Viewport } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'ztensor · anonymous subnets on any chain',
  description:
    'Bittensor-style subnets where every action is a ring signature: miners and validators are public sets, never public identities.',
};

export const viewport: Viewport = { themeColor: '#07080c', width: 'device-width', initialScale: 1 };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
