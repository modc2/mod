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
// Four blocks, in the order the decision is made:
//
//   ACCOUNT  (AccountsPanel)  — every wallet this browser has signed in as and
//                               the USDC each holds. It carries the column's
//                               × close, so the user block IS the header.
//   MONEY    (MoneyBlock)     — topping up and taking money back out. It was a
//                               subtab of the live workspace, which put funding
//                               a navigation away from every screen that needed
//                               it; money is a drawer, not a destination. Any
//                               screen that finds itself short fires
//                               OPEN_MONEY_EVENT and this opens over it.
//   STRATS   (StratBlock)     — the saved strategies, and which one BACKTEST
//                               and LIVE are looking at. The default one is a
//                               TRADER INDEX: every trade the bench makes,
//                               copied 1:1 and scaled by your capital against
//                               that trader's own book.
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
import MoneyBlock, { OPEN_MONEY_EVENT } from "./MoneyBlock";
import StratBlock, { OPEN_STRATS_EVENT } from "./StratBlock";
import StratsTab from "./StratsTab";
import DeskRoster from "./DeskRoster";
import IndexBench from "./IndexBench";
import SelectionTray from "./SelectionTray";
import Workspace from "./Workspace";

/** Anything can ask for the column by name — the finder's "SHOW PANEL →"
    dispatches this when rows get checked with the column closed. */
export const OPEN_SIDEBAR_EVENT = "poly-open-sidebar";

// ── The column's TABS ──
//
// The console used to be three PAGES (TRADERS · BACKTEST · LIVE). The user
// asked for one: the board fills the screen, and testing/running what you
// picked is the side panel's job — tab between them without ever leaving
// the traders you're browsing. So the column carries the rail now:
//
//   INDEX     the account, the MONEY drawer and the ALLOCATION across your
//             strats, plus the bench you're building and the copy book —
//             everything "whose money and how much"
//   STRATS    building and sharing: create/fork/rename/delete strats, the
//             AUTO STRAT factory + STRAT LAB, and publishing — every strat
//             is private by default, flipped public per row (StratsTab)
//   BACKTEST  the full workspace (CopyIndex) replaying the bench on history
//   LIVE      the same workspace against the real book
//
// BACKTEST/LIVE mount the SAME Workspace the old routes rendered (bare —
// no TopBar), and the docked column WIDENS for every non-INDEX tab
// (data-strat-dock="wide" → globals.css) because an engine — or a gallery —
// built for the main pane earns more than 340px. /backtest, /live and
// /strats survive as forwarders into these tabs.
export type SidebarTab = "INDEX" | "STRATS" | "BACKTEST" | "LIVE";
export const SIDEBAR_TAB_EVENT = "poly-sidebar-tab";
const TAB_KEY = "poly_sidebar_tab";
const TABS: SidebarTab[] = ["INDEX", "STRATS", "BACKTEST", "LIVE"];
const TAB_HINTS: Record<SidebarTab, string> = {
  INDEX: "Your wallets, your money, and how it's allocated across your strats",
  STRATS: "Create, build and share strats — private by default, publish to the gallery when ready",
  BACKTEST: "Replay the bench against history on simulated money — no wallet touched",
  LIVE: "Run the bench against the real book with real money",
};
const isSidebarTab = (t: unknown): t is SidebarTab => TABS.includes(t as SidebarTab);

/** Open the side panel on a named tab from anywhere (the /backtest and /live
    forwarders use this). Persists first so a not-yet-mounted column restores
    onto the right tab. */
