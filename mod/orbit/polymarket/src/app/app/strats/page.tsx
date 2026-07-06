"use client";

import { Suspense } from "react";
import TopBar from "../components/TopBar";
import CopyIndex from "../components/CopyIndex";
import StratSidebar from "../components/StratSidebar";
import WalletTokenPanel from "../components/WalletTokenPanel";
import WalletPanel from "../components/WalletPanel";
import WalletFundingPanel from "../components/WalletFundingPanel";
import PolymarketAccountPanel from "../components/PolymarketAccountPanel";
import PreconditionChecklist from "../components/PreconditionChecklist";
import { useFilters, useUrlSync } from "../context/FiltersContext";

/// The STRAT page owns everything strat + account: the strat tabs
/// (STRAT / HUB / BACKTEST / LIVE) in the main column, and the account
/// column on the right — strat list (select / rename / delete / new),
/// wallet/token/QR pairing, trading-wallet deposit/withdraw, bridge
/// funds in, legacy V1 migration (self-hides when empty), and the
/// go-live checklist. There is no global account sidebar; this column
/// only exists here.
function AccountColumn() {
  return (
    <aside className="w-full lg:w-[300px] shrink-0 lg:sticky lg:top-14 lg:max-h-[calc(100vh-4rem)] lg:overflow-y-auto space-y-2">
      <div className="pixel-panel p-2 space-y-2">
        <div className="px-1 text-[10px] text-pixel-gray tracking-[0.22em]">STRATS</div>
        <StratSidebar />
      </div>
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
