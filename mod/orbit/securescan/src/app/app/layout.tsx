import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "securescan — github vulnerability scanner",
  description: "Scan any GitHub repository for security vulnerabilities.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className="font-sans bg-bg text-text min-h-screen antialiased">
        {children}
      </body>
    </html>
  );
}
