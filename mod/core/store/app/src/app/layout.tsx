import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "store — mod decentralized storage",
  description:
    "CID-agnostic decentralized storage: marketplace, timed sharing, pools, semantic search — Filecoin, Hippius, Lighthouse & localfs behind MetaMask sign-in",
};

// Applied before hydration so a light-mode user never sees a dark flash (and
// vice versa). Reads the persisted choice, falls back to the OS preference.
const themeInit = `(function(){try{var t=localStorage.getItem("store:theme");if(t!=="light"&&t!=="dark"){t=window.matchMedia&&window.matchMedia("(prefers-color-scheme: light)").matches?"light":"dark"}document.documentElement.dataset.theme=t}catch(e){}})()`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    // data-theme is owned exclusively by the inline script below — rendering it
    // from JSX too lets React reset the attribute after hydration (seen on the
    // statically prerendered /docs page). Script-less browsers get :root dark.
    <html lang="en" suppressHydrationWarning>
      <body>
        <script dangerouslySetInnerHTML={{ __html: themeInit }} />
        {children}
      </body>
    </html>
  );
}
