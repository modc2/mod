"use client";

import { Suspense } from "react";
import TopBar from "../components/TopBar";
import CopyIndex from "../components/CopyIndex";
import PreconditionChecklist from "../components/PreconditionChecklist";
import { useFilters, useUrlSync } from "../context/FiltersContext";
import { useSidebar } from "../context/SidebarContext";

function StratsInner() {
  useUrlSync();
  const { search } = useFilters();
  // When the right sidebar is docked it owns the go-live checklist + the
  // wallet/token header, so render them inline only when it's undocked.
  const { docked, hydrated } = useSidebar();
  const sidebarOwnsHeader = hydrated && docked;

  return (
    <div className="max-w-[1920px] mx-auto">
      <TopBar searchPlaceholder="FILTER INDEX BY MARKET..." />
      <div className="p-4">
        {!sidebarOwnsHeader && (
          <div className="mb-4">
            <PreconditionChecklist />
          </div>
        )}
        <CopyIndex searchFilter={search} hideWalletHeader={sidebarOwnsHeader} />
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
