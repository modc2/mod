"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { shortAddr } from "../lib/api";
import { useWallet } from "../lib/wallet";
import { useSession } from "../lib/auth";
import ThemePicker from "./ThemePicker";
import { useDock } from "../lib/dock";
import { WATCH_EVENT } from "./AccountPanel";

// The console is seven nouns: four places to find something to back, one book
// of what you've backed, the desk agent, and the chatbot. Money (deposit,
// withdraw, send) is the account chip → desk wallet tab. The two older copy
// engines the invest book superseded and the plumbing pages live under MORE, which also holds the primary links when they're hidden on a
// narrow viewport, so no page is ever unreachable.
const NAV = [
  { href: "/", label: "Traders" },
  { href: "/market", label: "Market" },
  { href: "/invest", label: "Invest" },
  { href: "/strats", label: "Strats" },
  { href: "/vaults", label: "Vaults" },
  { href: "/ask", label: "Ask" },
  { href: "/chat", label: "Chat" },
];

const MORE = [
  { href: "/live", label: "Live engine" },
  { href: "/follows", label: "Follows" },
  { href: "/signals", label: "Signals" },
  { href: "/sync", label: "Data" },
  { href: "/mcp", label: "MCP" },
];

const isActive = (path: string | null, href: string) =>
  path === href || (href !== "/" && !!path?.startsWith(href));

/** Close a popover on outside click or Escape. */
function useDismiss(open: boolean, close: () => void) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) close();
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && close();
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, close]);
  return ref;
}

const chevron = (open: boolean) => (
  <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"
    className={`transition-transform duration-150 ${open ? "rotate-180" : ""}`}>
    <path d="m6 9 6 6 6-6" />
  </svg>
);

// ── Nav ───────────────────────────────────────────────────────────────────
const linkCls = (active: boolean) =>
  `whitespace-nowrap text-[11px] font-medium uppercase tracking-wider px-2.5 py-1.5 rounded-md transition-all duration-150
   ${active
     ? "text-accent bg-accent/10 shadow-[inset_0_0_0_1px_rgb(var(--c-accent)/0.3)]"
     : "text-muted hover:text-ink hover:bg-white/[0.04]"}`;

const itemCls = (active: boolean) =>
  `block px-2 py-1.5 rounded text-[11px] font-medium uppercase tracking-wider transition-colors
   ${active ? "text-accent bg-accent/10" : "text-muted hover:text-ink hover:bg-white/[0.05]"}`;

function NavBar({ path }: { path: string | null }) {
  const [open, setOpen] = useState(false);
  const menuRef = useDismiss(open, useCallback(() => setOpen(false), []));
  const activeMore = MORE.find((n) => isActive(path, n.href));

  return (
    <nav className="flex items-center gap-0.5 min-w-0">
      {NAV.map((n) => (
        <Link key={n.href} href={n.href}
          className={`hidden md:inline-block ${linkCls(isActive(path, n.href))}`}>
          {n.label}
        </Link>
      ))}

      <div className="relative" ref={menuRef}>
        <button
          className={`${linkCls(!!activeMore)} inline-flex items-center gap-1.5`}
          onClick={() => setOpen((o) => !o)}
          aria-haspopup="menu"
          aria-expanded={open}
          aria-label="More pages"
        >
          {activeMore?.label ?? "More"}
          {chevron(open)}
        </button>
        {open && (
          <div role="menu"
            className="panel bg-panel absolute left-0 mt-2 w-44 p-1.5 z-50 shadow-lift animate-fadeUp">
            {/* Below md the primary links have no row to sit in — they fold
                in here rather than disappearing. */}
            <div className="md:hidden">
              {NAV.map((n) => (
                <Link key={n.href} href={n.href} role="menuitem" onClick={() => setOpen(false)}
                  className={itemCls(isActive(path, n.href))}>{n.label}</Link>
              ))}
              <div className="my-1 border-t border-white/[0.06]" />
            </div>
            {MORE.map((n) => (
              <Link key={n.href} href={n.href} role="menuitem" onClick={() => setOpen(false)}
                className={itemCls(isActive(path, n.href))}>{n.label}</Link>
            ))}
          </div>
        )}
      </div>
    </nav>
  );
}

