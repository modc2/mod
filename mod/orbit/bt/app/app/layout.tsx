import type { Metadata, Viewport } from 'next';
import Shell from '@/components/Shell';
import './globals.css';

export const metadata: Metadata = {
  title: 'bt · WORLD τ-1 — the open Bittensor explorer',
  description: 'Every subnet, price, validator and account — indexed locally, served instantly. Open source, no API key.',
  icons: { icon: `${process.env.NEXT_PUBLIC_BASE ?? ''}/icon.svg` },
};

export const viewport: Viewport = { width: 'device-width', initialScale: 1, viewportFit: 'cover' };

/* set the theme before first paint so there is no flash */
const THEME = `(function(){var t;try{t=localStorage.getItem('bt.theme')}catch(e){}
if(t!=='light'&&t!=='dark')t=matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light';
document.documentElement.dataset.theme=t})()`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head><script dangerouslySetInnerHTML={{ __html: THEME }} /></head>
      <body><Shell>{children}</Shell></body>
    </html>
  );
}
