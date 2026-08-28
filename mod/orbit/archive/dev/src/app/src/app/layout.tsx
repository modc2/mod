import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "dev — multi-provider LLM gateway",
  description:
    "One sleek console for every OpenAI-compatible model provider — Venice, OpenRouter, OpenAI, Claude, and any you add. BYOK or backend key, streamed token-by-token. Wallet auth.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link
          href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap"
          rel="stylesheet"
        />
      </head>
      <body>{children}</body>
    </html>
  );
}
