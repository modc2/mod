import type { Metadata } from "next";
import "./globals.css";
import { AuthProvider } from "./context/AuthContext";
import { CopyEngineProvider } from "./context/CopyEngineContext";
import { FiltersProvider } from "./context/FiltersContext";
import { ThemeProvider, ThemeBoot } from "./context/ThemeContext";
import BuildBadge from "./components/BuildBadge";
import LiveAutoResume from "./components/LiveAutoResume";
import AccessGate from "./components/AccessGate";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Polymarket - Prediction Market Terminal",
  description: "Copy-trading and market data terminal for Polymarket prediction markets",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    // data-theme/data-base are server-rendered as the MIDNIGHT default and
    // corrected by ThemeBoot below for any saved theme, so first paint
    // already has the final palette. suppressHydrationWarning because that
    // script mutates these attributes before React ever looks at them.
    <html lang="en" data-theme="dark" data-base="dark" suppressHydrationWarning>
      <head>
        {/* Stamp the saved theme before React hydrates so returning users
            don't see a flash of the default palette on first paint. */}
        <ThemeBoot />
        {/* Deploy self-healing: the app is rebuilt+restarted in place, so a
            page loaded just before (or held open across) a deploy references
            _next/static chunks that no longer exist — every script 400s, the
            page never hydrates, and no click works. Inline (not a chunk) so
            it runs even when zero bundles load: on a failed _next script/css
            load, reload once, rate-limited so a deploy window can't loop. */}
        <script
          dangerouslySetInnerHTML={{
            __html: `(function(){var RL="pm.chunkReload";function stale(){try{var t=+sessionStorage.getItem(RL)||0;if(Date.now()-t<20000)return true;sessionStorage.setItem(RL,String(Date.now()))}catch(e){}return false}function reload(){if(!stale())setTimeout(function(){location.reload()},1500)}addEventListener("error",function(e){var el=e.target;if(el&&(el.tagName==="SCRIPT"||el.tagName==="LINK")){var u=el.src||el.href||"";if(u.indexOf("/_next/")!==-1)reload()}},true);addEventListener("unhandledrejection",function(e){var m=e.reason&&(e.reason.name+" "+e.reason.message)||"";if(m.indexOf("ChunkLoadError")!==-1||m.indexOf("dynamically imported module")!==-1)reload()})})()`,
          }}
        />
      </head>
      <body className="font-pixel antialiased bg-pixel-bg text-pixel-white min-h-screen">
        <ThemeProvider>
          <AuthProvider>
            <CopyEngineProvider>
            <FiltersProvider>
              {/* Auto-restart the copy engine if the user reloaded the
                  page while a live session was running. Reads the
                  poly_live_session localStorage record + AuthContext's
                  rehydrated CLOB creds; no-op if either is missing.
                  Explicit STOP clears the record, so accidental reloads
                  auto-resume but deliberate stops stay stopped. */}
              <div className="crt-overlay" />
              {/* The whole console insets when the account/strat sidebar is
                  docked open (--strat-dock, set by StratSidebar) — header
                  included, so the header cluster that owns it (top-RIGHT)
                  never slides under it. */}
              <div
                className="crt-screen dock-inset min-h-screen"
                style={{ paddingRight: "var(--strat-dock, 0px)" }}
              >
                {/* Owner-only gate: the API 401s everything until the sudo
                    address signs the terms-acceptance challenge, so the
                    whole console (panels, auto-resume) waits behind
                    AccessGate instead of rendering dead 401'd chrome. */}
                <AccessGate>
                  <LiveAutoResume />
                  {/* No sidebars: global nav is a dropdown in each page's
                      TopBar, and wallet chrome is a WALLET tab inside the
                      STRAT page. Content gets the full viewport.
                      `pb-14` reserves a lane for the fixed BuildBadge —
                      without it the badge sits on top of the last table row
                      (and its +ADD button) on every long page. */}
                  <main className="min-w-0 pb-14">{children}</main>
                  <BuildBadge />
                </AccessGate>
              </div>
            </FiltersProvider>
            </CopyEngineProvider>
          </AuthProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
