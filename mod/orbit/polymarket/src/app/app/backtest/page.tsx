"use client";

// /backtest — TEST. The second of the console's three tabs.
//
// Pick traders, test them, run them. This is TEST: the same workspace /live
// renders, replaying your bench against real historical flow on simulated
// money. No wallet, no deposit, nothing to fund. `forcedMode` pins the screen
// so the panel carries no TEST|LIVE switch of its own — the nav is the switch,
// and the URL is the answer to "which am I looking at".
//
// basePath ("/polymarket") is prepended automatically — pass paths WITHOUT it.

import { Suspense } from "react";

import Workspace from "../components/Workspace";

export default function BacktestPage() {
  return (
    <Suspense>
      <Workspace mode="BACKTEST" />
    </Suspense>
  );
}
