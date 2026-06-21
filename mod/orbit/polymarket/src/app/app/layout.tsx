import type { Metadata } from "next";
import "./globals.css";
import { AuthProvider } from "./context/AuthContext";
import { CopyEngineProvider } from "./context/CopyEngineContext";
import { FiltersProvider } from "./context/FiltersContext";
import { SidebarProvider } from "./context/SidebarContext";
import { SplitProvider } from "./context/SplitContext";
import { ThemeProvider, ThemeBoot } from "./context/ThemeContext";
import SidebarShell from "./components/SidebarShell";
import SplitShell from "./components/SplitShell";
import LeftNav from "./components/LeftNav";
import MarketTicker from "./components/MarketTicker";
import BuildBadge from "./components/BuildBadge";
import LiveAutoResume from "./components/LiveAutoResume";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "SUPER POLYMARKET BROS - Prediction Market Terminal",
  description: "Mario-themed black & white Polymarket trading terminal",
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
              <SidebarProvider>
                <SplitProvider>
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
                    {/* Global expandable left nav + page content. The rail is a
                        flex sibling so content reflows when it expands. */}
                    <div className="flex items-stretch">
                      <LeftNav />
                      <div className="flex-1 min-w-0">
                        <SplitShell>
                          <SidebarShell>
                            <main>{children}</main>
                          </SidebarShell>
                        </SplitShell>
                      </div>
                    </div>
                    <BuildBadge />
                  </div>
                </SplitProvider>
              </SidebarProvider>
            </FiltersProvider>
            </CopyEngineProvider>
          </AuthProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
