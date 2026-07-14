"use client";

// One-line strat selector for the top header: the active strat's name opens
// a dropdown of every saved strat (click to switch, ✎ or double-click to
// rename, × to delete), the [+] beside it creates a new strat, and a
// DEFAULT STRATS gallery at the bottom forks curated templates
// (lib/defaultStrats.ts) into user-owned strats. This is THE strat
// manager — indexStore localStorage store, `strat-updated` window event,
// best-effort server sync — so CopyIndex, the LIVE checklist and this picker
// can never disagree about which strat is active.

import { useCallback, useEffect, useRef, useState } from "react";
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
import StratCardsBrowser from "./StratCardsBrowser";
import type { SavedIndex } from "../lib/types";

export default function HeaderStratPicker() {
  const embedded = useEmbedded();
  const { localToken } = useAuth();
  const { category, marketQuery, daysAgo, minPerDay } = useFilters();
  const [indexes, setIndexes] = useState<SavedIndex[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [browserOpen, setBrowserOpen] = useState(false);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [initialSynced, setInitialSynced] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
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

  // Close on outside click / Escape.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const broadcast = useCallback(() => {
    reload();
    window.dispatchEvent(new Event("strat-updated"));
  }, [reload]);

  const select = (id: string) => {
    setActiveIndexId(id);
    setActiveId(id);
    setOpen(false);
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

  const remove = (id: string) => {
    const idx = indexes.find((i) => i.id === id);
    if (idx && !confirm(`Delete strat "${idx.name}"?`)) return;
    deleteIndex(id);
    const remaining = loadIndexes();
    if (activeId === id) setActiveIndexId(remaining[0]?.id ?? null);
    broadcast();
    if (localToken) deleteServerStrat(id, localToken.token);
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
      rebalanceMinutes: 1,
      livePollMinutes: 1,
      capital: 1000,
      minTrade: 1,
      maxTrade: 100,
      maxTradesPerHour: 10,
      maxPerCycle: 3,
      createdAt: now,
      updatedAt: now,
    };
    saveIndex(idx);
    setActiveIndexId(idx.id);
    setOpen(false);
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
    setOpen(false);
    broadcast();
    if (localToken) pushStrat(idx, localToken.token);
  };

  // Embedded split-screen panes stay lightweight, same as NavMenu.
  if (embedded) return null;

  const active = indexes.find((i) => i.id === activeId) ?? indexes[0] ?? null;

  return (
    <div ref={rootRef} className="relative flex items-center gap-1">
      <button
        onClick={() => setOpen((v) => !v)}
        title={active ? `Strat: ${active.name} — click to switch` : "Select a strat"}
        aria-expanded={open}
        className={`flex items-center gap-1.5 px-2 py-1.5 rounded-[var(--radius-sm)] transition-colors max-w-[200px] ${
          open ? "bg-pixel-white/[0.06]" : "hover:bg-pixel-white/[0.06]"
        }`}
      >
        {active?.liveEnabled && (
          <span className="w-2 h-2 rounded-full bg-green-400 animate-pulse shrink-0" title="LIVE" />
        )}
        <span className="truncate min-w-0 text-[12.5px] font-mono font-semibold text-green-400">
          {active ? active.name : "NO STRAT"}
        </span>
        {active && (
          <span className="text-[10px] text-pixel-gray shrink-0">{active.traders.length}T</span>
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
        onClick={() => { setOpen(false); setBrowserOpen(true); }}
        title="Browse strats as cards"
        className="grid place-items-center w-[22px] h-[22px] rounded-[var(--radius-sm)] border border-pixel-border text-pixel-gray hover:text-green-400 hover:border-green-400/60 transition-colors text-[11px] leading-none shrink-0"
      >
        ▦
      </button>

      {open && (
        <div
          className="absolute left-0 top-full mt-1.5 z-50 min-w-[260px] max-w-[320px] rounded-[var(--radius-sm)] backdrop-blur-md p-1.5 flex flex-col gap-0.5 max-h-[70vh] overflow-y-auto"
          style={{
            background:
              "linear-gradient(180deg, rgb(var(--pixel-black-rgb)/0.96), rgb(var(--pixel-bg-rgb)/0.94))",
            border: "1px solid var(--border)",
            boxShadow: "0 12px 32px rgba(0,0,0,0.45)",
          }}
        >
          {indexes.length === 0 && (
            <div className="px-3 py-2 text-[11px] text-pixel-gray">No strats yet</div>
          )}
          {indexes.map((idx) => {
            const isActive = idx.id === activeId;
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
                {idx.liveEnabled && (
                  <span className="w-2 h-2 rounded-full bg-green-400 animate-pulse shrink-0" title="LIVE" />
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
                    className="flex-1 min-w-0 truncate text-[12px] font-mono font-semibold"
                    title="Double-click to rename"
                  >
                    {idx.name}
                  </span>
                )}
                <span className="text-[10px] text-pixel-gray shrink-0">{idx.traders.length}T</span>
                <button
                  onClick={(e) => { e.stopPropagation(); setRenamingId(idx.id); setRenameValue(idx.name); }}
                  className="text-[11px] text-pixel-gray hover:text-green-400 shrink-0"
                  title="Rename"
                >
                  ✎
                </button>
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
            onClick={() => { setOpen(false); setBrowserOpen(true); }}
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
      )}

      <StratCardsBrowser
        open={browserOpen}
        indexes={indexes}
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
    </div>
  );
}
