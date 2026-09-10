import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "store — mod decentralized storage",
  description:
    "CID-agnostic decentralized storage: marketplace, timed sharing, pools, semantic search — Filecoin, Hippius, Lighthouse & localfs behind MetaMask sign-in",
};

// Applied before hydration so a light-mode user never sees a dark flash (and
// vice versa). Reads the persisted choice, migrates the two pre-multi-theme
// values, falls back to the OS preference. Ids must match src/lib/theme.ts.
const themeInit = `(function(){try{
var ids=["8bit-underground","8bit-overworld","crt-green","crt-amber","soft-midnight","soft-porcelain","neon-synthwave","print-broadsheet","blueprint-draft"];
var light=window.matchMedia&&window.matchMedia("(prefers-color-scheme: light)").matches;
var t=localStorage.getItem("store:theme");
if(t==="light")t="8bit-overworld";
if(t==="dark")t="8bit-underground";
if(ids.indexOf(t)<0)t=light?"8bit-overworld":"8bit-underground";
var d=document.documentElement;d.dataset.theme=t;d.dataset.skin=t.split("-")[0];
}catch(e){}})()`;

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
