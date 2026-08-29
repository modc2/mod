import "./globals.css";
import { JetBrains_Mono } from "next/font/google";
import type { Metadata } from "next";

const mono = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-mono",
});

export const metadata: Metadata = {
  title: "Compute",
  description: "Rent and manage compute instances with on-chain billing",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="dark">
      <body className={`${mono.variable} font-mono antialiased`}>
        {children}
      </body>
    </html>
  );
}
