import type { Metadata } from "next";
import "./globals.css";
import { ThemeProvider, ThemeBoot } from "./context/ThemeContext";
import { AuthProvider } from "./context/AuthContext";
import TopBar from "./components/TopBar";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "liquidai — every LFM, wherever it runs",
  description:
    "Catalog and run every Liquid AI model: in your browser on WebGPU, on this box, or in Liquid's cloud.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    // The default skin is server-rendered and corrected to whatever the
    // visitor last picked by ThemeBoot, before paint — the first frame is
    // already the right cabinet. `suppressHydrationWarning` because that
    // script legitimately edits these two attributes.
    <html lang="en" data-theme="dark" data-base="dark" suppressHydrationWarning>
      <head>
        <ThemeBoot />
      </head>
      <body className="font-pixel antialiased bg-pixel-bg text-pixel-white min-h-screen">
        <ThemeProvider>
          <AuthProvider>
            <div className="crt-overlay" />
            <div className="crt-screen min-h-screen flex flex-col">
              <TopBar />
              {/* Full-bleed to 1800px with tight gutters. The board is a
                  console, not an article: the content runs to the edges and
                  the panels do the dividing. */}
              <main className="w-full max-w-[1800px] mx-auto px-2 py-2 sm:px-3 sm:py-3 flex-1 flex flex-col min-h-0">
                {children}
              </main>
            </div>
          </AuthProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
