import type { Metadata } from "next";
import "katex/dist/katex.min.css";
import "./globals.css";

export const metadata: Metadata = {
  title: "MOD Whitepaper — Scaling Off-Chain Open-Source Management",
  description:
    "Whitepaper: scaling off-chain open-source management by storing the tree, not the leaves.",
};

// Run before React hydration so we don't flash the wrong theme.
// Default = dark; localStorage override wins if the user has toggled.
const themeBootstrap = `
try {
  var saved = localStorage.getItem('whitepaper-theme');
  var dark = saved ? saved === 'dark' : true;
  if (dark) document.documentElement.classList.add('dark');
} catch (e) {
  document.documentElement.classList.add('dark');
}
`;

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeBootstrap }} />
      </head>
      <body className="min-h-screen font-serif bg-paper text-ink">
        {children}
      </body>
    </html>
  );
}
