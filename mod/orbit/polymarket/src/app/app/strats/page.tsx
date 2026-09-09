"use client";

// /strats — a FORWARDER into the side panel's STRATS tab.
//
// The strat manager lives in the side panel now (components/StratsTab.tsx):
// create, fork, rename, delete, build with the factory/lab, and share —
// private by default, publishable per strat. This route survives for
// bookmarks and old links, and two link shapes must keep working — they are
// in browser history and in every chat log the console was ever linked in:
//
//   /strats?id=copy-<address>   →  that leader's workspace
//   /strats                     →  /traders with the STRATS tab open
//
// basePath ("/polymarket") is prepended automatically — pass paths WITHOUT it.

import { Suspense, useEffect } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import { requestSidebarTab } from "../components/UserSidebar";
import { addressFromStrategyId } from "../lib/identityStrat";

function StratsRedirect() {
  const router = useRouter();
  const params = useSearchParams();
  const legacyId = params?.get("id") ?? null;
  const legacyAddress = legacyId ? addressFromStrategyId(legacyId) : null;

  // An effect rather than a server redirect: `addressFromStrategyId` reads a
  // client-side id format, and both destinations are localStorage backed, so
  // the two can't share a render pass.
  useEffect(() => {
    if (legacyAddress) {
      router.replace(`/copy/${legacyAddress}`);
    } else {
      requestSidebarTab("STRATS");
      router.replace("/traders");
    }
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
