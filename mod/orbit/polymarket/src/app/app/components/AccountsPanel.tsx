"use client";

// The ACCOUNTS list — every wallet this browser has signed in as, the money
// each one holds, and the controls to switch/rename/forget/add.
//
// It is the TOP block of the right-hand sidebar, not a top-right dropdown.
// Accounts and strats are the same decision made twice: a strat's money, its
// engine session and its ledger are all keyed by (wallet, strat), so "which
// wallet am I" is the first line of the same column that answers "which strat
// am I on". The header chip is now just a status readout that opens this
// column, and this block is the column's header — hence `onClose`, which puts
// the sidebar's × in the same row as the user it belongs to.
//
// Collapsed it shows the signed-in wallet; expanded it shows ONLY that wallet
// as a card — who you are, the money behind it, sign out. Every other known
// wallet lives behind a SWITCH ACCOUNT fold, closed by default: switching is a
// once-in-a-while act, and a list of four $0.00 strangers above the fold read
// as clutter, not options.

import { useCallback, useEffect, useState } from "react";
import { useAuth } from "../context/AuthContext";
import { shortAddress } from "../lib/auth";
import { fundedUsd } from "../lib/funding";
import WalletTokenPanel from "./WalletTokenPanel";

/** Header chip → sidebar handshake. The chip dispatches this to open the
 *  sidebar with the accounts section already expanded. */
export const OPEN_ACCOUNTS_EVENT = "poly-open-accounts";

