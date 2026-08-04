import type { Metadata, Viewport } from 'next'
import './globals.css'

export const metadata: Metadata = {
  title: 'Agent — Mod Agent OS',
  description: 'Autonomous coding agent OS — tools, agents, chains, and a shared library market.',
}

export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  viewportFit: 'cover', // the console dock hugs the home-bar notch
  themeColor: '#0a0a0a',
}

/* The saved theme has to land on <html> before the first paint or the page
 * flashes ARCADE and then swaps. The default is server-rendered and this
 * blocking script corrects it from localStorage — so keep the table below in
 * step with THEMES in components/Theme.tsx. */
const THEME_BOOT = `try{
var T={arcade:["pixel","dark"],matrix:["pixel","dark"],win95:["pixel","light"],
midnight:["soft","dark"],abyss:["soft","dark"],ember:["soft","dark"],neon:["soft","dark"],
paper:["soft","light"],daylight:["soft","light"]};
var t=localStorage.getItem("agent_theme");
if(t&&T[t]){var d=document.documentElement;d.setAttribute("data-theme",t);
d.setAttribute("data-skin",T[t][0]);d.setAttribute("data-base",T[t][1])}
}catch(e){}`

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" data-theme="arcade" data-skin="pixel" data-base="dark" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOT }} />
      </head>
      <body>{children}</body>
    </html>
  )
}
