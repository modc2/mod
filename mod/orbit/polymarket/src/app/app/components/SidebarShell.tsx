"use client";

import { ReactNode } from "react";
import { useSidebar, SIDEBAR_DEFAULT } from "../context/SidebarContext";
import { useAuth } from "../context/AuthContext";
import WalletChip from "./WalletChip";
import WalletTokenPanel from "./WalletTokenPanel";
import WalletPanel from "./WalletPanel";
import WalletFundingPanel from "./WalletFundingPanel";
import PolymarketAccountPanel from "./PolymarketAccountPanel";
import PreconditionChecklist from "./PreconditionChecklist";

/// Account sidebar that owns ALL account chrome: signed-in wallet
/// (WalletChip), wallet/token/QR pairing (WalletTokenPanel), trading-wallet
/// deposit/withdraw (WalletPanel), bridge-funds-in (WalletFundingPanel),
/// legacy V1 proxy migration (PolymarketAccountPanel — self-hides when
/// empty), and the go-live CHECKLIST. This is the ONLY place any of that
/// renders — the main column (CopyIndex/LivePanel) stays to strat tabs and
/// their content, nothing duplicated. Always mounted; `collapsed` only
/// swaps the full panel for a thin icon rail, it never disappears entirely.
/// Lives on the LEFT, immediately after LeftNav — the app only chromes one
/// edge of the screen, keeping the right side free for content.
export default function SidebarShell({ children }: { children: ReactNode }) {
  const { collapsed, width, hydrated, setWidth, setCollapsed, startDrag } = useSidebar();
  const { auth } = useAuth();

  if (!hydrated) return <>{children}</>;

  const resetWidth = () => setWidth(SIDEBAR_DEFAULT);

  if (collapsed) {
    // Mirrors WalletChip's dot semantics without mounting the full chip
    // (too wide for a 40px rail): gray = disconnected, amber = connected
    // but not CLOB-authed, green = ready to trade.
    const dotColor = !auth.connected
      ? "bg-pixel-gray"
      : auth.authenticated
        ? "bg-green-400"
        : "bg-amber-400";
    return (
      <div className="flex items-stretch min-h-[calc(100vh-3rem)]">
        <aside className="shrink-0 sticky top-12 self-start p-2 pr-0">
          <div className="pixel-panel u-lift flex flex-col items-center py-2.5 px-1.5 gap-2.5">
            <button
              onClick={() => setCollapsed(false)}
              title="Expand account panel"
              className="text-pixel-gray hover:text-green-400 text-[15px] leading-none"
            >
              »
            </button>
            <button
              onClick={() => setCollapsed(false)}
              title={auth.connected ? "Account connected — click to expand" : "Not connected — click to expand"}
              className="w-2.5 h-2.5 rounded-full"
            >
              <span className={`block w-2.5 h-2.5 rounded-full ${dotColor}`} />
            </button>
            <div className="[writing-mode:vertical-rl] text-[9.5px] tracking-[0.24em] text-pixel-gray">
              ACCOUNT
            </div>
          </div>
        </aside>
        <main className="flex-1 min-w-0 overflow-x-hidden">{children}</main>
      </div>
    );
  }

  return (
    <div className="flex items-stretch min-h-[calc(100vh-3rem)]">
      {/* Floating glass card that hugs its content — the page canvas (and its
          aurora) stays visible below it instead of a full-height black wall. */}
      <aside
        style={{ width }}
        className="shrink-0 sticky top-12 self-start max-h-[calc(100vh-3rem)] overflow-y-auto p-3 pr-0"
      >
        <div className="pixel-panel overflow-hidden">
          {/* Account row: signed-in wallet, CLOB status dot, switch + sign-out,
              add-person. Header carries a collapse button (rail mode, not a
              full hide — there's no main-column fallback for any of this). */}
          <div className="flex items-center justify-between gap-2 px-3 py-2.5 border-b border-[var(--border)] bg-[rgb(var(--pixel-white-rgb)/0.02)]">
            <span className="text-[10px] text-pixel-gray tracking-[0.22em] font-semibold">ACCOUNT</span>
            <div className="flex items-center gap-2">
              <WalletChip />
              <button
                onClick={() => setCollapsed(true)}
                className="grid place-items-center w-7 h-7 rounded-[var(--radius-sm)] text-[14px] text-pixel-gray hover:text-pixel-white hover:bg-pixel-white/[0.06] transition-colors shrink-0"
                title="Collapse to rail"
              >
                «
              </button>
            </div>
          </div>
          <div className="p-2 space-y-2">
            {/* Full wallet + token + sign-in-QR pairing panel. */}
            <WalletTokenPanel />
            {/* Trading-wallet deposit/withdraw (V2) — the LIVE tab's "FUND NOW"
                banner scrolls to this id. */}
            <div id="sidebar-wallet-panel">
              <WalletPanel />
            </div>
            {/* Bridge / send funds into Polygon USDC from any chain. */}
            <WalletFundingPanel />
            {/* Legacy V1 Safe — only renders once there's a leftover balance. */}
            <PolymarketAccountPanel />
            {/* Go-live preflight: WALLET · CLOB · STRATEGY · TRADERS · INTERVAL ·
                CAPITAL with a progress bar. */}
            <PreconditionChecklist />
          </div>
        </div>
      </aside>
      {/* Resize handle — invisible until hovered so it never reads as a wall. */}
      <div
        onMouseDown={startDrag}
        onDoubleClick={resetWidth}
        className="group w-3 shrink-0 cursor-col-resize flex justify-center"
        role="separator"
        aria-orientation="vertical"
        title="Drag to resize · Double-click to reset"
      >
        <div className="w-px h-full bg-transparent group-hover:bg-[var(--border-strong)] group-active:bg-[rgb(var(--accent)/0.5)] transition-colors" />
      </div>
      <main className="flex-1 min-w-0 overflow-x-hidden">{children}</main>
    </div>
  );
}
