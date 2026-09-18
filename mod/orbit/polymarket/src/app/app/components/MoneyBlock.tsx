"use client";

// MONEY — topping up and taking money out, as its OWN TAB on the side
// panel's rail (INDEX · MONEY · BACKTEST · LIVE).
//
// It used to be a collapsible drawer INSIDE the INDEX column, above the
// allocation list. That read as the same thing three times — the header
// already prints the funded balance, the drawer's collapsed line printed it
// again, and the expanded WalletPanel printed it a third time — and when
// open, the two big wallet tiles pushed the strat allocation (the thing the
// INDEX tab is actually for) below the fold. Money is still a drawer in
// spirit — you want it mid-backtest, mid-browse — but the rail IS the
// drawer handle now, one tap away at the top of the column, and INDEX gets
// to lead with "which strat holds what".
//
// Anything that discovers you're short of funds fires
//
//     window.dispatchEvent(new Event(OPEN_MONEY_EVENT))
//
// and UserSidebar opens the column on this tab (LIVE's FUND NOW banner, an
// engine "not enough balance" state, the header's balance chip).
//
// The panel itself is `WalletPanel` — two tiles, one amount, one button, and
// the direction flips by tapping the other tile. Deposit and withdraw were
// never two forms; they're one flow with an arrow in it. Bridging from
// another chain and the legacy V1 Safe stay behind MORE — they are
// once-ever operations, and a first-time user meeting three funding panels
// at once is how "how do I add money" becomes a support question.

import { useState } from "react";

import { useAuth } from "../context/AuthContext";
import WalletPanel from "./WalletPanel";
import WalletFundingPanel from "./WalletFundingPanel";
import PolymarketAccountPanel from "./PolymarketAccountPanel";

/** Ask the side panel to open on the MONEY tab. Anything short of funds
    fires this rather than navigating. Handled by UserSidebar. */
export const OPEN_MONEY_EVENT = "poly-open-money";

export default function MoneyTab() {
  const { auth } = useAuth();
  const [more, setMore] = useState(false);

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
      {/* Deposit / withdraw / send — one flow. */}
      <div id="sidebar-wallet-panel">
        <WalletPanel />
      </div>

      <button
        onClick={() => setMore((v) => !v)}
        className="text-[10px] font-mono tracking-[0.16em] text-pixel-gray hover:text-pixel-white px-1"
      >
        {more ? "⌃ LESS" : "⌄ BRING IT FROM ANOTHER CHAIN"}
      </button>

      {more && (
        <div className="space-y-2">
          {/* Bridge / send in from any chain. */}
          <WalletFundingPanel />
          {/* Legacy V1 Safe — renders itself away unless there's a
              leftover balance stranded on it. */}
          <PolymarketAccountPanel />
        </div>
      )}
    </div>
  );
}
