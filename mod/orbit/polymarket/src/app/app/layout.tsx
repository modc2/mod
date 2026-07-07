import type { Metadata } from "next";
import "./globals.css";
import { AuthProvider } from "./context/AuthContext";
import { CopyEngineProvider } from "./context/CopyEngineContext";
import { FiltersProvider } from "./context/FiltersContext";
import { ThemeProvider, ThemeBoot } from "./context/ThemeContext";
import MarketTicker from "./components/MarketTicker";
import BuildBadge from "./components/BuildBadge";
import LiveAutoResume from "./components/LiveAutoResume";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Polymarket - Prediction Market Terminal",
  description: "Copy-trading and market data terminal for Polymarket prediction markets",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <head>
        {/* Stamp data-theme="light" before React hydrates so light-mode
            users don't see a dark-mode flash on first paint. */}
        <ThemeBoot />
      </head>
      <body className="font-pixel antialiased bg-pixel-bg text-pixel-white min-h-screen">
        <ThemeProvider>
          <AuthProvider>
            <CopyEngineProvider>
            <FiltersProvider>
              {/* Auto-restart the copy engine if the user reloaded the
                  page while a live session was running. Reads the
                  poly_live_session localStorage record + AuthContext's
                  rehydrated CLOB creds; no-op if either is missing.
                  Explicit STOP clears the record, so accidental reloads
                  auto-resume but deliberate stops stay stopped. */}
              <LiveAutoResume />
              <div className="crt-overlay" />
              <div className="crt-screen min-h-screen">
                <MarketTicker />
                {/* No sidebars: global nav is a dropdown in each page's
                    TopBar, and wallet chrome is a WALLET tab inside the
                    STRAT page. Content gets the full viewport. */}
                <main className="min-w-0">{children}</main>
                <BuildBadge />
              </div>
            </FiltersProvider>
            </CopyEngineProvider>
          </AuthProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
