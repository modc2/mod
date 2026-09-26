"use client";

// /copy/basket — copy a SET of traders, with a different amount against each.
//
// A sibling of the desk, not a replacement for it: /copy is what this
// deployment IS copying, and this is where you decide what it should be. The
// roster here is a browser-local draft (lib/basketDraft.ts) until APPLY TO
// DESK writes it through `/copy/allocations`, which is the same route the
// `pm_copy_*` MCP tools call.
//
// See components/BasketSim.tsx.

import { Suspense } from "react";
import TopBar from "../../components/TopBar";
import BasketSim from "../../components/BasketSim";

function BasketPageInner() {
  return (
    <div className="max-w-[1600px] mx-auto">
      <TopBar showSearch searchPlaceholder="PASTE A TRADER ADDRESS…" />
      <div className="p-4">
        <BasketSim />
      </div>
    </div>
  );
}

export default function BasketPage() {
  return (
    <Suspense>
      <BasketPageInner />
    </Suspense>
  );
}
