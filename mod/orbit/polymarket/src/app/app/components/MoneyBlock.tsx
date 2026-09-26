"use client";

// MONEY — the liquidity view, as its OWN TAB on the side panel's rail
// (INDEX · MONEY · BACKTEST · LIVE).
//
// The tab used to stack a fold inside a fold: WalletPanel, then a
// "⌄ BRING IT FROM ANOTHER CHAIN" toggle, which revealed a funding panel
// with its OWN "+ ADD FUNDS" toggle, a chain dropdown and three asset chips.
// Reaching the bridge was two folds and two pickers deep for a question the
// wallet can answer by itself. Now the tab reads top to bottom in the order
// money flows:
//
//   LIQUIDITY   (LiquidityFlow)  — OTHER CHAINS ▸ WALLET ▸ TRADING ▸ IN PLAY,
//                                  one number per pool, each a shortcut to
//                                  the block that moves it
//   MOVE        (WalletPanel)    — wallet ⇄ trading, one amount, one button
//   BRIDGE      (BridgePanel)    — one row per chain+asset that actually
//                                  holds funds, prefilled, one button each;
//                                  no dropdowns, no folds
//   V1 SAFE     (PolymarketAccountPanel) — renders itself away unless a
//                                  balance is stranded on the legacy Safe
//
// "Where the liquidity GOES" (the split across strats) stays on the INDEX
// tab's ALLOCATION block — one home per question; the strip's IN PLAY pool
// jumps there.
//
// Anything that discovers you're short of funds fires
//
//     window.dispatchEvent(new Event(OPEN_MONEY_EVENT))
//
// and UserSidebar opens the column on this tab (LIVE's FUND NOW banner, an
// engine "not enough balance" state, the header's balance chip).

import { useAuth } from "../context/AuthContext";
import LiquidityFlow from "./LiquidityFlow";
import WalletPanel from "./WalletPanel";
import BridgePanel from "./BridgePanel";
import PolymarketAccountPanel from "./PolymarketAccountPanel";

/** Ask the side panel to open on the MONEY tab. Anything short of funds
    fires this rather than navigating. Handled by UserSidebar. */
export const OPEN_MONEY_EVENT = "poly-open-money";

export default function MoneyTab() {
  const { auth } = useAuth();

  if (!auth.connected) {
    return (
      <div className="px-3 py-4 text-[10.5px] font-mono text-pixel-gray">
        Sign in first — money moves between YOUR wallet and YOUR trading
        balance, so the panel needs to know who you are.
      </div>
    );
  }

  return (
    <div className="px-2 py-2 space-y-2">
      <LiquidityFlow />
      <div id="sidebar-wallet-panel">
        <WalletPanel />
      </div>
      <BridgePanel />
      {/* Legacy V1 Safe — self-hides unless a balance is stranded on it. */}
      <PolymarketAccountPanel />
    </div>
  );
}
