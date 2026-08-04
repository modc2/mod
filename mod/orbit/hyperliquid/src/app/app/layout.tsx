import "./globals.css";
import type { Metadata } from "next";
import Header from "./components/Header";
import TickerTape from "./components/TickerTape";
import { WalletProvider } from "./lib/wallet";
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
          <Header />
          <TickerTape />
          <main className="max-w-7xl mx-auto px-4 py-8 animate-fadeUp">{children}</main>
        </WalletProvider>
      </body>
    </html>
  );
}
