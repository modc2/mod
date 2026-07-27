"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import { shortAddr } from "../lib/api";
import { useWallet } from "../lib/wallet";
import ThemeToggle from "./ThemeToggle";

const NAV = [
  { href: "/", label: "Traders" },
  { href: "/vaults", label: "Vaults" },
  { href: "/strats", label: "Strats" },
  { href: "/follows", label: "Follows" },
  { href: "/signals", label: "Signals" },
  { href: "/live", label: "Live" },
  { href: "/wallet", label: "Wallet" },
];

export default function Header() {
  const path = usePathname();
  const { address, kind, hasProvider, token, signIn, connect, watch, disconnect } = useWallet();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(address ?? "");
  const [connectErr, setConnectErr] = useState<string | null>(null);

  const save = () => {
    const v = draft.trim();
    if (v && /^0x[a-fA-F0-9]{40}$/.test(v)) {
      watch(v);
      setEditing(false);
    }
  };

  const onConnect = async () => {
    setConnectErr(null);
    if (!hasProvider) { setEditing(true); return; } // no MetaMask → watch-address fallback
    try {
      await connect();
    } catch (e: any) {
      setConnectErr(e?.code === 4001 ? null : String(e?.message ?? e));
    }
  };

  return (
    <header className="sticky top-0 z-30 border-b border-white/[0.06] bg-bg/70 backdrop-blur-xl">
      <div className="max-w-7xl mx-auto px-4 h-16 flex items-center gap-4">
        <Link href="/" className="group flex items-center gap-2.5 shrink-0">
          <span className="relative grid place-items-center h-7 w-7 rounded-md bg-accent-grad shadow-glow">
            <span className="absolute inset-0 rounded-md bg-accent-grad blur-md opacity-50 group-hover:opacity-80 transition-opacity" />
            <span className="relative text-bg font-display font-bold text-sm">H</span>
          </span>
          <span className="font-display font-bold text-[15px] tracking-tight text-gradient">
            Hyperliquid
          </span>
        </Link>
        <nav className="flex items-center gap-0.5 overflow-x-auto min-w-0 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
          {NAV.map((n) => {
            const active = path === n.href || (n.href !== "/" && path?.startsWith(n.href));
            return (
              <Link key={n.href} href={n.href}
                className={`relative whitespace-nowrap text-[11px] font-medium uppercase tracking-wider px-2.5 py-1.5 rounded-md transition-all duration-150
                  ${active
                    ? "text-accent bg-accent/10 shadow-[inset_0_0_0_1px_rgb(var(--c-accent)/0.3)]"
                    : "text-muted hover:text-ink hover:bg-white/[0.04]"}`}>
                {n.label}
              </Link>
            );
          })}
        </nav>
        <div className="ml-auto flex items-center gap-2 shrink-0">
          <ThemeToggle />
          {editing ? (
            <>
              <input
                className="input w-[26ch] font-mono"
                placeholder="0x… watch address"
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && save()}
                autoFocus
              />
              <button className="btn-primary" onClick={save}>save</button>
              <button className="btn" onClick={() => setEditing(false)}>cancel</button>
            </>
          ) : address ? (
            <>
              {connectErr && <span className="text-[10px] text-loss max-w-[24ch] truncate" title={connectErr}>{connectErr}</span>}
              <span className="inline-flex items-center gap-1.5 pill border-accent/30 text-accent font-mono">
                <span className={`h-1.5 w-1.5 rounded-full ${token ? "bg-accent live-dot" : "bg-warn"}`} />
                {shortAddr(address)}
                {kind === "watch" && (
                  <span className="text-[9px] uppercase tracking-wider text-warn">watch</span>
                )}
              </span>
              {kind === "metamask" && !token && (
                <button
                  className="btn-primary"
                  title="Sign a message to authenticate with the API"
                  onClick={async () => {
                    setConnectErr(null);
                    try { await signIn(); }
                    catch (e: any) { setConnectErr(e?.code === 4001 ? null : String(e?.message ?? e)); }
                  }}
                >
                  sign in
                </button>
              )}
              {kind === "watch" && hasProvider && (
                <button className="btn" onClick={onConnect} title="Upgrade to a signing connection">metamask</button>
              )}
              <button className="btn-danger" onClick={disconnect}>disconnect</button>
            </>
          ) : (
            <>
              {connectErr && <span className="text-[10px] text-loss max-w-[24ch] truncate" title={connectErr}>{connectErr}</span>}
              <button className="btn-primary" onClick={onConnect}
                title={hasProvider ? "Connect MetaMask" : "Connect a wallet"}>
                connect
              </button>
              {hasProvider && (
                <button className="btn" onClick={() => setEditing(true)} title="Track an address without signing">watch</button>
              )}
            </>
          )}
        </div>
      </div>
    </header>
  );
}
