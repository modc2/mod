"use client";

import { ReactNode } from "react";
import { useSidebar, SIDEBAR_DEFAULT } from "../context/SidebarContext";
import { useFilters } from "../context/FiltersContext";
import CopyIndex from "./CopyIndex";
import WalletChip from "./WalletChip";

export default function SidebarShell({ children }: { children: ReactNode }) {
  const { docked, width, hydrated, setWidth, setDocked, startDrag } = useSidebar();
  const { search } = useFilters();

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
        className="shrink-0 border-l-2 border-pixel-border bg-pixel-black/60 overflow-y-auto"
      >
        <div className="p-2 space-y-2">
          {/* User / wallet info lives here (moved out of the top bar): signed-in
              account, CLOB status, wallet switch + sign-out, add-person. */}
          <div className="flex items-center justify-between gap-2 pb-2 border-b border-pixel-border/60">
            <span className="text-[10px] text-pixel-gray tracking-[0.18em]">ACCOUNT</span>
            <WalletChip />
          </div>
          <CopyIndex searchFilter={search} compact onClose={() => setDocked(false)} />
        </div>
      </aside>
    </div>
  );
}
