import "./globals.css";
import type { Metadata } from "next";
import Header from "./components/Header";
import TickerTape from "./components/TickerTape";
import { WalletProvider } from "./lib/wallet";

export const metadata: Metadata = {
  title: "Hyperliquid · Copy, Strats & Vaults",
  description: "Copy top traders by ROI, build community strats, and invest in vaults on Hyperliquid",
};

// Runs before first paint: apply the persisted theme (or OS preference) so
// there is no flash of the wrong mode. Storage reads are try/caught — the
// shared modc2 localStorage origin can be full or blocked.
const THEME_INIT = `(function(){var t;try{t=localStorage.getItem("hl.theme")}catch(e){}
if(t!=="light"&&t!=="dark"){try{t=window.matchMedia("(prefers-color-scheme: light)").matches?"light":"dark"}catch(e){t="dark"}}
document.documentElement.classList.add(t);})();`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT }} />
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
