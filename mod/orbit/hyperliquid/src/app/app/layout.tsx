import "./globals.css";
import type { Metadata } from "next";
import Header from "./components/Header";
import TickerTape from "./components/TickerTape";
import { WalletProvider } from "./lib/wallet";
import { SessionProvider } from "./lib/auth";
import { DockProvider } from "./lib/dock";
import Dock from "./components/Dock";
import { themeBootScript } from "./lib/themes";

export const metadata: Metadata = {
  title: "Hyperliquid · Copy, Strats & Vaults",
  description: "Copy top traders by ROI, build community strats, and invest in vaults on Hyperliquid",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    // Server-rendered as the Midnight default; the boot script below corrects
    // both attributes for a saved theme before first paint, which is why the
    // hydration warning is suppressed here.
    <html lang="en" data-theme="dark" data-base="dark" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeBootScript() }} />
      </head>
      <body className="font-sans antialiased">
        <WalletProvider>
          {/* Wallet = which account is attached. Session = whether it can
              write. Everything downstream asks the second question. */}
          <SessionProvider>
            {/* The desk (agent · strats · wallet) is a sibling of the page:
                it docks a column beside it at wide widths and slides over it
                below that — see `data-dock` in globals.css. */}
            <DockProvider>
              <Header />
              <TickerTape />
              <main className="max-w-7xl mx-auto px-4 py-8 animate-fadeUp">{children}</main>
              <Dock />
            </DockProvider>
          </SessionProvider>
        </WalletProvider>
      </body>
    </html>
  );
}