export function requestSidebarTab(tab: SidebarTab): void {
  try {
    localStorage.setItem(TAB_KEY, tab);
    localStorage.setItem(DOCK_KEY, "1");
  } catch {}
  window.dispatchEvent(new CustomEvent(SIDEBAR_TAB_EVENT, { detail: tab }));
}

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
  const [tab, setTab] = useState<SidebarTab>("INDEX");
  // Set when the wallet chip asks for the accounts block by name — the column
  // may not have been mounted yet when the event fired.
  const [accountsWanted, setAccountsWanted] = useState(false);

  useIsoLayoutEffect(() => {
    const mq = window.matchMedia(DOCK_MQ);
    setDocked(mq.matches);
    try {
      if (mq.matches && localStorage.getItem(DOCK_KEY) !== "0") setOpen(true);
      const t = localStorage.getItem(TAB_KEY);
      if (isSidebarTab(t)) setTab(t);
    } catch {
      if (mq.matches) setOpen(true);
    }
    const onChange = (e: MediaQueryListEvent) => setDocked(e.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  const setTabPersisted = useCallback((next: SidebarTab) => {
    setTab(next);
    try {
      localStorage.setItem(TAB_KEY, next);
    } catch {}
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
    const onOpen = () => { setAccountsWanted(true); setTabPersisted("INDEX"); setDrawer(true); };
    // Open WITHOUT forcing the accounts block — the caller wants the column
    // (the selection tray, the copy book, the money panel), not the wallet
    // list. MoneyBlock listens for OPEN_MONEY_EVENT itself and expands; this
    // only has to make sure there is a column for it to expand INSIDE, which
    // is why the same event is handled in both places rather than relayed.
    const onOpenPlain = () => setDrawer(true);
    // A named-tab ask (the /backtest and /live forwarders, or anything else
    // that wants a specific screen of the column).
    const onTab = (e: Event) => {
      const t = (e as CustomEvent).detail;
      if (isSidebarTab(t)) setTabPersisted(t);
      setDrawer(true);
    };
    // The money/accounts blocks live on INDEX — an ask for one of them from
    // another tab must also bring INDEX forward, or the block expands
    // somewhere the user can't see.
    const onIndexBlock = () => { setTabPersisted("INDEX"); setDrawer(true); };
    // The strat MANAGER moved to its own tab — an ask for "the strats"
    // (AccountsPanel's shortcut, the allocation block's BUILD & SHARE link)
    // opens STRATS, where building and sharing live now.
    const onStratsTab = () => { setTabPersisted("STRATS"); setDrawer(true); };
    window.addEventListener(OPEN_ACCOUNTS_EVENT, onOpen);
    window.addEventListener(OPEN_SIDEBAR_EVENT, onOpenPlain);
    window.addEventListener(OPEN_MONEY_EVENT, onIndexBlock);
    window.addEventListener(OPEN_STRATS_EVENT, onStratsTab);
    window.addEventListener(SIDEBAR_TAB_EVENT, onTab);
    return () => {
      window.removeEventListener(OPEN_ACCOUNTS_EVENT, onOpen);
      window.removeEventListener(OPEN_SIDEBAR_EVENT, onOpenPlain);
      window.removeEventListener(OPEN_MONEY_EVENT, onIndexBlock);
      window.removeEventListener(OPEN_STRATS_EVENT, onStratsTab);
      window.removeEventListener(SIDEBAR_TAB_EVENT, onTab);
    };
  }, [setDrawer, setTabPersisted]);

  // Inset the console for the docked column (CSS var, read by .crt-screen in
  // layout.tsx and by BuildBadge). BACKTEST/LIVE carry the full workspace, so
  // the docked column takes the WIDE width for them (globals.css).
  useEffect(() => {
    const el = document.documentElement;
    if (open && docked) el.dataset.stratDock = tab === "INDEX" ? "open" : "wide";
    else delete el.dataset.stratDock;
    return () => { delete el.dataset.stratDock; };
  }, [open, docked, tab]);

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

      {/* The rail. These were the console's top-level pages — now the board
          stays put and the column tabs between building the index, replaying
          it, and running it. */}
      <div className="flex shrink-0" style={{ borderBottom: "1px solid var(--border)" }}>
        {TABS.map((t) => {
          const active = t === tab;
          return (
            <button
              key={t}
              onClick={() => setTabPersisted(t)}
              title={TAB_HINTS[t]}
              className={`relative flex-1 px-2 py-2 text-[10.5px] font-semibold tracking-[0.16em] transition-colors ${
                active
                  ? t === "LIVE"
                    ? "text-red-400 bg-red-400/10"
                    : "text-green-400 bg-green-400/10"
                  : "text-pixel-gray hover:text-pixel-white hover:bg-pixel-white/[0.06]"
              }`}
            >
              {t}
              <span
                className={`absolute left-2 right-2 bottom-0 h-[2px] rounded-full transition-opacity ${
                  active
                    ? t === "LIVE"
                      ? "bg-red-400 opacity-100 shadow-[0_0_10px_rgba(248,113,113,0.7)]"
                      : "bg-green-400 opacity-100 shadow-[0_0_10px_rgba(74,222,128,0.7)]"
                    : "opacity-0"
                }`}
              />
            </button>
          );
        })}
      </div>

      <div className="flex-1 overflow-y-auto">
        {tab === "INDEX" ? (
          <>
            {/* Money first, under the wallet it belongs to: top up, take out.
                Collapsed to one line until you want it. */}
            <MoneyBlock />
            {/* ALLOCATION — how the wallet is split across your strats, the
                active strat BACKTEST/LIVE point at, and $ ALLOCATE to move
                money amongst them. Building and sharing live on STRATS. */}
            <StratBlock />
            {/* The active strat's bench — every + ADD from the board, each
                with its current board SCORE, toggled or removed right here. */}
            <IndexBench />
            {/* The finder's checked shortlist — replayed, sized and committed
                right here. Renders nothing while nothing is checked. */}
            <SelectionTray />
            {onDesk ? <DeskRoster /> : <CopyPanel />}
          </>
        ) : tab === "STRATS" ? (
          /* Build & share: the strat manager, the factory + lab, the public
             gallery and the CID share path. Private by default throughout. */
          <StratsTab />
        ) : (
          /* The full workspace, bare (no TopBar) — the same component the
             old /backtest and /live pages rendered. Keyed by tab so nothing
             (a half-run replay, a subtab position) leaks between modes. */
          <Workspace key={tab} mode={tab} bare />
        )}
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
        className={`absolute inset-y-0 right-0 flex flex-col backdrop-blur-md ${
          tab === "INDEX" ? "w-[340px] max-w-[85vw]" : "w-[760px] max-w-[95vw]"
        }`}
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
        title={`${open ? "Hide" : "Show"} the side panel — your wallets, your money, and who you copy`}
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
