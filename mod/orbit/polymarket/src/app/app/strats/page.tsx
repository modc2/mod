"use client";

import { Suspense } from "react";
import TopBar from "../components/TopBar";
import CopyIndex from "../components/CopyIndex";
import { useFilters, useUrlSync } from "../context/FiltersContext";

function StratsInner() {
  useUrlSync();
  const { search } = useFilters();

  // Wallet/token/funding + the go-live checklist live permanently in the
  // right SidebarShell (see layout.tsx) — this column is just the strat
  // tabs and their content.
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
