"use client";

import { Suspense } from "react";
import TopBar from "../components/TopBar";
import CopyIndex from "../components/CopyIndex";
import StratSidebar from "../components/StratSidebar";
import PreconditionChecklist from "../components/PreconditionChecklist";
import { useFilters, useUrlSync } from "../context/FiltersContext";

/// The STRAT page owns everything strat + account: the strat tabs
/// (STRAT / HUB / BACKTEST / LIVE / WALLET) in the main column, and a
/// slim column on the right — the strat list (select / rename /
/// delete / new) and the go-live checklist. Wallet/token/QR pairing,
/// trading-wallet deposit/withdraw, bridge funds in, and legacy V1
/// migration all live inside the WALLET tab (CopyIndex).
function AccountColumn() {
  return (
    <aside className="w-full lg:w-[300px] shrink-0 lg:sticky lg:top-14 lg:max-h-[calc(100vh-4rem)] lg:overflow-y-auto space-y-2">
      <div className="pixel-panel p-2 space-y-2">
        <div className="px-1 text-[10px] text-pixel-gray tracking-[0.22em]">STRATS</div>
        <StratSidebar />
      </div>
      {/* Go-live preflight: WALLET · CLOB · STRATEGY · TRADERS · INTERVAL ·
          CAPITAL with a progress bar. */}
      <PreconditionChecklist />
    </aside>
  );
}

function StratsInner() {
  useUrlSync();
  const { search } = useFilters();

  return (
    <div className="max-w-[1920px] mx-auto">
      <TopBar searchPlaceholder="FILTER INDEX BY MARKET..." />
      <div className="p-4 flex flex-col lg:flex-row items-start gap-4">
        <div className="flex-1 min-w-0 w-full">
          <CopyIndex searchFilter={search} />
        </div>
        <AccountColumn />
      </div>
    </div>
  );
}

export default function StratsPage() {
  return (
    <Suspense>
      <StratsInner />
    </Suspense>
  );
}
