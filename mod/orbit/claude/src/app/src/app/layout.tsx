import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Claude ✦ Orbit Console",
  description: "Programmable AI developer console — powered by Claude",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  maximumScale: 5,
  viewportFit: "cover",
  themeColor: "#07070d",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    // data-theme is rendered server-side (dark default) and corrected for a
    // saved light preference by the blocking script below, so the first paint
    // already has the final palette — no theme flip after hydration.
    <html lang="en" data-theme="dark" suppressHydrationWarning>
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
              'try{if(localStorage.getItem("claude_jobs_theme")==="light")document.documentElement.setAttribute("data-theme","light")}catch(e){}',
          }}
        />
      </head>
      <body>
        {children}
      </body>
    </html>
  );
}
