import type { Metadata, Viewport } from 'next';
import Shell from '@/components/Shell';
import './styles/sleek.css';
import './styles/news.css';
import './styles/tx.css';
import './styles/subnet.css';
import './styles/market.css';
import './styles/dock.css';
import './globals.css';

export const metadata: Metadata = {
  title: 'bt — the open Bittensor explorer',
  description: 'Every subnet, price, validator and account — indexed locally, served instantly. Open source, no API key.',
  icons: { icon: `${process.env.NEXT_PUBLIC_BASE ?? ''}/icon.svg` },
};

export const viewport: Viewport = { width: 'device-width', initialScale: 1, viewportFit: 'cover' };

/* set the theme (and optional skin) before first paint so there is no flash */
const THEME = `(function(){var t;try{t=localStorage.getItem('bt.theme')}catch(e){}
if(t!=='light'&&t!=='dark')t=matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light';
document.documentElement.dataset.theme=t;
var k;try{k=localStorage.getItem('bt.skin')}catch(e){}
if(k&&/^[a-z]+$/.test(k)){document.documentElement.dataset.skin=k;var l=document.createElement('link');
l.rel='stylesheet';l.id='bt-skin';l.href='${process.env.NEXT_PUBLIC_BASE ?? ''}/skins/'+k+'.css';document.head.appendChild(l)}})()`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head><script dangerouslySetInnerHTML={{ __html: THEME }} /></head>
      <body><Shell>{children}</Shell></body>
    </html>
  );
}
