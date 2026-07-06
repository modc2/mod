import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "store — mod decentralized storage",
  description: "Filecoin + Hippius unified storage with MetaMask SIWE auth",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
