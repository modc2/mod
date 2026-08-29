import type { Metadata, Viewport } from "next";
import "./globals.css";
import Tooltip from "../components/Tooltip";

export const metadata: Metadata = {
  title: "Build ✦ Orbit Console",
  description: "Programmable AI developer console — powered by Claude",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  maximumScale: 5,
  viewportFit: "cover",
  themeColor: "#9bbc0f",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    // data-theme is rendered server-side (gameboy default — DMG-01) and
    // corrected for any saved theme by the blocking script below, so the
    // first paint already has the final palette — no theme flip after
    // hydration. The id lists here must track THEMES in page.tsx: light-base
    // ids in L drive data-base, square-corner ids in P drive data-skin.
    // data-font is the same story for the typeface axis (FONTS in page.tsx).
    <html lang="en" data-theme="gameboy" data-base="light" data-skin="pixel" data-font="default" suppressHydrationWarning>
      <head>
        {/* globals.css styles the whole console in Inter + JetBrains Mono;
            without these links neither ever loads and every machine falls
            back to its system fonts. */}
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link
          href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:ital,wght@0,400;0,500;0,600;0,700;1,400&display=swap"
          rel="stylesheet"
        />
        {/* Profile isolation — ?profile=<id> namespaces ALL web storage for
            this instance, so a Desk pane (or second tab) loaded with a
            profile is a fully separate app: its own session token, guest
            wallet seed, theme — sign into a different account per pane.
            Must run before ANY other script that touches storage (the theme
            reader below included). key()/length stay raw, so the quota
            evictor in page.tsx is a harmless no-op under a profile. */}
        <script
          dangerouslySetInnerHTML={{
            __html:
              'try{var q=/[?&]profile=([A-Za-z0-9_-]{1,32})/.exec(location.search);if(q&&q[1]&&q[1]!=="default"){var P="profile."+q[1]+"::";var S=Storage.prototype,G=S.getItem,T=S.setItem,R=S.removeItem;S.getItem=function(k){return G.call(this,P+k)};S.setItem=function(k,v){return T.call(this,P+k,v)};S.removeItem=function(k){return R.call(this,P+k)};window.__modProfile=q[1]}}catch(e){}',
          }}
        />
        <script
          dangerouslySetInnerHTML={{
            __html:
              'try{var d=document.documentElement,t=localStorage.getItem("buildfork_jobs_theme"),A=["dark","light","matrix","neon","ember","abyss","paper","win95","mario","warp","gameboy","drive","vapor","disco","babe","surf"],L=["light","paper","win95","mario","gameboy","surf"],P=["gameboy","mario","warp","win95"];if(t&&A.indexOf(t)>=0){d.setAttribute("data-theme",t);d.setAttribute("data-base",L.indexOf(t)>=0?"light":"dark");d.setAttribute("data-skin",P.indexOf(t)>=0?"pixel":"soft")}var f=localStorage.getItem("buildfork_jobs_font");if(f&&["default","zoodmantra","antireal"].indexOf(f)>=0)d.setAttribute("data-font",f)}catch(e){}',
          }}
        />
        {/* Deploy-skew recovery. A rebuild landing mid-load can 404 this
            page's stylesheet or a chunk; the console then paints as raw
            unstyled HTML (classes gone, only inline styles left). Catch the
            failed asset load and reload once — the fresh html points at the
            new build. Rate-limited to one reload per 30s so a genuinely
            missing file degrades instead of looping. */}
        <script
          dangerouslySetInnerHTML={{
            __html:
              'try{var K="buildfork_asset_reload_at";addEventListener("error",function(e){var t=e.target;if(!t||!t.tagName)return;var u=t.href||t.src||"";if(u.indexOf("/_next/static/")<0)return;var last=+(sessionStorage.getItem(K)||0);if(Date.now()-last<30000)return;sessionStorage.setItem(K,String(Date.now()));location.reload()},true)}catch(e){}',
          }}
        />
      </head>
      <body>
        {children}
        {/* One themed bubble for every `title=` in the console — mounted here
            so it covers the auth page and the Desk panes too. */}
        <Tooltip />
      </body>
    </html>
  );
}
