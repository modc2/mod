import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "NearTensor",
  description: "Bittensor-inspired subnet protocol on NEAR",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className="min-h-screen bg-nt-bg text-nt-text font-mono antialiased">
        {children}
      </body>
    </html>
  );
}
