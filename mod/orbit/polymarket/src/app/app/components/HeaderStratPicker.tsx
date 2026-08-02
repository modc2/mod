"use client";

// One-line strat selector for the top header: the active strat's name opens
// the strat manager in the RIGHT sidebar — every saved strat (click to
// switch, ✎ or double-click to rename, × to delete), with the sidebar header
// showing the deposit wallet's on-chain USDC and each row showing that
// strat's OWN cash (its capital allocation minus deployed basis) plus 24h
// PnL from the signed-in wallet's engine ledger.
//
// On a wide viewport (≥1024px) the sidebar DOCKS: no backdrop, no scroll
// lock, the page insets by its width and it stays open across navigation and
// reloads, so you can edit a strat on the left while its allocation, cash and
// FUND bar sit beside it. Below that width there isn't room for a column, so
// it falls back to a modal overlay drawer.
//
// The sidebar is also the FUNDING desk. Every row carries an editable
// allocation, and edits accumulate as drafts: type amounts across as many
// strats as you like, watch ALLOCATED / UNALLOCATED track against the
// wallet's USDC, then commit them all with one FUND button. Committing a
// non-zero allocation to a strat also starts (or re-arms) that strat's own
// backend session — the engine runs one session per (wallet, strat), so
// several strats trade side by side, each budgeting against its own
// allocation (lib/liveSessions.ts). ■ STOP takes a single strat down without
// touching the others.
//
// The [+] beside the name creates a new strat, and a DEFAULT STRATS gallery
// at the bottom forks curated templates (lib/defaultStrats.ts) into
// user-owned strats. This is THE strat manager — indexStore localStorage
// store, `strat-updated` window event, best-effort server sync — so
// CopyIndex, the LIVE checklist and this picker can never disagree about
// which strat is active.

import { useCallback, useEffect, useLayoutEffect, useState } from "react";
import { createPortal } from "react-dom";
import { usePathname, useRouter } from "next/navigation";
import {
  loadIndexes,
  saveIndex,
  updateIndex,
  deleteIndex,
  getActiveIndexId,
  setActiveIndexId,
  equalWeightTraders,
} from "../lib/indexStore";
import { pushStrat, deleteServerStrat, syncStrats } from "../lib/stratSync";
import { useAuth } from "../context/AuthContext";
import { useFilters } from "../context/FiltersContext";
import { fetchTopTraderAddresses } from "../lib/polymarket";
import { useEmbedded } from "../lib/embedded";
import { DEFAULT_STRATS, forkDefaultStrat, type StratTemplate } from "../lib/defaultStrats";
import { useStratStats, fmtUsd } from "../lib/stratStats";
import { startLiveSession, stopLiveSession } from "../lib/liveSessions";
import StratCardsBrowser from "./StratCardsBrowser";
import type { SavedIndex } from "../lib/types";

// Wide enough for a real column beside the console; below this the sidebar
// falls back to a modal overlay. Matches the media query in globals.css that
// gives --strat-dock its width.
const DOCK_MQ = "(min-width: 1024px)";
const DOCK_KEY = "poly_strat_dock";

// TopBar (and this picker with it) remounts on every page navigation, so a
// docked sidebar has to restore itself — and restore BEFORE paint, or the
// console visibly un-insets and re-insets on each route change. Layout
// effects don't run on the server; useEffect there keeps React quiet.
const useIsoLayoutEffect = typeof window === "undefined" ? useEffect : useLayoutEffect;

