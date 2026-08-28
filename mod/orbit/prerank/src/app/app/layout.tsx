import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "prerank — a daily market on which model is on top",
  description:
    "A sealed-bid, once-a-day prediction market over model ranks, where the house's margin on early usage is handed back as a position in the model you used.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
