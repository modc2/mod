import type { Metadata } from "next";
import "./globals.css";
import { ThemeProvider, ThemeBoot } from "./context/ThemeContext";
import { FiltersProvider } from "./context/FiltersContext";
import { SidebarProvider } from "./context/SidebarContext";
import { CurrencyProvider } from "./context/CurrencyContext";
import SidebarShell from "./components/SidebarShell";
import SubnetTicker from "./components/SubnetTicker";
import TopBar from "./components/TopBar";
import BuildBadge from "./components/BuildBadge";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "copytensor — Bittensor dTAO copy trading",
  description:
    "Mirror top Bittensor validators' subnet allocations. Round-robin public RPCs, no third-party APIs.",
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
          <CurrencyProvider>
            <FiltersProvider>
              <SidebarProvider>
                <div className="crt-overlay" />
                <div className="crt-screen min-h-screen">
                  <SubnetTicker />
                  <TopBar />
                  <SidebarShell>
                    <main className="max-w-[1600px] mx-auto px-4 py-6">
                      {children}
                    </main>
                  </SidebarShell>
                  <BuildBadge />
                </div>
              </SidebarProvider>
            </FiltersProvider>
          </CurrencyProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
