import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "venice — AI gateway",
  description: "Venice AI: bring your own key, or pay per message (x402). Wallet auth.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
