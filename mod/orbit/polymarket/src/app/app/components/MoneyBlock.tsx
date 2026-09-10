"use client";

// MONEY — topping up and taking money out, in the SIDE PANEL.
//
// This used to be a subtab of the live workspace ("LIVE → WALLET"), which put
// funding one navigation away from every screen that needed it and gave the
// engine's own tab rail a stop that had nothing to do with the engine. Money
// isn't a destination. It's a drawer: you want it while you're reading a
// backtest, while you're picking traders, while you're watching a session
// starve — and you want the page you were on to still be there afterwards.
//
// So it lives in the same right-hand column as your accounts and your bench,
// directly under the wallet you're signed in as, and it opens from anywhere:
//
//     window.dispatchEvent(new Event(OPEN_MONEY_EVENT))
//
// Anything that discovers you're short of funds (LIVE's FUND NOW banner, an
// engine "not enough balance" state) fires that instead of routing.
//
// The panel itself is `WalletPanel` — two tiles, one amount, one button, and
// the direction flips by tapping the other tile. Deposit and withdraw were
// never two forms; they're one flow with an arrow in it. This component is
// the *placement*: the collapsed summary line (so a docked column isn't a
// wall of money chrome), the open/closed memory, and the event handshake.
//
// Bridging from another chain and the legacy V1 Safe stay behind MORE — they
// are once-ever operations, and a first-time user meeting three funding
// panels at once is how "how do I add money" becomes a support question.

import { useCallback, useEffect, useState } from "react";

import { useAuth } from "../context/AuthContext";
import { fundedUsd } from "../lib/funding";
import WalletPanel from "./WalletPanel";
import WalletFundingPanel from "./WalletFundingPanel";
import PolymarketAccountPanel from "./PolymarketAccountPanel";

/** Ask the side panel to open with MONEY expanded. Anything short of funds
    fires this rather than navigating. */
export const OPEN_MONEY_EVENT = "poly-open-money";

const OPEN_KEY = "poly_money_open";

export default function MoneyBlock() {
  const { auth } = useAuth();
  const [open, setOpen] = useState(false);
  const [more, setMore] = useState(false);
  const [funded, setFunded] = useState<number | null | undefined>(undefined);

  // Restore the last state. Collapsed by default on a first visit: the column
  // leads with WHO you are and WHO you copy, and money is the thing you open
  // when you need it.
  useEffect(() => {
    try {
      setOpen(localStorage.getItem(OPEN_KEY) === "1");
    } catch {}
  }, []);

  const setOpenPersisted = useCallback((next: boolean) => {
    setOpen(next);
    try {
      localStorage.setItem(OPEN_KEY, next ? "1" : "0");
    } catch {}
  }, []);

  // Anything, anywhere, can ask for this block by name.
  useEffect(() => {
    const onAsk = () => setOpenPersisted(true);
    window.addEventListener(OPEN_MONEY_EVENT, onAsk);
    return () => window.removeEventListener(OPEN_MONEY_EVENT, onAsk);
  }, [setOpenPersisted]);

  // The collapsed line still has to be worth reading, so it carries the one
  // number that matters: what's actually tradable. Only fetched while signed
  // in — a docked column mounts on every page.
  const address = auth.address;
  useEffect(() => {
    if (!address) {
      setFunded(undefined);
      return;
    }
    let cancelled = false;
    const read = async () => {
      const v = await fundedUsd(address);
      if (!cancelled) setFunded(v);
    };
    void read();
    const t = setInterval(() => void read(), 30_000);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [address, open]);

  if (!auth.connected) return null;

  return (
    <section style={{ borderTop: "1px solid var(--border)" }}>
      <button
        onClick={() => setOpenPersisted(!open)}
        aria-expanded={open}
        className="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-pixel-white/[0.04] transition-colors"
        title="Top up your trading balance, or take money back out"
      >
        <span className="text-[11px] font-semibold tracking-[0.2em] text-pixel-white">MONEY</span>
        <span className="text-[10px] text-pixel-gray">top up · take out</span>
        <span className="ml-auto flex items-center gap-2 shrink-0">
          {funded === undefined ? (
            <span className="text-[10px] font-mono text-pixel-gray/50 animate-pulse">···</span>
          ) : funded === null ? (
            <span className="text-[10px] font-mono text-pixel-gray/60" title="On-chain read failed">
              —
            </span>
          ) : (
            <span
              className={`text-[12px] font-mono ${funded > 0 ? "text-green-400" : "text-pixel-gray/70"}`}
              title="Tradable USDC in your Polymarket balance"
            >
              ${funded >= 1000 ? `${(funded / 1000).toFixed(1)}k` : funded.toFixed(2)}
            </span>
          )}
          <span className="text-[11px] text-pixel-gray">{open ? "⌃" : "⌄"}</span>
        </span>
      </button>

      {open && (
        <div className="px-2 pb-3 space-y-2">
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
      )}
    </section>
  );
}
