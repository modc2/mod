"use client";

// /strats — the STRATS main tab (header tab row, next to TRADERS).
//
// The strat manager lived in the side panel's STRATS rail tab for a day;
// the user moved it here 2026-09-09 ("this should be in the main tabs under
// strats tab in the main header"). This page owns building and sharing:
// the MY STRATS cards (live money + last backtest side by side), + NEW
// STRAT, the AUTO STRAT factory + STRAT LAB, the SCORE MARKET, the
// community gallery and the CID share path. Money still lives in the side
// panel's INDEX tab; BACKTEST/LIVE stay rail tabs there too.
//
// One legacy link shape must keep working — it is in browser history and in
// every chat log the console was ever linked in:
//
//   /strats?id=copy-<address>   →  that leader's workspace
//
// basePath ("/polymarket") is prepended automatically — pass paths WITHOUT it.

import { Suspense, useEffect } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import TopBar from "../components/TopBar";
import StratsTab from "../components/StratsTab";
import { addressFromStrategyId } from "../lib/identityStrat";

function StratsInner() {
  const router = useRouter();
  const params = useSearchParams();
  const legacyId = params?.get("id") ?? null;
  const legacyAddress = legacyId ? addressFromStrategyId(legacyId) : null;

  // An effect rather than a server redirect: `addressFromStrategyId` reads a
  // client-side id format against localStorage-backed state.
  useEffect(() => {
    if (legacyAddress) router.replace(`/copy/${legacyAddress}`);
  }, [legacyAddress, router]);

  if (legacyAddress) {
    return <p className="p-4 text-[12px] text-pixel-gray font-mono">opening…</p>;
  }

  // StratsTab is a self-contained column (it grew up in a 760px dock) — as a
  // main page it gets the same reading width, centered.
  return (
    <div className="max-w-[860px] mx-auto px-2 sm:px-4 py-3">
      <StratsTab />
    </div>
  );
}

export default function StratsPage() {
  return (
    <>
      <TopBar showSearch={false} />
      <Suspense>
        <StratsInner />
      </Suspense>
    </>
  );
}
