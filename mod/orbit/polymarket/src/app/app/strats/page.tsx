"use client";

import { Suspense } from "react";
import TopBar from "../components/TopBar";
import CopyIndex from "../components/CopyIndex";
import { useFilters, useUrlSync } from "../context/FiltersContext";

/// The STRAT page owns everything strat + account: the strat tabs
/// (STRAT / HUB / BACKTEST / LIVE / WALLET) full width. Strat
/// select/create lives in the TopBar picker, rename/delete in the
/// STRAT tab, and the go-live checklist in the LIVE tab — no side
/// column. Wallet/token/QR pairing, trading-wallet deposit/withdraw,
/// bridge funds in, and legacy V1 migration all live inside the
/// WALLET tab (CopyIndex).
function StratsInner() {
  useUrlSync();
  const { search } = useFilters();

  return (
    <div className="max-w-[1920px] mx-auto">
      <TopBar searchPlaceholder="FILTER INDEX BY MARKET..." />
      <div className="p-4">
        <CopyIndex searchFilter={search} />
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
