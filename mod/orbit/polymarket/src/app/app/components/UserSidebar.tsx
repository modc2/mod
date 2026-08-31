"use client";

// THE USER SIDEBAR — who you are, and who you copy, in one right-hand column.
//
// The header's wallet chip has always dispatched OPEN_ACCOUNTS_EVENT asking
// for a column to open; when the strat picker came out of the top bar that
// column went with it, and the chip was left pointing at nothing. This is the
// column, rebuilt around what the console is actually for: not "which of my
// eight strategies is selected" but "whose trades am I copying, with how
// much, and would that much have worked".
//
// Two blocks, in the order the decision is made:
//
//   ACCOUNT  (AccountsPanel)  — every wallet this browser has signed in as and
//                               the USDC each holds. It carries the column's
//                               × close, so the user block IS the header.
//   COPY     (CopyPanel)      — the copy book: pick a leader, set the dollars
//                               behind them, replay $N over the last M days,
//                               start or stop each one.
//
// They are one column because they are one question. A copy session, its
// ledger and its money are all keyed by (wallet, leader): "whose money" and
// "whose trades" answered two routes apart is how a console ends up funding a
// wallet that isn't the one running.
//
// Framing rules, inherited from the column this replaces because they were
// right: on a wide viewport (≥1024px) it DOCKS — no backdrop, no dimming, no
// scroll lock, no click-out — and the page insets by `--strat-dock` (the var
// keeps its old name; layout.tsx and BuildBadge already consume it). Below
// 1024px there's no room for a column, so it falls back to a modal drawer with
// Escape and click-out. Open/closed is remembered across navigation, and the
// column is portaled to <body> because the TopBar's backdrop-blur makes the
// header a containing block for fixed children — rendered in place it would be
// clipped to a 48px strip.

import { useCallback, useEffect, useLayoutEffect, useState } from "react";
import { createPortal } from "react-dom";
import { usePathname } from "next/navigation";
import { useEmbedded } from "../lib/embedded";
import AccountsPanel, { OPEN_ACCOUNTS_EVENT } from "./AccountsPanel";
import CopyPanel from "./CopyPanel";
import DeskRoster from "./DeskRoster";
import SelectionTray from "./SelectionTray";

/** Anything can ask for the column by name — the finder's "SHOW PANEL →"
    dispatches this when rows get checked with the column closed. */
export const OPEN_SIDEBAR_EVENT = "poly-open-sidebar";

const DOCK_MQ = "(min-width: 1024px)";
const DOCK_KEY = "poly_user_sidebar";

// TopBar (and this with it) remounts on every navigation, so a docked column
// has to restore itself BEFORE paint or the console visibly un-insets and
// re-insets on each route change. Layout effects don't run on the server.
const useIsoLayoutEffect = typeof window === "undefined" ? useEffect : useLayoutEffect;

