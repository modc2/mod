"use client";

import { Suspense } from "react";
import TopBar from "../components/TopBar";
import CopyIndex from "../components/CopyIndex";
import { useUrlSync } from "../context/FiltersContext";

/// The STRAT page owns everything strat + account: the strat tabs
/// (STRAT / BACKTEST / LIVE) full width, each with a subtab rail
/// (STRAT → BUILD/SOURCE/MARKET, BACKTEST → RESULTS/TRADES, LIVE →
/// PORTFOLIO/POSITIONS/STATS/TRADES/WALLET/HELP). Strat
/// select/create lives in the TopBar picker, rename/delete in the
/// STRAT tab, and the go-live checklist in the LIVE tab — no side
/// column. Wallet/token/QR pairing, trading-wallet deposit/withdraw,
/// bridge funds in, and legacy V1 migration all live under the
/// LIVE → WALLET subtab (CopyIndex).
function StratsInner() {
  useUrlSync();

  return (
    <div className="max-w-[1920px] mx-auto">
      <TopBar showSearch={false} />
      <div className="p-4">
        <CopyIndex searchFilter="" />
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
