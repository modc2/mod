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
