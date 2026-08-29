"use client";

// /copy/trades — MY COPY TRADES, full size.
//
// The sidebar carries a compact version of this board (it is the same
// component); this is where it gets room: the per-leader coverage table, the
// wider rows, and the sentence box with the market gate it can arm.
//
// Why a route of its own rather than another subtab of the LIVE desk: the desk
// is about ONE session's engine vitals, and this question spans the whole book
// — "across every trader I copy, how much of their flow am I actually
// getting". See components/CopyTradesPanel.tsx.

import { Suspense } from "react";
import Link from "next/link";

import TopBar from "../../components/TopBar";
import CopyTradesPanel from "../../components/CopyTradesPanel";
import { useAuth } from "../../context/AuthContext";
import { getOwnerAddress } from "../../lib/access";
import { useCopyBook } from "../../lib/useCopyBook";
import { shortAddress } from "../../lib/identityStrat";
import { confirmGate, gatePatch } from "../../lib/armGate";
import type { CompiledGate } from "../../lib/semanticFilter";

function TradesPageInner() {
  // The book is here for one reason: a sentence you just filtered your history
  // with is the sentence you want the copying to run under, and re-typing it
  // into each trader's own screen is how the two drift apart. Arming from here
  // applies to EVERY trader on the desk — the sidebar is where a subset is
  // checked off — and the confirm (lib/armGate.ts) names them before it writes.
  const { auth } = useAuth();
  const eoa = getOwnerAddress() ?? auth.address ?? null;
  const { rows, allocate } = useCopyBook(eoa);

  const arm = async (gate: CompiledGate) => {
    const names = rows.map((r) => r.label?.trim() || shortAddress(r.address));
    if (!confirmGate(gate, names)) return;
    for (const row of rows) {
      await allocate(row.address, row.allocationUsd, undefined, gatePatch(gate));
    }
  };

  return (
    <div className="max-w-[1600px] mx-auto">
      <TopBar showSearch searchPlaceholder="PASTE A TRADER ADDRESS…" />
      <div className="p-4 space-y-3">
        <div className="flex items-baseline gap-3 flex-wrap">
          <h1 className="text-[15px] font-mono font-semibold tracking-[0.12em] text-pixel-white">
            MY COPY TRADES
          </h1>
          <span className="text-[11px] font-mono text-pixel-gray">
            every trade the traders I copy made · every fill of mine · joined by market, side and time
          </span>
          <span className="flex-1" />
          <Link
            href="/copy"
            className="text-[11px] font-mono tracking-[0.1em] text-pixel-gray hover:text-green-400"
            title="The copy desk — who I copy and with how much"
          >
            ← DESK
          </Link>
        </div>
        <CopyTradesPanel
          defaultDays={7}
          onArm={rows.length ? (g) => void arm(g) : undefined}
          armLabel={`ARM ON ${rows.length} TRADER${rows.length === 1 ? "" : "S"}`}
        />
      </div>
    </div>
  );
}

export default function CopyTradesPage() {
  return (
    <Suspense>
      <TradesPageInner />
    </Suspense>
  );
}
