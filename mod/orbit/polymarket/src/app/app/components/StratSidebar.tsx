"use client";

// The user sidebar: a real RIGHT column that answers "who am I / which strat
// am I on" in one place. Its top block is the ACCOUNT switcher
// (AccountsPanel — every wallet this browser knows, its funded USDC, sign
// in/out); below it is every saved strat — click to switch, ✎ or double-click
// to rename, ⑂ to fork, × to delete — with the section header showing the
// deposit wallet's on-chain USDC and each row showing the money that strat
// actually has in play right now plus its 24h PnL, both from the signed-in
// wallet's engine ledger.
//
// It is furniture, not a menu. On a wide viewport (≥1024px) it DOCKS: no
// backdrop, no dimming, no scroll lock, no click-out-to-close — the page
// insets by its width and it stays open across navigation and reloads, so you
// can edit a strat on the right while its money sits beside it. It opens by
// default there; the user's open/closed choice is remembered. Below 1024px
// there isn't room for a column, so it falls back to a modal overlay drawer.
//
// It sits on the RIGHT, under the wallet chip that opens it: whose money and
// which strategy are one question, so the account switcher and the strat list
// are one column, opened from one corner. The top-LEFT is page navigation and
// nothing else.
//
// The rows show REAL money only. There is deliberately no allocation editor
// here: a `capital` number typed into a sidebar is an intention, not funds,
// and rendering it per row made eight idle strats each claim $1000 of a $223
// wallet. Funding a strat is arming it — that lives in the LIVE panel, which
// starts the backend session (lib/liveSessions.ts). The engine runs one
// session per (wallet, strat), so several strats trade side by side and each
// row prices its own slice; ■ STOP takes a single strat down without
// touching the others.
//
// In the header this component is a one-line readout of the ACTIVE strat —
// name, live dot, trader count, keyword count — sitting immediately left of
// the wallet chip, and both toggle the same column. It is deliberately not a
// dropdown: the list belongs in the column, so the header stays a row of
// things you can read at a glance. + NEW STRAT and ▦ STRAT HUB used to be
// icon buttons next to it; they live in the column now, so the top-right is
// two readouts (who / what) and nothing else.
//
// A DEFAULT STRATS gallery at the bottom forks curated templates
// (lib/defaultStrats.ts) into user-owned strats, and ▦ opens the STRAT HUB
// (/strats), where every strat is a card
// showing its 1-day backtest. Every mutation goes through useStratManager
// (lib/stratManager.ts) — indexStore localStorage store, `strat-updated`
// window event, best-effort server sync — so the hub, CopyIndex, the LIVE
// checklist and this sidebar can never disagree about which strat is active.

import { useCallback, useEffect, useLayoutEffect, useState } from "react";
import { createPortal } from "react-dom";
import { usePathname, useRouter } from "next/navigation";
import { useEmbedded } from "../lib/embedded";
import { useAuth } from "../context/AuthContext";
import { DEFAULT_STRATS, type StratTemplate } from "../lib/defaultStrats";
import { useStratStats, fmtUsd } from "../lib/stratStats";
import { useStratManager } from "../lib/stratManager";
import { describeTraderFilter } from "../lib/strats/strat";
import ConfirmDeleteStrat from "./ConfirmDeleteStrat";
import DepositPanel from "./DepositPanel";
import StratChat from "./StratChat";
import AccountsPanel, { OPEN_ACCOUNTS_EVENT } from "./AccountsPanel";

// Wide enough for a real column beside the console; below this the sidebar
// falls back to a modal overlay. Matches the media query in globals.css that
// gives --strat-dock its width.
const DOCK_MQ = "(min-width: 1024px)";
const DOCK_KEY = "poly_strat_dock";

// TopBar (and this component with it) remounts on every page navigation, so a
// docked sidebar has to restore itself — and restore BEFORE paint, or the
// console visibly un-insets and re-insets on each route change. Layout
// effects don't run on the server; useEffect there keeps React quiet.
const useIsoLayoutEffect = typeof window === "undefined" ? useEffect : useLayoutEffect;

