import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "MCP Hub",
  description:
    "A hub of MCP servers — the open-source Model Context Protocol ecosystem in one searchable directory, with live tool probes and wallet-signed publishing.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    // The theme is read from storage and applied before paint by the inline
    // script below; setting it here too would hydrate-mismatch (see the store
    // module's html-attr gotcha).
    <html lang="en" suppressHydrationWarning>
      <head>
        <script
          dangerouslySetInnerHTML={{
            __html: `try{var t=localStorage.getItem('mcp:theme');if(t)document.documentElement.setAttribute('data-theme',t);}catch(e){}`,
          }}
        />
      </head>
      <body>{children}</body>
    </html>
  );
}
