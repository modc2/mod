"use client";

import { ReactNode } from "react";
import { useSidebar, SIDEBAR_DEFAULT } from "../context/SidebarContext";
import WalletChip from "./WalletChip";
import WalletTokenPanel from "./WalletTokenPanel";
import PreconditionChecklist from "./PreconditionChecklist";

/// Right-docked sidebar that owns the "who am I / am I ready" block:
/// the signed-in account (WalletChip), the full wallet + token + QR pairing
/// panel (WalletTokenPanel), and the go-live CHECKLIST (PreconditionChecklist).
/// These used to sit in a wide top bar above the strats page; pinning them to
/// the side keeps them visible while the main column scrolls the trade feed.
///
/// When undocked it renders children verbatim (zero layout impact). The
/// docked/undocked toggle + persisted width live in SidebarContext.
export default function SidebarShell({ children }: { children: ReactNode }) {
  const { docked, width, hydrated, setWidth, setDocked, startDrag } = useSidebar();

  if (!hydrated) return <>{children}</>;
  if (!docked) return <>{children}</>;

  const resetWidth = () => setWidth(SIDEBAR_DEFAULT);

  return (
    <div className="flex items-stretch min-h-[calc(100vh-3rem)]">
      <main className="flex-1 min-w-0 overflow-x-hidden">{children}</main>
      <div
        onMouseDown={startDrag}
        onDoubleClick={resetWidth}
        className="w-1.5 shrink-0 bg-pixel-border hover:bg-pixel-white/40 active:bg-pixel-white/60 cursor-col-resize transition-colors"
        role="separator"
        aria-orientation="vertical"
        title="Drag to resize · Double-click to reset"
      />
      <aside
        style={{ width }}
        className="shrink-0 border-l-2 border-pixel-border bg-pixel-black/60 overflow-y-auto sticky top-12 self-start max-h-[calc(100vh-3rem)]"
      >
        <div className="p-2 space-y-2">
          {/* Account row: signed-in wallet, CLOB status dot, switch + sign-out,
              add-person. Header carries a close button that undocks. */}
          <div className="flex items-center justify-between gap-2 pb-2 border-b border-pixel-border/60">
            <span className="text-[10px] text-pixel-gray tracking-[0.18em]">ACCOUNT</span>
            <div className="flex items-center gap-2">
              <WalletChip />
              <button
                onClick={() => setDocked(false)}
                className="pixel-btn text-[13px] px-2 py-0.5 border-pixel-border text-pixel-gray hover:text-pixel-white hover:border-pixel-white shrink-0"
                title="Hide sidebar"
              >
                X
              </button>
            </div>
          </div>
          {/* Full wallet + token + sign-in-QR pairing panel. */}
          <WalletTokenPanel />
          {/* Go-live preflight: WALLET · CLOB · STRATEGY · TRADERS · INTERVAL ·
              CAPITAL with a progress bar. */}
          <PreconditionChecklist />
        </div>
      </aside>
    </div>
  );
}
