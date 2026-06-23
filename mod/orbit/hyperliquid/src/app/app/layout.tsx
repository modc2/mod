import "./globals.css";
import type { Metadata } from "next";
import Header from "./components/Header";
import { WalletProvider } from "./lib/wallet";

export const metadata: Metadata = {
  title: "Hyperliquid · Copy, Strats & Vaults",
  description: "Copy top traders by ROI, build community strats, and invest in vaults on Hyperliquid",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="font-sans antialiased">
        <WalletProvider>
          <Header />
          <main className="max-w-7xl mx-auto px-4 py-8 animate-fadeUp">{children}</main>
        </WalletProvider>
      </body>
    </html>
  );
}
