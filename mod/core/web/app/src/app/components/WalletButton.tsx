"use client";

// The header's wallet chip: connect, show who's connected and what they hold,
// and get them onto Base Sepolia if their wallet is somewhere else. It's the
// entry point for everything else on-chain in the explorer — the stake console
// and the per-module backing panels both read the address from here.

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { bloctime, fmt } from "@/lib/bloctime";
import { DEFAULT_CHAIN, useWallet } from "@/lib/wallet";

export default function WalletButton() {
  const w = useWallet();
  const [bloc, setBloc] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  // Live BLOC balance for the connected address (the bloctime module reads it
  // over its own RPC, so no provider round-trip from the browser).
  useEffect(() => {
    if (!w.address) {
      setBloc(null);
      return;
    }
    let alive = true;
    const load = () =>
      bloctime
        .overview(w.address)
        .then((o) => alive && setBloc(o.blocBalance))
        .catch(() => alive && setBloc(null));
    load();
    const t = setInterval(load, 60_000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [w.address]);

  useEffect(() => {
    const onDown = (e: PointerEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", onDown);
    return () => document.removeEventListener("pointerdown", onDown);
  }, []);

  if (!w.address) {
    return (
      <button
        className="wallet-btn"
        onClick={() => w.connect().catch(() => {})}
        disabled={w.connecting}
        title={
          w.hasWallet
            ? "Connect a browser wallet to stake BlocTime"
            : "Install MetaMask (or any EIP-1193 wallet) to stake"
        }
      >
        <span className="wallet-dot" />
        {w.connecting ? "connecting…" : "connect wallet"}
      </button>
    );
  }

  return (
    <div className="wallet-wrap" ref={rootRef}>
      <button
        className={`wallet-btn on${w.wrongNetwork ? " warn" : ""}`}
        onClick={() => setOpen((v) => !v)}
        title={w.address}
      >
        <span className="wallet-dot on" />
        <span className="mono">{w.short}</span>
        {w.wrongNetwork ? (
          <span className="wallet-net warn">wrong network</span>
        ) : (
          bloc !== null && <span className="wallet-net">⧗ {fmt(bloc)} BLOC</span>
        )}
      </button>

      {open && (
        <div className="wallet-pop">
          <div className="wallet-pop-head">
            <span className="mono">{w.address}</span>
            <span className="wallet-chain">
              {w.wrongNetwork
                ? `chain ${w.chainId ?? "?"}`
                : DEFAULT_CHAIN.name}
            </span>
          </div>

          {w.wrongNetwork && (
            <button
              className="btn btn-primary wallet-switch"
              onClick={() => w.switchNetwork().catch(() => {})}
            >
              switch to {DEFAULT_CHAIN.name}
            </button>
          )}

          <div className="wallet-pop-row">
            <span className="k">BLOC held</span>
            <span className="v mono">{bloc === null ? "—" : fmt(bloc)}</span>
          </div>

          <Link href="/stake" className="wallet-pop-link" onClick={() => setOpen(false)}>
            ⧗ stake in the BlocTime protocol →
          </Link>
          <a
            href={`${DEFAULT_CHAIN.explorer}/address/${w.address}`}
            target="_blank"
            rel="noreferrer"
            className="wallet-pop-link"
          >
            view on BaseScan ↗
          </a>
          <button
            className="wallet-pop-link danger"
            onClick={() => {
              w.disconnect();
              setOpen(false);
            }}
          >
            disconnect
          </button>
          {w.error && <div className="wallet-err">{w.error}</div>}
        </div>
      )}
    </div>
  );
}