export default function UserSidebar() {
  const embedded = useEmbedded();
  const pathname = usePathname() || "";
  const [open, setOpen] = useState(false);
  const [docked, setDocked] = useState(false);
  // Set when the wallet chip asks for the accounts block by name — the column
  // may not have been mounted yet when the event fired.
  const [accountsWanted, setAccountsWanted] = useState(false);

  useIsoLayoutEffect(() => {
    const mq = window.matchMedia(DOCK_MQ);
    setDocked(mq.matches);
    try {
      if (mq.matches && localStorage.getItem(DOCK_KEY) !== "0") setOpen(true);
    } catch {
      if (mq.matches) setOpen(true);
    }
    const onChange = (e: MediaQueryListEvent) => setDocked(e.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  /** Open/close, remembering the choice. Written here rather than in an
      effect: an effect would fire on mount with the pre-restore value and
      clobber what was saved. */
  const setDrawer = useCallback((next: boolean) => {
    setOpen(next);
    try {
      localStorage.setItem(DOCK_KEY, next ? "1" : "0");
    } catch {}
  }, []);

  useEffect(() => {
    const onOpen = () => { setAccountsWanted(true); setDrawer(true); };
    // Open WITHOUT forcing the accounts block — the caller wants the column
    // (the selection tray, the copy book), not the wallet list.
    const onOpenPlain = () => setDrawer(true);
    window.addEventListener(OPEN_ACCOUNTS_EVENT, onOpen);
    window.addEventListener(OPEN_SIDEBAR_EVENT, onOpenPlain);
    return () => {
      window.removeEventListener(OPEN_ACCOUNTS_EVENT, onOpen);
      window.removeEventListener(OPEN_SIDEBAR_EVENT, onOpenPlain);
    };
  }, [setDrawer]);

  // Inset the console for the docked column (CSS var, read by .crt-screen in
  // layout.tsx and by BuildBadge).
  useEffect(() => {
    const el = document.documentElement;
    if (open && docked) el.dataset.stratDock = "open";
    else delete el.dataset.stratDock;
    return () => { delete el.dataset.stratDock; };
  }, [open, docked]);

  // Escape + scroll lock belong to the OVERLAY only. A docked column is
  // furniture: the page behind it stays scrollable, and Escape belongs to
  // whatever modal that page has open.
  useEffect(() => {
    if (!open || docked) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setDrawer(false); };
    document.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [open, docked, setDrawer]);

  // Embedded split-screen panes stay lightweight, same as NavMenu.
  if (embedded) return null;

  // On the desk itself the column's copy-book block would be the same
  // CONTROLS twice, but the desk page scrolls the selection off-screen — so
  // there it carries a read-only roster of who's selected (finder picks +
  // the book), with the walkthrough folded beneath. Everywhere else it's the
  // full copy book you carry while browsing traders and markets.
  const onDesk = pathname === "/copy";
  const column = (
    <>
      <AccountsPanel initialExpanded={accountsWanted} onClose={() => setDrawer(false)} />
      <div className="flex-1 overflow-y-auto">
        {/* The finder's checked shortlist — replayed, sized and committed
            right here. Renders nothing while nothing is checked. */}
        <SelectionTray />
        {onDesk ? <DeskRoster /> : <CopyPanel />}
      </div>
    </>
  );

  const dockedSidebar = (
    <aside
      className="fixed inset-y-0 right-0 z-30 w-[var(--strat-dock)] flex flex-col backdrop-blur-md"
      style={{
        background:
          "linear-gradient(180deg, rgb(var(--pixel-black-rgb)/0.97), rgb(var(--pixel-bg-rgb)/0.95))",
        borderLeft: "1px solid var(--border)",
        animation: "drawer-in-right 0.18s ease-out",
      }}
    >
      {column}
    </aside>
  );

  const overlaySidebar = (
    <div className="fixed inset-0 z-50" onClick={() => setDrawer(false)}>
      <div className="absolute inset-0" style={{ background: "rgb(var(--pixel-black-rgb)/0.35)" }} />
      <aside
        onClick={(e) => e.stopPropagation()}
        className="absolute inset-y-0 right-0 w-[340px] max-w-[85vw] flex flex-col backdrop-blur-md"
        style={{
          background:
            "linear-gradient(180deg, rgb(var(--pixel-black-rgb)/0.97), rgb(var(--pixel-bg-rgb)/0.95))",
          borderLeft: "1px solid var(--border)",
          boxShadow: "-12px 0 32px rgba(0,0,0,0.45)",
          animation: "drawer-in-right 0.18s ease-out",
        }}
      >
        {column}
      </aside>
    </div>
  );

  return (
    <>
      <button
        onClick={() => setDrawer(!open)}
        aria-expanded={open}
        aria-label="Side panel"
        title={`${open ? "Hide" : "Show"} the side panel — your wallets, and who you copy`}
        className={`flex items-center px-2 py-2 rounded-[var(--radius-sm)] transition-colors shrink-0 ${
          open ? "bg-pixel-white/[0.06] text-green-400" : "text-pixel-gray hover:bg-pixel-white/[0.06]"
        }`}
      >
        {/* The RIGHT rail fills in when the column is showing — the glyph
            points at the edge the column actually comes from. */}
        <svg className="w-[15px] h-[15px]" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <rect x="3" y="4" width="18" height="16" rx="2" />
          {open ? <rect x="15" y="4" width="6" height="16" rx="2" fill="currentColor" /> : <path d="M15 4v16" />}
        </svg>
      </button>

      {open && createPortal(docked ? dockedSidebar : overlaySidebar, document.body)}
    </>
  );
}
