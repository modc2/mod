"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import { shortAddr } from "../lib/api";
import { useWallet } from "../lib/wallet";

const NAV = [
  { href: "/", label: "Traders" },
  { href: "/vaults", label: "Vaults" },
  { href: "/strats", label: "Strats" },
  { href: "/follows", label: "My Follows" },
  { href: "/signals", label: "Signals" },
  { href: "/live", label: "Live" },
];

export default function Header() {
  const path = usePathname();
  const { address, setAddress } = useWallet();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(address ?? "");

  const save = () => {
    const v = draft.trim();
    if (v && /^0x[a-fA-F0-9]{40}$/.test(v)) {
      setAddress(v);
      setEditing(false);
    }
  };

  return (
    <header className="sticky top-0 z-30 border-b border-white/[0.06] bg-bg/70 backdrop-blur-xl">
      <div className="max-w-7xl mx-auto px-4 h-16 flex items-center gap-7">
        <Link href="/" className="group flex items-center gap-2.5 shrink-0">
          <span className="relative grid place-items-center h-7 w-7 rounded-md bg-accent-grad shadow-glow">
            <span className="absolute inset-0 rounded-md bg-accent-grad blur-md opacity-50 group-hover:opacity-80 transition-opacity" />
            <span className="relative text-bg font-display font-bold text-sm">H</span>
          </span>
          <span className="font-display font-bold text-[15px] tracking-tight text-gradient">
            Hyperliquid
          </span>
        </Link>
        <nav className="flex items-center gap-1">
          {NAV.map((n) => {
            const active = path === n.href || (n.href !== "/" && path?.startsWith(n.href));
            return (
              <Link key={n.href} href={n.href}
                className={`relative text-[11px] font-medium uppercase tracking-wider px-3 py-1.5 rounded-md transition-all duration-150
                  ${active
                    ? "text-accent bg-accent/10 shadow-[inset_0_0_0_1px_rgba(60,224,200,0.25)]"
                    : "text-muted hover:text-ink hover:bg-white/[0.04]"}`}>
                {n.label}
              </Link>
            );
          })}
        </nav>
        <div className="ml-auto flex items-center gap-2">
          {editing ? (
            <>
              <input
                className="input w-[26ch] font-mono"
                placeholder="0x… wallet"
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
              <span className="inline-flex items-center gap-1.5 pill border-accent/30 text-accent font-mono">
                <span className="h-1.5 w-1.5 rounded-full bg-accent live-dot" />
                {shortAddr(address)}
              </span>
              <button className="btn" onClick={() => { setDraft(address); setEditing(true); }}>change</button>
              <button className="btn-danger" onClick={() => setAddress(null)}>disconnect</button>
            </>
          ) : (
            <button className="btn-primary" onClick={() => setEditing(true)}>connect wallet</button>
          )}
        </div>
      </div>
    </header>
  );
}
