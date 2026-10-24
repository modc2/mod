"use client";

// The header's account pill. One press opens the account sidebar — the agent
// chat and the wallet live there — so the header itself stays a single quiet
// chip: who you are, plus one status word (your BLOC, or "wrong network").
//
// ⌘J / Ctrl+J opens straight to the agent from anywhere.

import { useCallback, useEffect, useState } from "react";
import { bloctime, fmt, type Overview } from "@/lib/bloctime";
import { useWallet } from "@/lib/wallet";
import AccountSidebar, { addrGradient, type SideTab } from "./AccountSidebar";
import { Icon } from "./Icon";

const TAB_KEY = "mod.web.side.tab";

export default function WalletButton() {
  const w = useWallet();
  const [overview, setOverview] = useState<Overview | null>(null);
  const [open, setOpen] = useState(false);
  const [tab, setTabState] = useState<SideTab>("agent");

  useEffect(() => {
    const t = localStorage.getItem(TAB_KEY);
    if (t === "agent" || t === "wallet") setTabState(t);
  }, []);
  const setTab = useCallback((t: SideTab) => {
    setTabState(t);
    try {
      localStorage.setItem(TAB_KEY, t);
    } catch {}
  }, []);

  // Live BLOC position for the connected address (the bloctime module reads it
  // over its own RPC, so no provider round-trip from the browser).
  useEffect(() => {
    if (!w.address) {
      setOverview(null);
      return;
    }
    let alive = true;
    const load = () =>
      bloctime
        .overview(w.address)
        .then((o) => alive && setOverview(o))
        .catch(() => alive && setOverview(null));
    load();
    const t = setInterval(load, 60_000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [w.address]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key.toLowerCase() === "j" && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        setTab("agent");
        setOpen((v) => !v || tab !== "agent");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [setTab, tab]);

  const close = useCallback(() => setOpen(false), []);

  return (
    <div className="wallet-wrap">
      <button
        className={`wallet-btn${w.address ? " on" : ""}${w.wrongNetwork ? " warn" : ""}${open ? " open" : ""}`}
        onClick={() => setOpen((v) => !v)}
        title={w.address ? `${w.address} — account & agent (⌘J)` : "Agent & wallet (⌘J)"}
        aria-expanded={open}
      >
        {w.address ? (
          <>
            <span className="wallet-ava" style={{ background: addrGradient(w.address) }} />
            <span className="mono">{w.short}</span>
            {w.wrongNetwork ? (
              <span className="wallet-net warn">wrong network</span>
            ) : (
              overview && (
                <span className="wallet-net">
                  <Icon name="clock" /> {fmt(overview.blocBalance)}
                </span>
              )
            )}
          </>
        ) : (
          <>
            <span className="wallet-dot" />
            Connect
          </>
        )}
        <span className="wallet-agent" aria-hidden>
          <Icon name="spark" />
        </span>
      </button>

      <AccountSidebar open={open} tab={tab} onTab={setTab} onClose={close} overview={overview} />
    </div>
  );
}
