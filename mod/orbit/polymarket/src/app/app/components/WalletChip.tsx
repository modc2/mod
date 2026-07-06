"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useAuth } from "../context/AuthContext";
import { shortAddress } from "../lib/auth";

/// Wallet chip for the top bar + a dropdown that lets the user flip between
/// previously-used wallets ("sign in as another person"). The button itself
/// shows the active wallet and a trading-ready dot; clicking opens the
/// switcher.
export default function WalletChip() {
  const {
    auth,
    hasWallet,
    connect,
    disconnect,
    loading,
    knownWallets,
    switchToWallet,
    addWallet,
    renameWallet,
    forgetWallet,
  } = useAuth();

  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const [draftLabel, setDraftLabel] = useState("");
  const rootRef = useRef<HTMLDivElement>(null);
  // Funded USDC per wallet (deposit-wallet collateral), keyed by lowercased
  // address. undefined = not fetched yet, null = unavailable, number = dollars.
  const [balances, setBalances] = useState<Record<string, number | null>>({});

  // Wallets the switcher knows about, as a stable key so the balance fetch
  // doesn't re-run on every render (knownWallets is a fresh array each time).
  const addrKey = knownWallets.map((w) => w.address).join(",");

  // When the dropdown opens, fetch each known wallet's funded value so the
  // user can see where their money is before switching. Sums every USDC form
  // (CLOB collateral + native + un-deposited USDC.e) the backend reports.
  useEffect(() => {
    if (!open || !addrKey) return;
    const addrs = addrKey.split(",").filter(Boolean);
    let cancelled = false;
    (async () => {
      const entries = await Promise.all(
        addrs.map(async (addr) => {
          try {
            const r = await fetch(
              `/api/polymarket/deposit-wallet/info?eoa=${encodeURIComponent(addr)}`,
              { cache: "no-store" },
            );
            if (!r.ok) return [addr.toLowerCase(), null] as const;
            const j = (await r.json()) as {
              usdcBalance?: string; nativeUsdcBalance?: string;
              rawUsdceBalance?: string; balanceUnavailable?: boolean;
            };
            if (j.balanceUnavailable) return [addr.toLowerCase(), null] as const;
            const dollars =
              (Number(j.usdcBalance ?? 0) +
                Number(j.nativeUsdcBalance ?? 0) +
                Number(j.rawUsdceBalance ?? 0)) /
              1e6;
            return [addr.toLowerCase(), Number.isFinite(dollars) ? dollars : null] as const;
          } catch {
            return [addr.toLowerCase(), null] as const;
          }
        }),
      );
      if (!cancelled) setBalances(Object.fromEntries(entries));
    })();
    return () => { cancelled = true; };
  }, [open, addrKey]);

  // Close on outside-click / Escape.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false);
        setEditing(null);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { setOpen(false); setEditing(null); }
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const activeLower = auth.address?.toLowerCase() ?? null;
  // Everyone except the active wallet — the "switch to" choices.
  const others = knownWallets.filter((w) => w.address.toLowerCase() !== activeLower);
  const active = knownWallets.find((w) => w.address.toLowerCase() === activeLower);

  // Render the funded value for a wallet address (null = unavailable,
  // undefined = still loading). Green when funded, dim when empty.
  const fundedChip = (address: string) => {
    const v = balances[address.toLowerCase()];
    if (v === undefined) {
      return <span className="text-[10px] font-mono text-pixel-gray/50 animate-pulse">···</span>;
    }
    if (v === null) {
      return <span className="text-[10px] font-mono text-pixel-gray/50">—</span>;
    }
    const text = v >= 1000 ? `$${(v / 1000).toFixed(1)}k` : `$${v.toFixed(2)}`;
    return (
      <span
        className={`text-[11px] font-mono ${v > 0 ? "text-green-400" : "text-pixel-gray/60"}`}
        title="Funded USDC in this wallet's trading account"
      >
        {text}
      </span>
    );
  };

  const label = loading
    ? "..."
    : auth.connected && auth.address
      ? active?.label || shortAddress(auth.address)
      : hasWallet
        ? "CONNECT"
        : "NO WALLET";

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

  const copyActive = useCallback(async () => {
    if (!auth.address) return;
    try {
      await navigator.clipboard.writeText(auth.address);
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    } catch {}
  }, [auth.address]);

  const handleSwitch = useCallback(async (address: string) => {
    setOpen(false);
    setEditing(null);
    await switchToWallet(address);
  }, [switchToWallet]);

  const handleAdd = useCallback(async () => {
    setOpen(false);
    setEditing(null);
    await addWallet();
  }, [addWallet]);

  const startEdit = useCallback((address: string, current?: string) => {
    setEditing(address);
    setDraftLabel(current ?? "");
  }, []);

  const commitEdit = useCallback((address: string) => {
    renameWallet(address, draftLabel);
    setEditing(null);
  }, [renameWallet, draftLabel]);

  return (
    <div className="relative" ref={rootRef}>
      <button
        onClick={() => {
          // First-ever connect with no history: skip straight to MetaMask.
          if (!auth.connected && knownWallets.length === 0) {
            if (hasWallet) void connect();
            return;
          }
          setOpen((o) => !o);
        }}
        disabled={!hasWallet && !auth.connected && knownWallets.length === 0}
        title={
          auth.connected
            ? `${auth.address} · ${auth.authenticated ? "trading enabled" : "trading not yet enabled"} · click to switch`
            : hasWallet ? "Connect / switch wallet" : "Install a wallet extension first"
        }
        className={`pixel-btn text-[13px] px-2 py-1 transition-colors flex items-center gap-1.5 disabled:opacity-60 ${color}`}
      >
        <div className={`w-1.5 h-1.5 rounded-full ${dotColor}`} />
        <span className="font-mono">{label}</span>
        {(auth.connected || knownWallets.length > 0) && (
          <span className="text-[10px] opacity-60">{open ? "▲" : "▼"}</span>
        )}
      </button>

      {open && (
        <div className="absolute right-0 mt-1 w-[300px] z-50 pixel-panel border-2 border-pixel-border bg-pixel-black/95 shadow-xl">
          {/* ── Active wallet ── */}
          {auth.connected && auth.address ? (
            <div className="px-3 py-2 border-b border-pixel-border/60">
              <div className="text-[10px] text-pixel-gray tracking-[0.18em] mb-1">SIGNED IN AS</div>
              <div className="flex items-center gap-2">
                <div className={`w-1.5 h-1.5 rounded-full ${dotColor} shrink-0`} />
                <div className="min-w-0 flex-1">
                  {active?.label && (
                    <div className="text-[12px] text-pixel-white truncate">{active.label}</div>
                  )}
                  <div className="font-mono text-[11px] text-green-400 truncate" title={auth.address}>
                    {shortAddress(auth.address)}
                  </div>
                </div>
                <div className="flex flex-col items-end shrink-0">
                  {fundedChip(auth.address)}
                  <span className={`text-[10px] font-mono ${auth.authenticated ? "text-green-400" : "text-amber-400"}`}>
                    {auth.authenticated ? "CLOB ✓" : "CLOB…"}
                  </span>
                </div>
              </div>
              <div className="flex items-center gap-2 mt-1.5">
                <button
                  onClick={copyActive}
                  className="text-[10px] tracking-[0.12em] text-pixel-gray hover:text-green-400 border border-pixel-border/60 hover:border-green-400/60 px-1.5 py-0.5"
                >
                  {copied ? "COPIED ✓" : "COPY"}
                </button>
                <button
                  onClick={() => startEdit(auth.address!, active?.label)}
                  className="text-[10px] tracking-[0.12em] text-pixel-gray hover:text-pixel-white border border-pixel-border/60 hover:border-pixel-white/60 px-1.5 py-0.5"
                >
                  RENAME
                </button>
                <button
                  onClick={() => { disconnect(); }}
                  className="ml-auto text-[10px] tracking-[0.12em] text-red-400/80 hover:text-red-400 border border-red-400/40 hover:border-red-400/80 px-1.5 py-0.5"
                >
                  SIGN OUT
                </button>
              </div>
              {editing === auth.address && (
                <div className="flex items-center gap-1.5 mt-1.5">
                  <input
                    autoFocus
                    value={draftLabel}
                    onChange={(e) => setDraftLabel(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") commitEdit(auth.address!);
                      if (e.key === "Escape") setEditing(null);
                    }}
                    placeholder="name this wallet"
                    className="pixel-input-sm flex-1 text-[12px] font-mono"
                  />
                  <button
                    onClick={() => commitEdit(auth.address!)}
                    className="text-[10px] text-green-400 border border-green-400/60 px-1.5 py-1"
                  >
                    SAVE
                  </button>
                </div>
              )}
            </div>
          ) : (
            <div className="px-3 py-2 border-b border-pixel-border/60 flex items-center justify-between gap-2">
              <span className="text-[12px] text-pixel-gray">
                {hasWallet ? "Not signed in." : "No wallet extension."}
              </span>
              <button
                onClick={() => { setOpen(false); if (hasWallet) void connect(); }}
                disabled={!hasWallet || loading}
                className="pixel-btn text-[11px] px-2 py-1 border-green-400 text-green-400 hover:bg-green-400/10 disabled:opacity-40"
              >
                {loading ? "..." : "CONNECT"}
              </button>
            </div>
          )}

          {/* ── Other / previous wallets ── */}
          {others.length > 0 && (
            <div className="py-1 max-h-[260px] overflow-y-auto">
              <div className="px-3 pt-1 pb-1 text-[10px] text-pixel-gray tracking-[0.18em]">
                SWITCH TO
              </div>
              {others.map((w) => (
                <div
                  key={w.address}
                  className="group px-2 py-1 flex items-center gap-2 hover:bg-pixel-white/5"
                >
                  {editing === w.address ? (
                    <>
                      <input
                        autoFocus
                        value={draftLabel}
                        onChange={(e) => setDraftLabel(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") commitEdit(w.address);
                          if (e.key === "Escape") setEditing(null);
                        }}
                        placeholder="name this wallet"
                        className="pixel-input-sm flex-1 text-[12px] font-mono"
                      />
                      <button
                        onClick={() => commitEdit(w.address)}
                        className="text-[10px] text-green-400 border border-green-400/60 px-1.5 py-1"
                      >
                        SAVE
                      </button>
                    </>
                  ) : (
                    <>
                      <button
                        onClick={() => { void handleSwitch(w.address); }}
                        className="min-w-0 flex-1 text-left"
                        title={`Sign in as ${w.address}`}
                      >
                        {w.label && (
                          <div className="text-[12px] text-pixel-white truncate">{w.label}</div>
                        )}
                        <div className="font-mono text-[11px] text-pixel-gray group-hover:text-green-400 truncate">
                          {shortAddress(w.address)}
                        </div>
                      </button>
                      <div className="shrink-0">{fundedChip(w.address)}</div>
                      <button
                        onClick={() => startEdit(w.address, w.label)}
                        className="text-[11px] text-pixel-gray hover:text-pixel-white px-1 opacity-0 group-hover:opacity-100"
                        title="Rename"
                      >
                        ✎
                      </button>
                      <button
                        onClick={() => forgetWallet(w.address)}
                        className="text-[11px] text-pixel-gray hover:text-red-400 px-1 opacity-0 group-hover:opacity-100"
                        title="Forget this wallet"
                      >
                        ✕
                      </button>
                    </>
                  )}
                </div>
              ))}
            </div>
          )}

          {/* ── Add / sign in another person ── */}
          <div className="px-3 py-2 border-t border-pixel-border/60">
            <button
              onClick={() => { void handleAdd(); }}
              disabled={!hasWallet || loading}
              className="w-full pixel-btn text-[12px] px-2 py-1.5 border-green-400/70 text-green-400 hover:bg-green-400/10 disabled:opacity-40"
              title="Open MetaMask to sign in as another person"
            >
              {loading ? "..." : "+ SIGN IN ANOTHER PERSON"}
            </button>
            {!hasWallet && (
              <div className="text-[10px] text-pixel-gray mt-1.5 text-center">
                Install a wallet extension to switch accounts.
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