// ── Account ───────────────────────────────────────────────────────────────
// The chip IS the way into your account: it opens the desk's wallet tab,
// where every money operation lives (deposit, withdraw, perp/spot, send,
// agent auth, sign in, disconnect) — no menu in between.
function AccountChip() {
  const { address } = useWallet();
  // Not `!!token` — a restored token is a claim until /auth/me agrees with it.
  const { canWrite, isWatching } = useSession();
  const { open, tab, toggle } = useDock();
  if (!address) return null;
  const status = canWrite ? "Signed in" : isWatching ? "Watch only" : "Sign in to trade";
  const on = open && tab === "wallet";
  return (
    <button
      className={`btn !px-2.5 gap-1.5 font-mono !text-[11px] !text-ink ${on ? "!border-accent/40 bg-accent/10" : ""}`}
      onClick={() => toggle("wallet")}
      aria-expanded={on}
      title={`${address} — ${status} · deposit, withdraw, send`}
    >
      <span className={`h-1.5 w-1.5 rounded-full shrink-0 ${canWrite ? "bg-accent live-dot" : "bg-warn"}`} />
      {shortAddr(address)}
    </button>
  );
}

// ── Desk handle ───────────────────────────────────────────────────────────
// One handle for the right-hand column: the agent, the strats you manage and
// your wallet. It lights up while the desk is open so the header always says
// where that column came from.
function DeskToggle() {
  const { open, toggle } = useDock();
  return (
    <button
      onClick={() => toggle()}
      aria-expanded={open}
      aria-label="Desk — agent, my strats, my wallet"
      title="Desk — agent, my strats, my wallet"
      className={`btn !px-2.5 gap-1.5 ${open ? "!text-accent !border-accent/40 bg-accent/10" : ""}`}
    >
      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor"
        strokeWidth="2" strokeLinejoin="round" aria-hidden="true">
        <rect x="3" y="4" width="18" height="16" rx="2" />
        <path d="M15 4v16" />
      </svg>
      <span className="hidden sm:inline">desk</span>
    </button>
  );
}

export default function Header() {
  const path = usePathname();
  const { address, hasProvider, connect, watch } = useWallet();
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

  // MetaMask's own reject (4001) is a user decision, not an error to report.
  const report = (e: any) => setConnectErr(e?.code === 4001 ? null : String(e?.message ?? e));

  const onConnect = async () => {
    setConnectErr(null);
    if (!hasProvider) { setEditing(true); return; } // no MetaMask → watch-address fallback
    try { await connect(); } catch (e) { report(e); }
  };

  // The desk's wallet tab asks for the watch-address input; it lives here.
  useEffect(() => {
    const onWatch = () => { setDraft(""); setEditing(true); };
    window.addEventListener(WATCH_EVENT, onWatch);
    return () => window.removeEventListener(WATCH_EVENT, onWatch);
  }, []);

  return (
    <header className="sticky top-0 z-30 border-b border-white/[0.06] bg-bg/70 backdrop-blur-xl">
      <div className="max-w-7xl mx-auto px-4 h-16 flex items-center gap-3 sm:gap-4">
        <Link href="/" className="group flex items-center gap-2.5 shrink-0" aria-label="Hyperliquid — home">
          <span className="relative grid place-items-center h-7 w-7 rounded-md bg-accent-grad shadow-glow">
            <span className="absolute inset-0 rounded-md bg-accent-grad blur-md opacity-50 group-hover:opacity-80 transition-opacity" />
            <span className="relative text-bg font-display font-bold text-sm">H</span>
          </span>
          <span className="hidden sm:inline font-display font-bold text-[15px] tracking-tight text-gradient">
            Hyperliquid
          </span>
        </Link>

        {editing ? (
          <div className="flex-1 min-w-0 flex items-center gap-2">
            <input
              className="input flex-1 min-w-0 max-w-[46ch] font-mono text-xs"
              placeholder="0x… watch address"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") save();
                if (e.key === "Escape") setEditing(false);
              }}
              autoFocus
            />
            <button className="btn-primary shrink-0" onClick={save}>save</button>
            <button className="btn shrink-0" onClick={() => setEditing(false)}>cancel</button>
          </div>
        ) : (
          <NavBar path={path} />
        )}

        {/* While the watch-address input is up it owns the bar: on a narrow
            viewport the wallet controls would squeeze it down to a few
            characters, and none of them are needed mid-edit. */}
        <div className={`ml-auto items-center gap-2 shrink-0 ${editing ? "hidden" : "flex"}`}>
          <DeskToggle />
          <ThemePicker />
          {address ? (
            <>
              <AccountChip />
            </>
          ) : (
            <>
              {connectErr && (
                <span className="hidden md:block text-[10px] text-loss max-w-[24ch] truncate" title={connectErr}>
                  {connectErr}
                </span>
              )}
              <button className="btn-primary" onClick={onConnect}
                title={hasProvider ? "Connect MetaMask" : "Connect a wallet"}>
                connect
              </button>
              {hasProvider && (
                <button className="btn hidden sm:inline-flex" onClick={() => { setDraft(""); setEditing(true); }}
                  title="Track an address without signing">
                  watch
                </button>
              )}
            </>
          )}
        </div>
      </div>
    </header>
  );
}