export default function AccountsPanel({
  initialExpanded = false,
  onClose,
}: {
  initialExpanded?: boolean;
  /** Given by the sidebar: renders its close × in this header row. */
  onClose?: () => void;
}) {
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

  // `initialExpanded` covers the chip opening a CLOSED sidebar: this component
  // mounts with the column, after the event has already been and gone. The
  // listener below covers the sidebar already being open.
  const [expanded, setExpanded] = useState(initialExpanded);
  const [copied, setCopied] = useState(false);
  /** Device pairing (token + sign-in QR) — folded away by default. */
  const [showPairing, setShowPairing] = useState(false);
  /** The other known wallets + "sign in another person" — same treatment. */
  const [showSwitch, setShowSwitch] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const [draftLabel, setDraftLabel] = useState("");
  // Funded USDC per wallet (deposit-wallet collateral), keyed by lowercased
  // address. undefined = not fetched yet, null = unavailable, number = dollars.
  const [balances, setBalances] = useState<Record<string, number | null>>({});

  // Wallets the switcher knows about, as a stable key so the balance fetch
  // doesn't re-run on every render (knownWallets is a fresh array each time).
  const addrKey = knownWallets.map((w) => w.address).join(",");

  // Only fetch what is actually showing — a docked sidebar is open on every
  // page, and a per-wallet balance sweep on every mount would be a request
  // storm for something nobody is looking at. Expanded shows one wallet, so
  // that's one fetch; the rest wait for the SWITCH ACCOUNT fold to open.
  const activeAddr = auth.address?.toLowerCase() ?? null;
  useEffect(() => {
    if (!expanded || !addrKey) return;
    const addrs = addrKey
      .split(",")
      .filter(Boolean)
      .filter((a) => showSwitch || a.toLowerCase() === activeAddr);
    let cancelled = false;
    (async () => {
      const entries = await Promise.all(
        addrs.map(async (addr) => [addr.toLowerCase(), await fundedUsd(addr)] as const),
      );
      if (!cancelled) setBalances((prev) => ({ ...prev, ...Object.fromEntries(entries) }));
    })();
    return () => { cancelled = true; };
  }, [expanded, showSwitch, addrKey, activeAddr]);

  // The header chip asks for this section by name.
  useEffect(() => {
    const onOpen = () => setExpanded(true);
    window.addEventListener(OPEN_ACCOUNTS_EVENT, onOpen);
    return () => window.removeEventListener(OPEN_ACCOUNTS_EVENT, onOpen);
  }, []);

  const others = knownWallets.filter((w) => w.address.toLowerCase() !== activeAddr);
  const active = knownWallets.find((w) => w.address.toLowerCase() === activeAddr);

  // Trading-ready status folded into one dot, same vocabulary as the header
  // chip: bright green = CLOB authenticated, amber = connected but read-only,
  // gray = signed out.
  const dotColor = !auth.connected
    ? "bg-pixel-gray"
    : auth.authenticated
      ? "bg-green-400"
      : "bg-amber-400";

  /** Funded value for a wallet (null = unavailable, undefined = loading). */
  const fundedChip = (address: string) => {
    const v = balances[address.toLowerCase()];
    if (v === undefined) {
      return <span className="text-[10px] font-mono text-pixel-gray/50 animate-pulse">···</span>;
    }
    if (v === null) return <span className="text-[10px] font-mono text-pixel-gray/50">—</span>;
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

  const copyActive = useCallback(async () => {
    if (!auth.address) return;
    try {
      await navigator.clipboard.writeText(auth.address);
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    } catch {}
  }, [auth.address]);

  const startEdit = useCallback((address: string, current?: string) => {
    setEditing(address);
    setDraftLabel(current ?? "");
  }, []);

  const commitEdit = useCallback((address: string) => {
    renameWallet(address, draftLabel);
    setEditing(null);
  }, [renameWallet, draftLabel]);

  return (
    <div className="shrink-0" style={{ borderBottom: "1px solid var(--border)" }}>
      {/* ── Header row: who you are, click to expand the switcher. The
             sidebar's close × rides along at the end — a sibling, not a child,
             since a button can't nest inside a button. ── */}
      <div className="flex items-stretch min-h-[48px]">
        <button
          onClick={() => setExpanded((e) => !e)}
          className="flex-1 min-w-0 px-3 py-2 flex items-center gap-2 text-left hover:bg-pixel-white/[0.06] transition-colors"
          aria-expanded={expanded}
          title={
            auth.connected && auth.address
              ? `${auth.address} · ${auth.authenticated ? "trading enabled" : "trading not yet enabled"}`
              : "Not signed in"
          }
        >
          <div className={`w-1.5 h-1.5 rounded-full ${dotColor} shrink-0`} />
          <span className="min-w-0 flex-1">
            <span className="block text-[9.5px] font-mono tracking-[0.14em] text-pixel-gray">
              ACCOUNT
              {knownWallets.length > 1 && (
                <span className="text-pixel-gray/70"> · {knownWallets.length} known</span>
              )}
            </span>
            <span className="block truncate text-[11.5px] font-mono text-green-400">
              {auth.connected && auth.address
                ? active?.label || shortAddress(auth.address).toLowerCase()
                : hasWallet ? "not signed in" : "no wallet"}
            </span>
          </span>
          <span className="text-[9px] text-pixel-gray shrink-0">{expanded ? "▲" : "▼"}</span>
        </button>
        {onClose && (
          <button
            onClick={onClose}
            title="Hide this sidebar"
            className="self-center mr-2 grid place-items-center w-[24px] h-[24px] rounded-[var(--radius-sm)] border border-pixel-border text-pixel-gray hover:text-pixel-white hover:border-pixel-white/40 transition-colors text-[13px] leading-none shrink-0"
          >
            ×
          </button>
        )}
      </div>

      {expanded && (
        <div className="px-2 pb-2 space-y-1.5">
          {/* ── The signed-in wallet, as a card. This is the whole point of
                 the block — everything else folds away beneath it. ── */}
          {auth.connected && auth.address ? (
            <div className="rounded-[var(--radius-sm)] border border-pixel-border/60 bg-pixel-white/[0.03] px-2.5 py-2">
              <div className="flex items-center gap-2">
                <div className="min-w-0 flex-1">
                  {active?.label && (
                    <div className="text-[11.5px] text-pixel-white truncate">{active.label}</div>
                  )}
                  <div className="font-mono text-[11px] text-green-400 truncate" title={auth.address}>
                    {shortAddress(auth.address).toLowerCase()}
                  </div>
                </div>
                <div className="flex flex-col items-end shrink-0">
                  {fundedChip(auth.address)}
                  <span className={`text-[9.5px] font-mono ${auth.authenticated ? "text-green-400/80" : "text-amber-400"}`}>
                    {auth.authenticated ? "CLOB ✓" : "CLOB…"}
                  </span>
                </div>
              </div>
              <div
                className="flex items-center gap-1.5 mt-2 pt-1.5"
                style={{ borderTop: "1px solid var(--border)" }}
              >
                <button
                  onClick={copyActive}
                  className="text-[10px] tracking-[0.12em] text-pixel-gray hover:text-green-400 rounded-[3px] border border-pixel-border/60 hover:border-green-400/60 px-2 py-0.5 transition-colors"
                >
                  {copied ? "copied ✓" : "copy"}
                </button>
                <button
                  onClick={() => startEdit(auth.address!, active?.label)}
                  className="text-[10px] tracking-[0.12em] text-pixel-gray hover:text-pixel-white rounded-[3px] border border-pixel-border/60 hover:border-pixel-white/60 px-2 py-0.5 transition-colors"
                >
                  rename
                </button>
                <button
                  onClick={() => disconnect()}
                  className="ml-auto text-[10px] tracking-[0.12em] text-red-400/70 hover:text-red-400 rounded-[3px] border border-red-400/30 hover:border-red-400/70 px-2 py-0.5 transition-colors"
                >
                  sign out
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
                    save
                  </button>
                </div>
              )}
            </div>
          ) : (
            <div className="rounded-[var(--radius-sm)] border border-pixel-border/60 bg-pixel-white/[0.03] px-2.5 py-2 flex items-center justify-between gap-2">
              <span className="text-[11px] text-pixel-gray">
                {hasWallet ? "Not signed in." : "No wallet extension."}
              </span>
              <button
                onClick={() => { if (hasWallet) void connect(); }}
                disabled={!hasWallet || loading}
                className="pixel-btn normal-case text-[11px] px-2 py-1 border-green-400 text-green-400 hover:bg-green-400/10 disabled:opacity-40"
              >
                {loading ? "..." : "connect"}
              </button>
            </div>
          )}

          {/* ── SWITCH ACCOUNT — the other known wallets + "sign in another
                 person", folded and CLOSED by default: the card above is the
                 answer to "who am I"; this is the once-in-a-while act. ── */}
          {(others.length > 0 || hasWallet) && (
            <button
              onClick={() => setShowSwitch((v) => !v)}
              className="block px-1 text-[10px] font-mono tracking-[0.16em] text-pixel-gray hover:text-pixel-white transition-colors"
              aria-expanded={showSwitch}
            >
              {showSwitch
                ? "⌃ HIDE OTHER ACCOUNTS"
                : `⌄ SWITCH ACCOUNT${others.length > 0 ? ` · ${others.length} MORE` : ""}`}
            </button>
          )}
          {showSwitch && others.length > 0 && (
            <div className="max-h-[220px] overflow-y-auto rounded-[var(--radius-sm)] border border-pixel-border/40">
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
                        save
                      </button>
                    </>
                  ) : (
                    <>
                      <button
                        onClick={() => { void switchToWallet(w.address); }}
                        className="min-w-0 flex-1 text-left"
                        title={`Sign in as ${w.address}`}
                      >
                        {w.label && (
                          <div className="text-[11.5px] text-pixel-white truncate">{w.label}</div>
                        )}
                        <div className="font-mono text-[11px] text-pixel-gray group-hover:text-green-400 truncate">
                          {shortAddress(w.address).toLowerCase()}
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

          {/* ── Add / sign in another person — lives INSIDE the fold: it is
                 part of the same act as switching. ── */}
          {showSwitch && (
            <div className="px-1">
              <button
                onClick={() => { void addWallet(); }}
                disabled={!hasWallet || loading}
                className="w-full pixel-btn normal-case text-[11px] px-2 py-1 border-green-400/60 text-green-400 hover:bg-green-400/10 disabled:opacity-40"
                title="Open MetaMask to sign in as another person"
              >
                {loading ? "..." : "+ sign in another person"}
              </button>
              {!hasWallet && (
                <div className="text-[10px] text-pixel-gray mt-1.5 text-center">
                  Install a wallet extension to switch accounts.
                </div>
              )}
            </div>
          )}

          {/* ── Carry this session to another device ──
              The wallet + local-token + sign-in-QR panel. It used to sit on a
              WALLET subtab inside the live workspace, next to the deposit
              forms, which made "pair my phone" a thing you found while
              looking for money. It is about WHO you are signed in as, so it
              belongs here — folded away, because it is a once-per-device act
              and the block above is what you open this column for. */}
          <div className="px-1">
            <button
              onClick={() => setShowPairing((v) => !v)}
              className="text-[10px] font-mono tracking-[0.16em] text-pixel-gray hover:text-pixel-white transition-colors"
              aria-expanded={showPairing}
            >
              {showPairing ? "⌃ HIDE DEVICE PAIRING" : "⌄ USE THIS ACCOUNT ON ANOTHER DEVICE"}
            </button>
            {showPairing && (
              <div className="pt-2">
                <WalletTokenPanel />
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
