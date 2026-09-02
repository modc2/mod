"use client";

// /strats — RETIRED, kept as a forwarder.
//
// This was the console's front door: a board of saved strats, each with its
// capital and its bench. It is gone because it was a page you had to visit to
// set two things that the workspace already edits inline — capital lives in
// the SETTINGS panel above the charts, and the bench is the TRADERS tab. The
// console is three tabs now: TRADERS, BACKTEST, LIVE.
//
// Two link shapes still land here and both must keep working — they are in
// bookmarks, in browser history and in every chat log the console was ever
// linked in:
//
//   /strats?id=copy-<address>   →  that leader's workspace
//   /strats                     →  the new front door
//
// Switching between saved strats is the copy book in the side panel
// (components/StratSidebar.tsx), which is reachable from every screen.
//
// basePath ("/polymarket") is prepended automatically — pass paths WITHOUT it.

import { Suspense, useEffect } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import { addressFromStrategyId } from "../lib/identityStrat";

function StratsRedirect() {
  const router = useRouter();
  const params = useSearchParams();
  const legacyId = params?.get("id") ?? null;
  const legacyAddress = legacyId ? addressFromStrategyId(legacyId) : null;

  // An effect rather than a server redirect: `addressFromStrategyId` reads a
  // client-side id format, and the destination workspaces are localStorage
  // backed, so the two can't share a render pass.
  useEffect(() => {
    router.replace(legacyAddress ? `/copy/${legacyAddress}` : "/traders");
  }, [legacyAddress, router]);

  return <p className="p-4 text-[12px] text-pixel-gray font-mono">opening…</p>;
}

export default function StratsPage() {
  return (
    <Suspense>
      <StratsRedirect />
    </Suspense>
  );
}