export default function StratSidebar() {
  const embedded = useEmbedded();
  const { auth } = useAuth();
  // The store, the mutations and the server sync all live in the shared strat
  // manager — the HUB drives the exact same hook.
  const {
    indexes, activeId, select: selectStrat, create: createStrat, fork: forkStrat,
    forkDefault: forkDefaultInto, rename, requestDelete, pendingDelete,
    confirmDelete, cancelDelete, stopStrat: stopStratSession, broadcast,
  } = useStratManager();
  const [open, setOpen] = useState(false);
  const [docked, setDocked] = useState(false);
  // Set when the header's wallet chip asks for the accounts section — the
  // column is where accounts live now, so the chip opens it rather than
  // dropping its own list on top of the page.
  const [accountsWanted, setAccountsWanted] = useState(false);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  // Which strat's chat is open. Held by ID, not by object: the list reloads
  // every couple of seconds, and an open panel must follow the strat's edits
  // rather than pin a stale copy of it.
  const [chatId, setChatId] = useState<string | null>(null);
  // The multi-strat DEPOSIT screen. It lives here rather than in a page
  // because the money question is the column's question — and because from
  // here it reaches every strat, not just the one a route happens to be on.
  const [depositOpen, setDepositOpen] = useState(false);
  // Per-strat live PnL from the backend engine's tagged fills + the deposit
  // wallet's USDC cash — rendered as a second line on each sidebar row.
  const { stats: stratStats, cash, running: liveStratIds } = useStratStats();
  // Resolved fresh each render, so an applied patch shows up in the panel's
  // own "current settings" on the next poll.
  const chatStrat = chatId === null ? null : indexes.find((i) => i.id === chatId) ?? null;
  const router = useRouter();
  const pathname = usePathname();

  // Pick dock vs overlay, and restore the sidebar the user left open. Docked
  // defaults to OPEN — the column is the strat list, and a console that hides
  // which strat you're on until you click something is worse than one that
  // just shows you. Both before paint — see useIsoLayoutEffect.
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

  /** Open/close the sidebar, remembering the choice so it survives navigation
      and reloads. Written here rather than in an effect: an effect would fire
      on mount with the pre-restore `open` and clobber the saved state. */
  const setDrawer = useCallback((next: boolean) => {
    setOpen(next);
    try {
      localStorage.setItem(DOCK_KEY, next ? "1" : "0");
    } catch {}
  }, []);

  // The wallet chip in the header asks for the accounts section by name: open
  // the column (docked or overlay) and let AccountsPanel mount expanded.
  useEffect(() => {
    const onOpen = () => { setAccountsWanted(true); setDrawer(true); };
    window.addEventListener(OPEN_ACCOUNTS_EVENT, onOpen);
    return () => window.removeEventListener(OPEN_ACCOUNTS_EVENT, onOpen);
  }, [setDrawer]);

  // Inset the console for the docked column (CSS var, consumed by
  // .crt-screen in layout.tsx).
  useEffect(() => {
    const el = document.documentElement;
    if (open && docked) el.dataset.stratDock = "open";
    else delete el.dataset.stratDock;
    return () => {
      delete el.dataset.stratDock;
    };
  }, [open, docked]);

  // Escape closes the overlay drawer, and locks page scroll while it's up. A
  // DOCKED sidebar does neither: it's furniture, so the page behind it stays
  // scrollable and Escape belongs to whatever modal the page itself has open.
  useEffect(() => {
    if (!open || docked) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setDrawer(false);
    };
    document.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [open, docked, setDrawer]);

  /** Switch the active strat. On /strats that also means opening its
      workspace — the hub and the workspace are the same route, keyed by ?id. */
  const select = (id: string) => {
    selectStrat(id);
    // A docked sidebar stays put — switching strats is something you do a few
    // times in a row, and the column isn't covering anything.
    if (!docked) setDrawer(false);
    if (pathname?.startsWith("/strats")) router.push(`/strats?id=${id}`);
  };

  // Everything the wallet has at work, across every strat's open positions.
  const totalInPlay = Object.values(stratStats).reduce((s, m) => s + m.openValue, 0);

  const stopStrat = (id: string) => stopStratSession(id);

  /** Fork a saved strat, then drop straight into rename mode — the first
      thing you do with a fork is say what makes it different. */
  const fork = (id: string) => {
    const copy = forkStrat(id);
    if (!copy) return;
    setRenamingId(copy.id);
    setRenameValue(copy.name);
  };

  const commitRename = () => {
    if (!renamingId) return;
    rename(renamingId, renameValue);
    setRenamingId(null);
    setRenameValue("");
  };

  const create = () => {
    const idx = createStrat();
    if (!docked) setDrawer(false);
    if (pathname?.startsWith("/strats")) router.push(`/strats?id=${idx.id}`);
  };

  const forkDefault = (t: StratTemplate) => {
    const idx = forkDefaultInto(t);
    if (!docked) setDrawer(false);
    if (pathname?.startsWith("/strats")) router.push(`/strats?id=${idx.id}`);
  };

  // Embedded split-screen panes stay lightweight, same as NavMenu.
  if (embedded) return null;

  const active = indexes.find((i) => i.id === activeId) ?? indexes[0] ?? null;
  // The strat's market keywords (the KEYWORDS filter / MARKET param) are part
  // of its identity — two strats on the same traders differ only by them — so
  // the header readout surfaces them next to the name.
  const activeKeywords = active?.marketQuery?.trim() ?? "";
  const keywordGroups = (q: string) => q.split(/[,|]/).map((s) => s.trim()).filter(Boolean);

  // ── The column itself. Identical in docked and overlay mode; only the
  //    framing around it differs, so there is one copy of the markup. ──
  const column = (
    <>
      {/* ── Who you are ──
          The ACCOUNT switcher is the column's top bar: every wallet this
          browser has signed in as, the money each holds, and the controls to
          switch/rename/forget/add. Accounts and strats are the same decision
          made twice — a strat's money, its engine session and its ledger are
          all keyed by (wallet, strat) — so "which wallet am I" is the first
          line of the same column that answers "which strat am I on". It also
          carries the column's close button, so the user block reads as the
          sidebar's header rather than as a panel bolted onto one. */}
      <AccountsPanel initialExpanded={accountsWanted} onClose={() => setDrawer(false)} />

      <div
        className="flex items-center gap-2 px-3 py-1.5 shrink-0"
        style={{ borderBottom: "1px solid var(--border)" }}
      >
        <span className="text-[11px] font-mono font-bold tracking-[0.18em] text-pixel-white">STRATS</span>
        <span
          className="flex-1 text-[10.5px] font-mono text-pixel-gray truncate text-right"
          title="Wallet = your deposit wallet's on-chain USDC, free to trade. Each row shows what that strat currently has in play."
        >
          {indexes.length} saved · wallet{" "}
          <span className={cash === null ? "" : "text-pixel-white"}>
            {cash === null ? "…" : fmtUsd(cash)}
          </span>
        </span>
        {/* Funding several strats is one decision — split this wallet across
            them — so it gets one button, next to the wallet it spends. */}
        <button
          onClick={() => setDepositOpen(true)}
          title="Deposit into several strats at once — allocate this wallet across them and arm each one"
          className="shrink-0 px-2 py-0.5 rounded-[var(--radius-sm)] border border-pixel-border text-[9.5px] font-mono font-semibold tracking-[0.1em] text-pixel-gray hover:text-green-400 hover:border-green-400/60 transition-colors"
        >
          $ DEPOSIT
        </button>
      </div>
      <div className="flex-1 overflow-y-auto p-1.5 flex flex-col gap-0.5">
        {indexes.length === 0 && (
          <div className="px-3 py-2 text-[11px] text-pixel-gray">No strats yet</div>
        )}
        {indexes.map((idx) => {
          const isActive = idx.id === activeId;
          // Only money the strat actually has: its open positions marked to
          // current prices. A strat that has never traded shows nothing —
          // its `capital` field is an intention, not funds, and printing it
          // here made every idle strat look like it held a bankroll.
          const money = stratStats[idx.id];
          const pnl24h = money?.pnl24h ?? 0;
          const roi24h = money?.roi24h ?? null;
          const inPlay = money?.openValue ?? 0;
          const openPositions = money?.openPositions ?? 0;
          const traded = openPositions > 0 || (money?.fills ?? 0) > 0;
          // The engine is the truth about what's running; `liveEnabled` is
          // only the strat's own intent flag and goes stale when a session
          // is stopped elsewhere.
          const isRunning = liveStratIds.has(idx.id);
          return (
            <div
              key={idx.id}
              onClick={() => select(idx.id)}
              className={`relative flex items-center gap-2 rounded-[var(--radius-sm)] px-3 py-2 text-left cursor-pointer transition-colors ${
                isActive
                  ? "text-green-400 bg-green-400/10"
                  : "text-pixel-gray hover:text-pixel-white hover:bg-pixel-white/[0.06]"
              }`}
            >
              <span
                className={`absolute left-0 top-1/2 -translate-y-1/2 w-[3px] rounded-full bg-green-400 transition-all duration-200 ${
                  isActive ? "h-5 opacity-100 shadow-[0_0_10px_rgba(74,222,128,0.7)]" : "h-0 opacity-0"
                }`}
              />
              {isRunning && (
                <span
                  className="w-2 h-2 rounded-full bg-green-400 animate-pulse shrink-0"
                  title="Engine running for this strat"
                />
              )}
              {renamingId === idx.id ? (
                <input
                  value={renameValue}
                  onChange={(e) => setRenameValue(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter") commitRename(); if (e.key === "Escape") setRenamingId(null); }}
                  onBlur={commitRename}
                  onClick={(e) => e.stopPropagation()}
                  autoFocus
                  className="flex-1 min-w-0 bg-transparent border-b border-green-400 text-green-400 font-mono text-[12px] outline-none"
                />
              ) : (
                <span
                  onDoubleClick={(e) => { e.stopPropagation(); setRenamingId(idx.id); setRenameValue(idx.name); }}
                  className="flex-1 min-w-0"
                  title="Double-click to rename"
                >
                  <span className="block truncate text-[12px] font-mono font-semibold">
                    {idx.name}
                    {idx.identity && (
                      <span className="ml-1.5 text-[9px] tracking-[0.1em] text-cyan-400" title={`IDENTITY strat — copies exactly one trader: ${idx.identity}`}>
                        ID
                      </span>
                    )}
                    {idx.visibility === "public" && (
                      <span className="ml-1.5 text-[9px] tracking-[0.1em] text-green-400/90" title="Published to the PUBLIC gallery — anyone can view and fork it from the STRAT HUB">
                        PUB
                      </span>
                    )}
                  </span>
                  {idx.marketQuery?.trim() && (
                    <span
                      className="block truncate text-[10px] font-mono text-amber-300/70"
                      title={`Copying only markets matching: ${idx.marketQuery.trim()}`}
                    >
                      ⌕ {idx.marketQuery.trim()}
                    </span>
                  )}
                  {idx.filter && (
                    <span
                      className="block truncate text-[10px] font-mono text-cyan-300/70"
                      title={`Trader filter — of ${idx.traders.length} watched traders this strat copies only the ${describeTraderFilter(idx.filter)}, re-ranked every scan.`}
                    >
                      ▼ {describeTraderFilter(idx.filter)}
                    </span>
                  )}
                  <span
                    className="block truncate text-[10px] font-mono text-pixel-gray"
                    title={
                      money
                        ? `${fmtUsd(money.moneyIn)} cost basis across ${money.openPositions} open position(s) · total ${money.totalPnl >= 0 ? "+" : ""}${fmtUsd(money.totalPnl)} (realized ${fmtUsd(money.realized)} · unrealized ${fmtUsd(money.unrealized)})`
                        : "No fills from your wallet in this strat yet"
                    }
                  >
                    {traded ? (
                      <>
                        {openPositions > 0 ? `${openPositions} pos` : "flat"}
                        {" "}· 24h{" "}
                        <span className={pnl24h > 0 ? "text-green-400" : pnl24h < 0 ? "text-red-400" : ""}>
                          {pnl24h >= 0 ? "+" : ""}{fmtUsd(pnl24h)}
                          {roi24h !== null && ` (${roi24h >= 0 ? "+" : ""}${roi24h.toFixed(1)}%)`}
                        </span>
                      </>
                    ) : isRunning ? (
                      "running · no positions yet"
                    ) : (
                      "not trading"
                    )}
                  </span>
                </span>
              )}
              {/* The money this strat actually holds — open positions marked
                  to current prices. Blank when it has never traded. */}
              <span
                className="shrink-0 font-mono text-[11px] tabular-nums text-pixel-gray-light"
                title={
                  openPositions > 0
                    ? `${idx.name} has ${fmtUsd(inPlay)} in play across ${openPositions} open position(s).`
                    : `${idx.name} holds no positions.`
                }
              >
                {openPositions > 0 ? fmtUsd(inPlay) : <span className="opacity-40">—</span>}
              </span>
              <span className="text-[10px] text-pixel-gray shrink-0">{idx.traders.length}T</span>
              {/* Every strat has a chat. It reads this strat's settings and
                  proposes parameter changes you apply by hand — the fastest
                  path from "it's buying too many longshots" to the setting
                  that stops it. */}
              <button
                onClick={(e) => { e.stopPropagation(); setChatId(idx.id); }}
                className="text-[9.5px] font-mono font-semibold tracking-[0.08em] text-pixel-gray hover:text-green-400 shrink-0"
                title={`Chat about "${idx.name}" — ask for a change in words and apply the patch it proposes`}
              >
                ASK
              </button>
              <button
                onClick={(e) => { e.stopPropagation(); fork(idx.id); }}
                className="text-[11px] text-pixel-gray hover:text-green-400 shrink-0"
                title={`Fork "${idx.name}" — an independent copy of the whole strategy, stopped and un-funded, ready to rename`}
              >
                ⑂
              </button>
              {isRunning ? (
                <button
                  onClick={(e) => { e.stopPropagation(); void stopStrat(idx.id); }}
                  className="text-[10px] font-mono text-pixel-gray hover:text-red-400 shrink-0"
                  title="Stop this strat's engine — the wallet's other funded strats keep running"
                >
                  ■
                </button>
              ) : (
                <button
                  onClick={(e) => { e.stopPropagation(); setRenamingId(idx.id); setRenameValue(idx.name); }}
                  className="text-[11px] text-pixel-gray hover:text-green-400 shrink-0"
                  title="Rename"
                >
                  ✎
                </button>
              )}
              <button
                onClick={(e) => { e.stopPropagation(); requestDelete(idx.id); }}
                className="text-[13px] text-pixel-gray hover:text-red-400 shrink-0"
                title="Delete"
              >
                ×
              </button>
            </div>
          );
        })}
        <button
          onClick={create}
          className="mt-0.5 rounded-[var(--radius-sm)] border border-dashed border-pixel-border px-3 py-2 text-left text-[11px] font-mono font-semibold tracking-[0.08em] text-pixel-gray hover:text-green-400 hover:border-green-400/60 transition-colors"
        >
          + NEW STRAT
        </button>
        <button
          onClick={() => { if (!docked) setDrawer(false); router.push("/strats"); }}
          className="rounded-[var(--radius-sm)] px-3 py-2 text-left text-[11px] font-mono font-semibold tracking-[0.08em] text-pixel-gray hover:text-green-400 hover:bg-pixel-white/[0.06] transition-colors"
        >
          ▦ STRAT HUB
        </button>

        {/* Curated starting points — forking one materializes a fresh
            user-owned strat and seeds it from the live leaderboard. */}
        <div className="mt-1 pt-1.5 border-t border-pixel-border/60">
          <div className="px-3 pb-1 text-[9.5px] font-mono font-semibold tracking-[0.14em] text-pixel-gray/80">
            DEFAULT STRATS — FORK TO CUSTOMIZE
          </div>
          {DEFAULT_STRATS.map((t) => (
            <button
              key={t.slug}
              onClick={() => forkDefault(t)}
              title={`Fork "${t.name}" into your strats`}
              className="group w-full flex items-start gap-2 rounded-[var(--radius-sm)] px-3 py-1.5 text-left text-pixel-gray hover:text-pixel-white hover:bg-pixel-white/[0.06] transition-colors"
            >
              <span className="flex-1 min-w-0">
                <span className="block truncate text-[11.5px] font-mono font-semibold group-hover:text-green-400">
                  {t.name}
                </span>
                <span className="block text-[10px] leading-snug text-pixel-gray/80">
                  {t.description}
                </span>
              </span>
              <span className="text-[10px] font-mono shrink-0 mt-0.5 opacity-60 group-hover:opacity-100 group-hover:text-green-400">
                ⑂ FORK
              </span>
            </button>
          ))}
        </div>
      </div>

      {/* ── In play ──
          What the wallet has at work right now, summed across every strat's
          open positions. Real money only — no allocations, no intentions.
          Sits outside the scroll area so it stays visible while the list
          scrolls. */}
      <div
        className="shrink-0 px-3 py-2 space-y-1.5"
        style={{ borderTop: "1px solid var(--border)" }}
      >
        <div className="flex items-center justify-between text-[10px] font-mono">
          <span className="text-pixel-gray tracking-[0.14em]">IN PLAY</span>
          <span className="text-pixel-white tabular-nums" title="Open positions across all strats, marked to current prices">
            {fmtUsd(totalInPlay)}
            {cash !== null && (
              <span className="text-pixel-gray"> · {fmtUsd(cash)} free</span>
            )}
          </span>
        </div>
        <div className="text-[9.5px] font-mono leading-snug text-pixel-gray">
          {liveStratIds.size === 0
            ? "No strat is running. Start one from the TRADE tab."
            : `${liveStratIds.size} strat${liveStratIds.size > 1 ? "s" : ""} running.`}
        </div>
      </div>
    </>
  );

  // Docked: a plain column under the header, below its z-index. No backdrop,
  // no click-out — the page beside it stays fully live.
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

  // Overlay: no room for a column, so it's a modal drawer over the console.
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
    <div className="relative flex items-center gap-1">
      <button
        onClick={() => setDrawer(!open)}
        title={
          active
            ? `Strat: ${active.name}${activeKeywords ? ` — keywords: ${activeKeywords}` : ""} — click to ${open ? "hide" : "show"} your account + strats sidebar`
            : "Select a strat"
        }
        aria-expanded={open}
        className={`flex items-center gap-1.5 px-2 py-1.5 rounded-[var(--radius-sm)] transition-colors max-w-[min(200px,32vw)] ${
          open ? "bg-pixel-white/[0.06] text-green-400" : "text-pixel-gray hover:bg-pixel-white/[0.06]"
        }`}
      >
        {/* Sidebar glyph: the RIGHT rail fills in when the column is showing —
            it points at the edge the column actually comes from, and it's the
            only affordance the header needs now that the list lives in the
            sidebar rather than in a dropdown. */}
        <svg className="w-[13px] h-[13px] shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <rect x="3" y="4" width="18" height="16" rx="2" />
          {open ? <rect x="15" y="4" width="6" height="16" rx="2" fill="currentColor" /> : <path d="M15 4v16" />}
        </svg>
        {/* Engine truth, not the strat's `liveEnabled` intent flag — and the
            count when the wallet is running more than this one strat. */}
        {active && liveStratIds.has(active.id) && (
          <span className="w-2 h-2 rounded-full bg-green-400 animate-pulse shrink-0" title="Engine running for this strat" />
        )}
        <span className="truncate min-w-0 text-[12.5px] font-mono font-semibold text-green-400">
          {active ? active.name : "NO STRAT"}
        </span>
        {liveStratIds.size > 1 && (
          <span
            className="text-[10px] font-mono text-green-400/80 shrink-0"
            title={`${liveStratIds.size} strats funded and running`}
          >
            ●{liveStratIds.size}
          </span>
        )}
        {active && (
          <span className="text-[10px] text-pixel-gray shrink-0">{active.traders.length}T</span>
        )}
        {activeKeywords && (
          <span
            className="text-[10px] font-mono text-amber-300/90 shrink-0"
            title={`Copying only markets matching: ${activeKeywords}`}
          >
            ⌕{keywordGroups(activeKeywords).length}
          </span>
        )}
      </button>

      {/* Portal to <body>: the TopBar's backdrop-blur makes the header the
          containing block for fixed descendants — rendered in place, the
          full-height sidebar would be clipped to the 48px header strip. */}
      {open && createPortal(docked ? dockedSidebar : overlaySidebar, document.body)}

      <ConfirmDeleteStrat
        name={pendingDelete === null ? null : indexes.find((i) => i.id === pendingDelete)?.name ?? pendingDelete}
        onConfirm={confirmDelete}
        onCancel={cancelDelete}
      />

      {/* Allocate the wallet across several strats and arm them in one pass.
          Portals itself, so it floats above the column that opened it. */}
      {depositOpen && (
        <DepositPanel
          indexes={indexes}
          stats={stratStats}
          cash={cash}
          running={liveStratIds}
          eoa={auth.address}
          onClose={() => setDepositOpen(false)}
          onDone={broadcast}
        />
      )}

      {/* The per-strat chat. Rendered from the sidebar (not from a page) so
          the strat you're asking about is the row you clicked, from anywhere
          in the console — and portaled for the same reason the column is. */}
      {chatStrat && createPortal(
        <StratChat
          strat={chatStrat}
          eoa={auth.address}
          onClose={() => setChatId(null)}
          onApplied={() => broadcast()}
        />,
        document.body,
      )}
    </div>
  );
}
