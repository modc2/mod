import "./globals.css";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "model gateway",
  description: "BYOK chat — openrouter, grok via chutes/targon/venice — gated by ModelGate.sol",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
