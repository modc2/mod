import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Mod AI",
  description: "Background AI job runner — powered by Claude",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>
        {children}
      </body>
    </html>
  );
}
