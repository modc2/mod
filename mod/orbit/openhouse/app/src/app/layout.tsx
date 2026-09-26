import type { Metadata } from 'next'
import './globals.css'
import { Providers } from './providers'

export const metadata: Metadata = {
  metadataBase: new URL('https://modc2.com/openhouse'),
  title: 'OpenHouse — Own the Skyline',
  description: 'Fractional property ownership on-chain. Buy a slice, earn dividends, own the building. No broker, no buzzer.',
  openGraph: {
    title: 'OpenHouse — Own the Skyline',
    description: 'Real estate built generational wealth for everyone who could afford the door. OpenHouse hands the rest of us a key.',
  },
}

// Runs before React hydrates, so it can't import the VIBES table from
// components/vibe.tsx — the ids are baked in here as strings instead. Keep the three
// lists in step with VIBES when you add a cabinet; the picker validates
// against the same ids, and an id missing here just falls back to SUNDAY.
// `openhouse_mode` was the old two-way paper/digital key: a returning
// digital visitor is migrated to TERMINAL, which is the same skin.
const VIBE_BOOT = `try{
var K="openhouse_vibe",
A="sunday,mario,gameboy,terminal,amber,arcade,c64,vapor".split(","),
D="terminal,amber,arcade,c64,vapor".split(","),
P="mario,gameboy,arcade,c64,vapor".split(","),
v=localStorage.getItem(K);
if(!v&&localStorage.getItem("openhouse_mode")==="digital"){v="terminal";localStorage.setItem(K,v)}
if(v&&A.indexOf(v)>=0){var d=document.documentElement;
d.setAttribute("data-vibe",v);
d.setAttribute("data-mode",D.indexOf(v)>=0?"digital":"paper");
d.setAttribute("data-skin",P.indexOf(v)>=0?"pixel":"soft")}
}catch(e){}`.replace(/\n/g, '')

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    // Three attributes carry the vibe (see the VIBES section of globals.css):
    // data-mode is the field, data-skin the treatment, data-vibe the palette.
    // SUNDAY is the default; the blocking script below upgrades all three to
    // the saved vibe before the first paint on every route, so a GAMEBOY
    // visitor never sees a flash of pastel paper — not on the first page and
    // not on the next. The VIBE picker in components/vibe.tsx writes the key.
    <html lang="en" data-mode="paper" data-skin="soft" data-vibe="sunday" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: VIBE_BOOT }} />
      </head>
      <body suppressHydrationWarning>
        <Providers>
          {children}
        </Providers>
      </body>
    </html>
  )
}
