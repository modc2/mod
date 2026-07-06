import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Uniswap Trader Scanner",
  description: "Multi-chain Uniswap V3 trader discovery and analytics",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="min-h-screen">{children}</body>
    </html>
  );
}
