import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Dev ✦ Orbit Console",
  description: "Programmable AI developer console — powered by Claude",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  maximumScale: 5,
  viewportFit: "cover",
  themeColor: "#5c94fc",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    // data-theme is rendered server-side (mario default — it's-a World 1-1)
    // and corrected for any saved theme by the blocking script below, so the
    // first paint already has the final palette — no theme flip after
    // hydration. The id list here must track THEMES in page.tsx (light-base
    // ids in the L set drive data-base).
    <html lang="en" data-theme="mario" data-base="light" suppressHydrationWarning>
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
        <script
          dangerouslySetInnerHTML={{
            __html:
              'try{var t=localStorage.getItem("build_jobs_theme"),A=["dark","light","matrix","neon","ember","abyss","paper","win95","mario","warp"],L=["light","paper","win95","mario"];if(t&&A.indexOf(t)>=0){var d=document.documentElement;d.setAttribute("data-theme",t);d.setAttribute("data-base",L.indexOf(t)>=0?"light":"dark")}}catch(e){}',
          }}
        />
      </head>
      <body>
        {children}
      </body>
    </html>
  );
}
