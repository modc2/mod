"use client";

// Headless: turns every "open the panel / money / accounts / strats" event in
// the console into a navigation to the matching /strats tab. It replaced the
// right-hand side panel (UserSidebar) 2026-10-01 — see lib/stratsNav.ts.
// Mounted once, in TopBar, which is on every page.

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { OPEN_ACCOUNTS_EVENT } from "./AccountsPanel";
import { OPEN_MONEY_EVENT } from "./MoneyBlock";
import {
  OPEN_SIDEBAR_EVENT, SIDEBAR_TAB_EVENT, STRATS_VIEW_EVENT,
  isStratsView, stratsHref, viewFromLegacyTab, type StratsView,
} from "../lib/stratsNav";

export default function StratsNav() {
  const router = useRouter();

  useEffect(() => {
    const go = (v: StratsView) => router.push(stratsHref(v));
    const routes: [string, (e: Event) => void][] = [
      [STRATS_VIEW_EVENT, (e) => { const v = (e as CustomEvent).detail; go(isStratsView(v) ? v : "strats"); }],
      [SIDEBAR_TAB_EVENT, (e) => go(viewFromLegacyTab((e as CustomEvent).detail))],
      // The wallet chip: the account row heads the MONEY tab.
      [OPEN_ACCOUNTS_EVENT, () => go("money")],
      [OPEN_MONEY_EVENT, () => go("money")],
      [OPEN_SIDEBAR_EVENT, () => go("copy")],
    ];
    for (const [name, fn] of routes) window.addEventListener(name, fn);
    return () => { for (const [name, fn] of routes) window.removeEventListener(name, fn); };
  }, [router]);

  return null;
}
