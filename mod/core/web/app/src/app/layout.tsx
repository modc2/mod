import type { Metadata, Viewport } from "next";
import "./globals.css";
import { WalletProvider } from "@/lib/wallet";

export const metadata: Metadata = {
  title: "mod — a modular runtime for on-chain software",
  description:
    "Write a module. Register it on-chain. Set a price. Get paid every time someone calls it. Browse the live mod ecosystem.",
  applicationName: "mod",
  metadataBase: new URL("https://modc2.com"),
  openGraph: {
    title: "mod — a modular runtime for on-chain software",
    description:
      "Write a module. Register it on-chain. Get paid every time someone calls it.",
    type: "website",
  },
};

export const viewport: Viewport = {
  themeColor: "#06070a",
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>
        {/* One wallet connection for the whole explorer: the header chip, the
            BlocTime console and every module's backing panel read it. */}
        <WalletProvider>{children}</WalletProvider>
      </body>
    </html>
  );
}
