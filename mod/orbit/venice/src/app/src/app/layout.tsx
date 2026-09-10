import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "venice — a generative atelier",
  // No pay-per-turn claim here: whether that path exists is per-deployment
  // (x402 + a backend key), and static metadata can't check.
  description: "Venice AI: text, image & video in one thread. Bring your own key. Wallet auth.",
};

// Paint the stored mode before first paint — otherwise every reload flashes
// the default theme. With nothing stored we follow the OS so a light-mode
// machine never gets a black screen. Mirrors lib/theme.ts (keep ids in sync).
const NO_FLASH = `try{
var L=['paper','gameboy','bloom'],
    D=['arcade','atelier','noir','lagoon','commodore','vapor','matrix','velvet'],
    t=localStorage.getItem('venice:theme');
if(L.indexOf(t)<0&&D.indexOf(t)<0)
  t=(window.matchMedia&&window.matchMedia('(prefers-color-scheme: light)').matches)?'paper':'arcade';
var b=L.indexOf(t)>=0?'light':'dark',e=document.documentElement;
e.dataset.theme=t;e.dataset.base=b;e.style.colorScheme=b;
}catch(e){}`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    // the no-flash script rewrites data-theme/data-base before hydration, so
    // React must not diff what the server rendered against it
    <html lang="en" data-theme="arcade" data-base="dark" suppressHydrationWarning>
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link
          href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,300;0,400;0,500;0,600;1,400&family=Inter:wght@400;500;600;700&family=Press+Start+2P&family=VT323&display=swap"
          rel="stylesheet"
        />
        <script dangerouslySetInnerHTML={{ __html: NO_FLASH }} />
      </head>
      <body>{children}</body>
    </html>
  );
}