export default function HeaderStratPicker() {
  const embedded = useEmbedded();
  const { localToken, auth } = useAuth();
  const { category, marketQuery, daysAgo, minPerDay } = useFilters();
  const [indexes, setIndexes] = useState<SavedIndex[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [docked, setDocked] = useState(false);
  const [browserOpen, setBrowserOpen] = useState(false);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  // Uncommitted allocation edits, stratId → raw input text. Several rows can
  // hold a draft at once — that's the point: fund a whole book in one pass
  // instead of one strat per round-trip. Committed (and cleared) by fundAll.
  const [allocDrafts, setAllocDrafts] = useState<Record<string, string>>({});
  const [funding, setFunding] = useState(false);
  // In-app delete confirmation (replaces the native window.confirm popup so
  // the "Delete strat?" prompt renders inside the console, styled like the
  // rest of the UI, instead of an ugly modc2.com-says browser dialog).
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);
  const [initialSynced, setInitialSynced] = useState(false);
  // Per-strat live PnL from the backend engine's tagged fills + the deposit
  // wallet's USDC cash — rendered as a second line on each dropdown row and
  // on the cards.
  const { stats: stratStats, cash, running: liveStratIds } = useStratStats();
  const router = useRouter();
  const pathname = usePathname();

  const reload = useCallback(() => {
    setIndexes(loadIndexes());
    setActiveId(getActiveIndexId());
  }, []);

  // Poll localStorage so edits made anywhere (leaderboard toggles,
  // backtest writes) show up here.
  useEffect(() => {
    reload();
    const t = setInterval(reload, 2000);
    window.addEventListener("strat-updated", reload);
    return () => {
      clearInterval(t);
      window.removeEventListener("strat-updated", reload);
    };
  }, [reload]);

  // Initial local↔server strat merge (lived in the old strats-page sidebar;
  // the picker renders on every page, so a fresh browser pulls server strats
  // down no matter where the user lands).
  useEffect(() => {
    if (!localToken || initialSynced) return;
    setInitialSynced(true);
    const local = loadIndexes();
    syncStrats(local, localToken.token).then((merged) => {
      for (const s of merged) {
        if (!local.find((l) => l.id === s.id)) saveIndex(s);
      }
      reload();
    });
  }, [localToken, initialSynced, reload]);

  // Pick dock vs overlay, and re-open a docked sidebar the user left open.
  // Both before paint — see useIsoLayoutEffect.
  useIsoLayoutEffect(() => {
    const mq = window.matchMedia(DOCK_MQ);
    setDocked(mq.matches);
    try {
      if (mq.matches && localStorage.getItem(DOCK_KEY) === "1") setOpen(true);
    } catch {}
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

  // Inset the console for the docked column (CSS var, consumed by
  // .crt-screen in layout.tsx and by the BuildBadge).
  useEffect(() => {
    const el = document.documentElement;
    if (open && docked) el.dataset.stratDock = "open";
    else delete el.dataset.stratDock;
    return () => {
      delete el.dataset.stratDock;
    };
  }, [open, docked]);

  // Close on Escape; lock page scroll only in overlay mode — a docked sidebar
  // is furniture, not a modal, so the page behind it stays scrollable. Outside
  // clicks are handled by the overlay's own backdrop: the panel is
  // portal-rendered to <body>, so a document-level containment check against
  // this component's root would treat clicks inside the panel as "outside".
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setDrawer(false);
    };
    document.addEventListener("keydown", onKey);
    if (docked) return () => document.removeEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [open, docked, setDrawer]);

  const broadcast = useCallback(() => {
    reload();
    window.dispatchEvent(new Event("strat-updated"));
  }, [reload]);

  const select = (id: string) => {
    setActiveIndexId(id);
    setActiveId(id);
    // A docked sidebar stays put — switching strats is something you do a few
    // times in a row, and the column isn't covering anything.
    if (!docked) setDrawer(false);
    broadcast();
  };

  // Card-browser select: switch strat AND land in the strat editor — the
  // browser is reachable from every page, so picking a card should end on
  // /strats where the choice is actionable.
  const selectFromBrowser = (id: string) => {
    setActiveIndexId(id);
    setActiveId(id);
    setBrowserOpen(false);
    broadcast();
    if (!pathname?.startsWith("/strats")) router.push("/strats");
  };

  // Ask first — opens the in-app confirm modal instead of window.confirm.
  const remove = (id: string) => setPendingDelete(id);

  // Actually delete, once the modal is confirmed.
  const confirmRemove = () => {
    const id = pendingDelete;
    if (!id) return;
    setPendingDelete(null);
    deleteIndex(id);
    const remaining = loadIndexes();
    if (activeId === id) setActiveIndexId(remaining[0]?.id ?? null);
    broadcast();
    if (localToken) deleteServerStrat(id, localToken.token);
  };

  // ── Funding desk ──────────────────────────────────────────────
  // `capital` is the same field the LIVE panel and backtest sim size against,
  // and the budget each backend session spends against, so an allocation set
  // here IS that strat's funding.

  /** Allocation a row currently shows: its uncommitted draft if it has one,
      else the persisted `capital`. */
  const allocOf = (idx: SavedIndex): number => {
    const draft = allocDrafts[idx.id];
    if (draft !== undefined) {
      const n = Number(draft);
      return Number.isFinite(n) && n >= 0 ? n : 0;
    }
    return idx.capital ?? 0;
  };

  const draftIds = Object.keys(allocDrafts);
  // Total across ALL strats, drafts included — funding is a book-level
  // decision, so the bar has to price every strat, not just the edited ones.
  const totalAllocated = indexes.reduce((s, i) => s + allocOf(i), 0);
  const unallocated = cash === null ? null : cash - totalAllocated;
  const overAllocated = unallocated !== null && unallocated < 0;

  /** Commit every draft at once: persist each allocation, then start (or
      re-arm) a backend session for the funded ones so they actually trade.
      Strats drafted down to $0 are stopped. */
  const fundAll = async () => {
    if (draftIds.length === 0 || funding) return;
    setFunding(true);
    try {
      const current = loadIndexes();
      for (const id of draftIds) {
        const idx = current.find((i) => i.id === id);
        if (!idx) continue;
        const amount = Math.max(0, Math.round(allocOf(idx)));
        updateIndex(id, { capital: amount, liveEnabled: amount > 0, updatedAt: Date.now() });
      }
      setAllocDrafts({});
      broadcast();
      const saved = loadIndexes();
      for (const id of draftIds) {
        const idx = saved.find((i) => i.id === id);
        if (!idx) continue;
        if (localToken) pushStrat(idx, localToken.token);
        // Arming needs a wallet to fund from — without one this is just a
        // saved allocation the LIVE tab will pick up later.
        if (!auth.address) continue;
        const amount = idx.capital ?? 0;
        if (amount > 0) await startLiveSession(auth.address, idx, amount);
        else await stopLiveSession(auth.address, id);
      }
      broadcast();
    } finally {
      setFunding(false);
    }
  };

  /** Take one strat's session down without touching the wallet's others. */
  const stopStrat = async (id: string) => {
    updateIndex(id, { liveEnabled: false, updatedAt: Date.now() });
    broadcast();
    if (auth.address) await stopLiveSession(auth.address, id);
  };

  const commitRename = () => {
    if (!renamingId) return;
    updateIndex(renamingId, { name: renameValue.trim() || "Untitled", updatedAt: Date.now() });
    const updated = loadIndexes().find((i) => i.id === renamingId);
    if (updated && localToken) pushStrat(updated, localToken.token);
    setRenamingId(null);
    setRenameValue("");
    broadcast();
  };

  const create = () => {
    const existing = loadIndexes();
    const now = Date.now();
    const idx: SavedIndex = {
      id: now.toString(36),
      name: `Strat ${existing.length + 1}`,
      traders: [],
      backtestDays: 7,
      rebalanceMinutes: 0.5,
      livePollMinutes: 0.5,
      capital: 1000,
      minTrade: 1,
      maxTrade: 100,
      maxPerCycle: 3,
      createdAt: now,
      updatedAt: now,
    };
    saveIndex(idx);
    setActiveIndexId(idx.id);
    if (!docked) setDrawer(false);
    broadcast();
    if (localToken) pushStrat(idx, localToken.token);

    // Seed with the top 10 traders matching the current leaderboard filters
    // (same behavior as the sidebar's + NEW STRAT).
    fetchTopTraderAddresses(
      { days: Number(daysAgo) || undefined, minPerDay: Number(minPerDay) || undefined, category, marketQuery },
      10,
    ).then((addrs) => {
      if (addrs.length === 0) return;
      const traders = equalWeightTraders(addrs);
      updateIndex(idx.id, { traders, updatedAt: Date.now() });
      broadcast();
      if (localToken) pushStrat({ ...idx, traders }, localToken.token);
    });
  };

  // Fork a built-in template into a user-owned strat: instant strat with
  // the recipe's params, traders seeded async from the live leaderboard.
  const forkDefault = (t: StratTemplate) => {
    const idx = forkDefaultStrat(t, (seeded) => {
      broadcast();
      if (localToken) pushStrat(seeded, localToken.token);
    });
    if (!docked) setDrawer(false);
    broadcast();
    if (localToken) pushStrat(idx, localToken.token);
  };

  // Embedded split-screen panes stay lightweight, same as NavMenu.
  if (embedded) return null;

  const active = indexes.find((i) => i.id === activeId) ?? indexes[0] ?? null;
  // The strat's market keywords (the KEYWORDS filter / MARKET param) are part
  // of its identity — two strats on the same traders differ only by them — so
  // the picker surfaces them next to the name instead of hiding them in tabs.
  const activeKeywords = active?.marketQuery?.trim() ?? "";
  const keywordGroups = (q: string) => q.split(/[,|]/).map((s) => s.trim()).filter(Boolean);

  return (
    <div className="relative flex items-center gap-1">
      <button
        onClick={() => setDrawer(!open)}
        title={
          active
            ? `Strat: ${active.name}${activeKeywords ? ` — keywords: ${activeKeywords}` : ""} — click to ${open ? "close" : "open"} the strat sidebar`
            : "Select a strat"
        }
        aria-expanded={open}
        className={`flex items-center gap-1.5 px-2 py-1.5 rounded-[var(--radius-sm)] transition-colors max-w-[min(200px,32vw)] ${
          open ? "bg-pixel-white/[0.06]" : "hover:bg-pixel-white/[0.06]"
        }`}
      >
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
        <span className={`text-[10px] text-pixel-gray transition-transform duration-150 ${open ? "rotate-180" : ""}`}>
          ▾
        </span>
      </button>
      <button
        onClick={create}
        title="Create new strat"
        className="grid place-items-center w-[22px] h-[22px] rounded-[var(--radius-sm)] border border-pixel-border text-pixel-gray hover:text-green-400 hover:border-green-400/60 transition-colors text-[14px] leading-none shrink-0"
      >
        +
      </button>
      <button
        onClick={() => { if (!docked) setDrawer(false); setBrowserOpen(true); }}
        title="Browse strats as cards"
        className="hidden min-[480px]:grid place-items-center w-[22px] h-[22px] rounded-[var(--radius-sm)] border border-pixel-border text-pixel-gray hover:text-green-400 hover:border-green-400/60 transition-colors text-[11px] leading-none shrink-0"
      >
        ▦
      </button>

      {open &&
        createPortal(
          // Portal to <body>: the TopBar's backdrop-blur makes the header the
          // containing block for fixed descendants — rendered in place, the
          // full-height drawer would be clipped to the 48px header strip.
          <div className="fixed inset-0 z-50" onClick={() => setOpen(false)}>
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
              <div
                className="flex items-center gap-2 px-3 h-12 shrink-0"
                style={{ borderBottom: "1px solid var(--border)" }}
              >
                <span className="text-[12px] font-mono font-bold tracking-[0.18em] text-pixel-white">STRATS</span>
                <span
                  className="flex-1 text-[10.5px] font-mono text-pixel-gray truncate"
                  title="Wallet = your deposit wallet's on-chain USDC. Each row shows that strat's own cash (its allocation minus what it has deployed)."
                >
                  {indexes.length} saved · wallet{" "}
                  <span className={cash === null ? "" : "text-pixel-white"}>
                    {cash === null ? "…" : fmtUsd(cash)}
                  </span>
                </span>
                <button
                  onClick={() => setOpen(false)}
                  title="Close (Esc)"
                  className="grid place-items-center w-[24px] h-[24px] rounded-[var(--radius-sm)] border border-pixel-border text-pixel-gray hover:text-pixel-white hover:border-pixel-white/40 transition-colors text-[13px] leading-none shrink-0"
                >
                  ×
                </button>
              </div>
              <div className="flex-1 overflow-y-auto p-1.5 flex flex-col gap-0.5">
          {indexes.length === 0 && (
            <div className="px-3 py-2 text-[11px] text-pixel-gray">No strats yet</div>
          )}
          {indexes.map((idx) => {
            const isActive = idx.id === activeId;
            // Always show the signed-in user's money in each strat — an
            // untraded strat reads its full allocation rather than hiding the
            // line, so the list doubles as a per-strat portfolio breakdown.
            // Strat cash = the strat's capital allocation minus the cost basis
            // it currently has deployed — the same free-capital number the
            // engine budgets mirrors against (NOT the wallet-wide USDC, which
            // lives in the drawer header).
            const money = stratStats[idx.id];
            const pnl24h = money?.pnl24h ?? 0;
            const roi24h = money?.roi24h ?? null;
            const alloc = allocOf(idx);
            const drafted = allocDrafts[idx.id] !== undefined;
            const deployed = money?.moneyIn ?? 0;
            const stratCash = Math.max(0, alloc - deployed);
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
                    <span className="block truncate text-[12px] font-mono font-semibold">{idx.name}</span>
                    {idx.marketQuery?.trim() && (
                      <span
                        className="block truncate text-[10px] font-mono text-amber-300/70"
                        title={`Copying only markets matching: ${idx.marketQuery.trim()}`}
                      >
                        ⌕ {idx.marketQuery.trim()}
                      </span>
                    )}
                    <span
                      className="block truncate text-[10px] font-mono text-pixel-gray"
                      title={
                        money
                          ? `$${money.moneyIn.toFixed(2)} deployed across ${money.openPositions} open position(s) · total ${money.totalPnl >= 0 ? "+" : ""}${fmtUsd(money.totalPnl)} (realized ${fmtUsd(money.realized)} · unrealized ${fmtUsd(money.unrealized)})`
                          : "No fills from your wallet in this strat yet"
                      }
                    >
                      <span
                        title={`This strat's cash: $${alloc} allocation − ${fmtUsd(deployed)} deployed${cash !== null ? ` (wallet holds ${fmtUsd(cash)} USDC)` : ""}.`}
                      >
                        {fmtUsd(stratCash)} cash
                      </span>
                      {" "}· 24h{" "}
                      <span className={pnl24h > 0 ? "text-green-400" : pnl24h < 0 ? "text-red-400" : ""}>
                        {pnl24h >= 0 ? "+" : ""}{fmtUsd(pnl24h)}
                        {roi24h !== null && ` (${roi24h >= 0 ? "+" : ""}${roi24h.toFixed(1)}%)`}
                      </span>
                    </span>
                  </span>
                )}
                {/* Allocation — a draft here doesn't commit until FUND, so
                    several strats can be funded in one pass. */}
                <span
                  onClick={(e) => e.stopPropagation()}
                  className={`flex items-center shrink-0 rounded-[var(--radius-sm)] border px-1 font-mono text-[11px] ${
                    drafted
                      ? "border-amber-400/70 bg-amber-500/10 text-amber-300"
                      : "border-pixel-border text-pixel-gray-light"
                  }`}
                  title={`Capital allocated to ${idx.name}. Edit as many strats as you like, then FUND to commit them all.`}
                >
                  <span className="opacity-60">$</span>
                  <input
                    type="number"
                    min={0}
                    step={10}
                    value={allocDrafts[idx.id] ?? String(idx.capital ?? 0)}
                    onChange={(e) =>
                      setAllocDrafts((prev) => ({ ...prev, [idx.id]: e.target.value }))
                    }
                    onKeyDown={(e) => { if (e.key === "Enter") void fundAll(); }}
                    className="w-[52px] bg-transparent text-right outline-none [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"
                  />
                </span>
                <span className="text-[10px] text-pixel-gray shrink-0">{idx.traders.length}T</span>
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
                  onClick={(e) => { e.stopPropagation(); remove(idx.id); }}
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
            onClick={() => { if (!docked) setDrawer(false); setBrowserOpen(true); }}
            className="rounded-[var(--radius-sm)] px-3 py-2 text-left text-[11px] font-mono font-semibold tracking-[0.08em] text-pixel-gray hover:text-green-400 hover:bg-pixel-white/[0.06] transition-colors"
          >
            ▦ BROWSE CARDS
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

              {/* ── Funding bar ──
                  Prices the WHOLE book against the wallet, then commits every
                  drafted allocation in one action: each funded strat gets (or
                  keeps) its own backend session, each drafted to $0 is
                  stopped. Sits outside the scroll area so the numbers stay
                  visible while you edit rows above. */}
              <div
                className="shrink-0 px-3 py-2 space-y-1.5"
                style={{ borderTop: "1px solid var(--border)" }}
              >
                <div className="flex items-center justify-between text-[10px] font-mono">
                  <span className="text-pixel-gray tracking-[0.14em]">ALLOCATED</span>
                  <span className={overAllocated ? "text-red-400" : "text-pixel-white"}>
                    {fmtUsd(totalAllocated)}
                    {cash !== null && <span className="text-pixel-gray"> / {fmtUsd(cash)}</span>}
                  </span>
                </div>
                <div className="flex items-center justify-between text-[10px] font-mono">
                  <span className="text-pixel-gray tracking-[0.14em]">UNALLOCATED</span>
                  <span className={overAllocated ? "text-red-400" : "text-pixel-gray-light"}>
                    {unallocated === null ? "…" : fmtUsd(unallocated)}
                  </span>
                </div>
                {overAllocated && (
                  <div className="text-[9.5px] font-mono leading-snug text-red-400/90">
                    Allocations exceed the wallet — the strats that run out of
                    USDC first will fail their orders on-chain.
                  </div>
                )}
                <button
                  onClick={() => void fundAll()}
                  disabled={draftIds.length === 0 || funding}
                  title={
                    draftIds.length === 0
                      ? "Edit one or more $ allocations above, then fund them together"
                      : `Commit ${draftIds.length} allocation(s) and arm each funded strat's engine`
                  }
                  className={`w-full rounded-[var(--radius-sm)] border px-3 py-1.5 text-[11px] font-mono font-bold tracking-[0.1em] transition-colors ${
                    draftIds.length === 0 || funding
                      ? "border-pixel-border text-pixel-gray/50 cursor-not-allowed"
                      : "border-green-400/60 bg-green-400/10 text-green-400 hover:bg-green-400/20"
                  }`}
                >
                  {funding
                    ? "FUNDING…"
                    : draftIds.length === 0
                      ? "FUND STRATS"
                      : `FUND ${draftIds.length} STRAT${draftIds.length > 1 ? "S" : ""}`}
                </button>
                {!auth.address && draftIds.length > 0 && (
                  <div className="text-[9.5px] font-mono leading-snug text-pixel-gray">
                    Not signed in — allocations save, but no engine is armed.
                  </div>
                )}
              </div>
            </aside>
          </div>,
          document.body,
        )}

      <StratCardsBrowser
        open={browserOpen}
        indexes={indexes}
        stats={stratStats}
        cash={cash}
        activeId={activeId}
        onClose={() => setBrowserOpen(false)}
        onSelect={selectFromBrowser}
        onDelete={remove}
        onCreate={() => {
          create();
          setBrowserOpen(false);
          if (!pathname?.startsWith("/strats")) router.push("/strats");
        }}
        onFork={(t) => {
          forkDefault(t);
          setBrowserOpen(false);
          if (!pathname?.startsWith("/strats")) router.push("/strats");
        }}
      />

      {/* In-app delete confirmation — portal to <body> so it floats above the
          drawer and the card browser, both of which route delete through
          remove(). Replaces the native window.confirm() browser popup. */}
      {pendingDelete !== null &&
        createPortal(
          <div
            className="fixed inset-0 z-[70] grid place-items-center p-4"
            onClick={() => setPendingDelete(null)}
          >
            <div className="absolute inset-0" style={{ background: "rgb(var(--pixel-black-rgb)/0.6)" }} />
            <div
              role="alertdialog"
              aria-modal="true"
              onClick={(e) => e.stopPropagation()}
              onKeyDown={(e) => {
                if (e.key === "Escape") setPendingDelete(null);
                if (e.key === "Enter") confirmRemove();
              }}
              tabIndex={-1}
              ref={(el) => el?.focus()}
              className="relative w-full max-w-[360px] rounded-[var(--radius)] backdrop-blur-md p-4 outline-none"
              style={{
                background:
                  "linear-gradient(180deg, rgb(var(--pixel-black-rgb)/0.98), rgb(var(--pixel-bg-rgb)/0.96))",
                border: "1px solid var(--border)",
                boxShadow: "0 24px 64px rgba(0,0,0,0.6)",
                animation: "drawer-in-left 0.14s ease-out",
              }}
            >
              <div className="text-[11px] font-mono font-bold tracking-[0.16em] text-red-400/90">
                DELETE STRAT
              </div>
              <div className="mt-2 text-[12.5px] font-mono text-pixel-white leading-relaxed">
                Delete strat{" "}
                <span className="text-green-400 font-semibold">
                  “{indexes.find((i) => i.id === pendingDelete)?.name ?? pendingDelete}”
                </span>
                ?
              </div>
              <div className="mt-1 text-[10.5px] font-mono text-pixel-gray">
                This removes the strat and its file from disk. This can’t be undone.
              </div>
              <div className="mt-4 flex justify-end gap-2">
                <button
                  onClick={() => setPendingDelete(null)}
                  className="rounded-[var(--radius-sm)] border border-pixel-border px-3 py-1.5 text-[11px] font-mono font-semibold tracking-[0.06em] text-pixel-gray hover:text-pixel-white hover:border-pixel-white/40 transition-colors"
                >
                  CANCEL
                </button>
                <button
                  onClick={confirmRemove}
                  autoFocus
                  className="rounded-[var(--radius-sm)] border border-red-400/50 bg-red-400/10 px-3 py-1.5 text-[11px] font-mono font-semibold tracking-[0.06em] text-red-400 hover:bg-red-400/20 hover:border-red-400 transition-colors"
                >
                  DELETE
                </button>
              </div>
            </div>
          </div>,
          document.body,
        )}
    </div>
  );
}
