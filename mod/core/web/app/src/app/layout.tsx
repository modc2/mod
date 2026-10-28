import type { Metadata, Viewport } from "next";
import "./globals.css";
import { WalletProvider } from "@/lib/wallet";

export const metadata: Metadata = {
  title: "mod — apps and services, running right now",
  description:
    "Browse every module in the mod ecosystem: open live apps in your browser, read how they are built, and back the ones you care about on-chain.",
  applicationName: "mod",
  metadataBase: new URL("https://modc2.com"),
  openGraph: {
    title: "mod — apps and services, running right now",
    description:
      "Open live apps, read how they are built, and back the ones you care about on-chain.",
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
