"use client";

import { useState } from "react";

import { connectDev, connectInjected, credits, short, Wallet } from "../lib/api";

/**
 * Two ways in: a browser wallet, or one of the deterministic development
 * wallets the API will sign for while it is in open mode. The second is
 * labelled as what it is, and disappears when open mode is off.
 */
export default function WalletBar({
  wallet,
  setWallet,
  account,
  status,
}: {
  wallet: Wallet | null;
  setWallet: (w: Wallet | null) => void;
  account: any;
  status: any;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [devOpen, setDevOpen] = useState(false);
  const openMode = status?.current !== undefined && status?.open_mode !== false;

  const connect = async (fn: () => Promise<Wallet>) => {
    setBusy(true);
    setError(null);
    try {
      setWallet(await fn());
      setDevOpen(false);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  if (wallet) {
    return (
      <div className="text-right">
        <div className="flex items-center gap-2 justify-end">
          {account?.is_owner && <span className="pill" style={{ color: "var(--warn)" }}>owner</span>}
          {account?.is_attestor && <span className="pill" style={{ color: "var(--accent)" }}>grader</span>}
          {account?.is_meter && <span className="pill">meter</span>}
          <span className="pill">{wallet.kind === "dev" ? `dev wallet ${wallet.devIndex}` : "wallet"}</span>
          <span>{short(wallet.address)}</span>
          <button className="btn text-[11px] py-1" onClick={() => setWallet(null)}>
            sign out
          </button>
        </div>
        <div className="text-[12px] text-muted mt-1">
          <span className="text-fg">{credits(account?.balance)}</span> credits
          {account?.locked > 0 && (
            <>
              {" · "}
              <span className="text-warn">{credits(account.locked)}</span> locked in sealed bets
            </>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="text-right">
      <div className="flex gap-2 justify-end">
        <button className="btn btn-primary" disabled={busy} onClick={() => connect(connectInjected)}>
          sign in
        </button>
        <button className="btn" disabled={busy} onClick={() => setDevOpen(!devOpen)}>
          dev wallet
        </button>
      </div>
      {devOpen && (
        <div className="mt-2 flex gap-1 justify-end flex-wrap max-w-xs">
          {[0, 1, 2, 3, 4, 5, 6, 7].map((i) => (
            <button
              key={i}
              className="btn text-[11px] py-1 px-2"
              disabled={busy}
              onClick={() => connect(() => connectDev(i))}
              title={
                ["owner", "grader a", "grader b", "meter", "alice", "bob", "carol", "dave"][i]
              }
            >
              {i}
            </button>
          ))}
        </div>
      )}
      {error && <div className="text-[11px] text-down mt-1 max-w-xs">{error}</div>}
      {!openMode && devOpen && (
        <div className="text-[11px] text-muted mt-1">dev wallets need the api in open mode</div>
      )}
    </div>
  );
}
