"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import { shortAddr } from "../lib/api";
import { useWallet } from "../lib/wallet";

const NAV = [
  { href: "/", label: "Traders" },
  { href: "/indexes", label: "Indexes" },
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
    <header className="border-b border-border sticky top-0 z-20 bg-bg/95 backdrop-blur">
      <div className="max-w-7xl mx-auto px-4 h-14 flex items-center gap-6">
        <Link href="/" className="text-sm tracking-widest text-accent">HYPERLIQ•COPY</Link>
        <nav className="flex gap-1">
          {NAV.map((n) => {
            const active = path === n.href || (n.href !== "/" && path?.startsWith(n.href));
            return (
              <Link key={n.href} href={n.href}
                className={`text-[11px] uppercase tracking-wider px-3 py-1 rounded
                  ${active ? "bg-panel2 text-accent" : "text-muted hover:text-ink"}`}>
                {n.label}
              </Link>
            );
          })}
        </nav>
        <div className="ml-auto flex items-center gap-2">
          {editing ? (
            <>
              <input
                className="input w-[26ch]"
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
              <span className="pill border-accent2/40 text-accent2">{shortAddr(address)}</span>
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
