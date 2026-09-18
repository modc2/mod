import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "DeFi ✦ Modular Protocol Composer",
  description:
    "Compose Ethereum DeFi protocols out of reusable Solidity blocks — wire typed ports, type-check the graph, compile with solc, deploy from your wallet.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
