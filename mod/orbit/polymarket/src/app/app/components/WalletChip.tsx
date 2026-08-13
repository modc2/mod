"use client";

import { useCallback } from "react";
import { useAuth } from "../context/AuthContext";
import { shortAddress } from "../lib/auth";
import { OPEN_ACCOUNTS_EVENT } from "./AccountsPanel";

/// Wallet chip for the top bar: who's signed in, whether trading is enabled,
/// and one click to the ACCOUNTS section of the strat sidebar — which owns the
/// list of known wallets, their balances and the switcher (AccountsPanel).
/// This used to carry its own dropdown copy of that list; two switchers that
/// could disagree about the active wallet is one too many.
///
/// It still handles the one thing that must work with no column open at all:
/// the first-ever connect, when there is no account list to show yet.
export default function WalletChip() {
  const { auth, hasWallet, connect, loading, knownWallets } = useAuth();

  const label = loading
    ? "..."
    : auth.connected && auth.address
      ? knownWallets.find((w) => w.address.toLowerCase() === auth.address!.toLowerCase())?.label
        || shortAddress(auth.address).toLowerCase()
      : hasWallet
        ? "connect"
        : "no wallet";

  const color = auth.connected
    ? "border-green-400 text-green-400 hover:bg-green-400/10"
    : hasWallet
      ? "border-pixel-border text-pixel-gray hover:text-green-400 hover:border-green-400"
      : "border-pixel-border text-pixel-gray cursor-not-allowed";

  // Trading-ready status folded into the dot:
  //   • bright green = connected AND CLOB authenticated (trades will go through)
  //   • amber        = connected, CLOB not yet enabled (read-only)
  //   • gray         = no wallet / disconnected
  const dotColor = !auth.connected
    ? "bg-pixel-gray"
    : auth.authenticated
      ? "bg-green-400"
      : "bg-amber-400";

  const openAccounts = useCallback(() => {
    window.dispatchEvent(new Event(OPEN_ACCOUNTS_EVENT));
  }, []);

  return (
    <button
      onClick={() => {
        // First-ever connect with no history: skip straight to MetaMask —
        // there is no account list to show yet.
        if (!auth.connected && knownWallets.length === 0) {
          if (hasWallet) void connect();
          return;
        }
        openAccounts();
      }}
      disabled={!hasWallet && !auth.connected && knownWallets.length === 0}
      title={
        auth.connected
          ? `${auth.address} · ${auth.authenticated ? "trading enabled" : "trading not yet enabled"} · click for ACCOUNTS in the sidebar`
          : hasWallet ? "Connect / switch wallet" : "Install a wallet extension first"
      }
      className={`pixel-btn normal-case text-[13px] px-2 py-1 transition-colors flex items-center gap-1.5 disabled:opacity-60 ${color}`}
    >
      <div className={`w-1.5 h-1.5 rounded-full ${dotColor}`} />
      <span className="font-mono whitespace-nowrap">{label}</span>
      {/* Only when switching is actually possible — "no wallet ×2" reads as
          nonsense on a browser with no extension to switch with. */}
      {hasWallet && knownWallets.length > 1 && (
        <span
          className="text-[10px] opacity-70 font-mono"
          title={`${knownWallets.length} accounts — switch in the sidebar`}
        >
          ×{knownWallets.length}
        </span>
      )}
    </button>
  );
}
