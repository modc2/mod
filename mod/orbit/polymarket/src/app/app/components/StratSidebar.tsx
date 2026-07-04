"use client";

// Left-docked strat list — select / rename / delete / "+ New strat".
//
// Parameter editing lives in the STRAT → PARAMS subtab (CopyIndex) — the
// single place to tune capital, trade band, throttle, top-N, market + trade
// filters. The sidebar deliberately doesn't duplicate them.
//
// Everything writes straight to `indexStore` (localStorage, the canonical
// strat store) + best-effort `stratSync` to the server, then dispatches a
// `strat-updated` window event so CopyIndex re-hydrates its backtest config
// and watchlist.

import { useState, useEffect, useCallback } from "react";
import {
  loadIndexes,
  saveIndex,
  deleteIndex,
  updateIndex,
  getActiveIndexId,
  setActiveIndexId,
  equalWeightTraders,
} from "../lib/indexStore";
import { pushStrat, deleteServerStrat, syncStrats } from "../lib/stratSync";
import { useAuth } from "../context/AuthContext";
import { useFilters } from "../context/FiltersContext";
import { fetchTopTraderAddresses } from "../lib/polymarket";
import type { SavedIndex } from "../lib/types";

interface StratSidebarProps {
  onStratChange?: () => void;
}

type SyncState = "idle" | "syncing" | "synced" | "error";

const COLLAPSE_KEY = "stratSidebarCollapsed";

function fmtPnlShort(v: number | undefined): string {
  if (v === undefined) return "—";
  const sign = v >= 0 ? "+" : "";
  if (Math.abs(v) >= 1000) return `${sign}${(v / 1000).toFixed(1)}k`;
  return `${sign}${v.toFixed(0)}`;
}

export default function StratSidebar({ onStratChange }: StratSidebarProps) {
  const { localToken } = useAuth();
  const { category, marketQuery, daysAgo, minPerDay } = useFilters();
  const [indexes, setIndexes] = useState<SavedIndex[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [syncStates, setSyncStates] = useState<Record<string, SyncState>>({});
  const [initialSynced, setInitialSynced] = useState(false);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [collapsed, setCollapsed] = useState(false);

  useEffect(() => {
    try {
      setCollapsed(localStorage.getItem(COLLAPSE_KEY) === "1");
    } catch {}
  }, []);

  const reload = useCallback(() => {
    setIndexes(loadIndexes());
    setActiveId(getActiveIndexId());
  }, []);

  // Poll localStorage so external edits (trader added from a leaderboard,
  // backtest snapshot written) show up here too.
  useEffect(() => {
    reload();
    const t = setInterval(reload, 2000);
    const onUpdate = () => reload();
    window.addEventListener("strat-updated", onUpdate);
    return () => {
      clearInterval(t);
      window.removeEventListener("strat-updated", onUpdate);
    };
  }, [reload]);

  // Initial server merge (same flow StratPicker used).
  useEffect(() => {
    if (!localToken || initialSynced) return;
    setInitialSynced(true);
    const local = loadIndexes();
    syncStrats(local, localToken.token).then((merged) => {
      for (const s of merged) {
        if (!local.find((l) => l.id === s.id)) saveIndex(s);
      }
      reload();
      const allSynced: Record<string, SyncState> = {};
      for (const s of merged) allSynced[s.id] = "synced";
      setSyncStates(allSynced);
    });
  }, [localToken, initialSynced, reload]);

  const broadcast = useCallback(() => {
    reload();
    onStratChange?.();
    window.dispatchEvent(new Event("strat-updated"));
  }, [reload, onStratChange]);

  const pushToServer = useCallback(
    async (idx: SavedIndex) => {
      if (!localToken) return;
      setSyncStates((p) => ({ ...p, [idx.id]: "syncing" }));
      const ok = await pushStrat(idx, localToken.token);
      setSyncStates((p) => ({ ...p, [idx.id]: ok ? "synced" : "error" }));
    },
    [localToken],
  );

  const select = (id: string) => {
    setActiveIndexId(id);
    setActiveId(id);
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
    broadcast();
    pushToServer(idx);

    // Seed with the top 10 traders matching the current leaderboard filters
    // instead of leaving the new strat at 0 traders.
    fetchTopTraderAddresses(
      { days: Number(daysAgo) || undefined, minPerDay: Number(minPerDay) || undefined, category, marketQuery },
      10,
    ).then((addrs) => {
      if (addrs.length === 0) return;
      const traders = equalWeightTraders(addrs);
      updateIndex(idx.id, { traders, updatedAt: Date.now() });
      broadcast();
      pushToServer({ ...idx, traders });
    });
  };

  const remove = (id: string) => {
    const idx = indexes.find((i) => i.id === id);
    if (idx && !confirm(`Delete strat "${idx.name}"?`)) return;
    deleteIndex(id);
    const remaining = loadIndexes();
    if (activeId === id) setActiveIndexId(remaining[0]?.id ?? null);
    broadcast();
    if (localToken) {
      deleteServerStrat(id, localToken.token);
      setSyncStates((p) => {
        const n = { ...p };
        delete n[id];
        return n;
      });
    }
  };

  const commitRename = () => {
    if (!renamingId) return;
    updateIndex(renamingId, { name: renameValue.trim() || "Untitled", updatedAt: Date.now() });
    const updated = loadIndexes().find((i) => i.id === renamingId);
    if (updated) pushToServer(updated);
    setRenamingId(null);
    setRenameValue("");
    broadcast();
  };

  const syncBadge = (id: string) => {
    if (!localToken) return null;
    switch (syncStates[id]) {
      case "syncing": return <span className="text-[10px] text-amber-400 animate-pulse">SYNC</span>;
      case "synced": return <span className="text-[10px] text-green-400">OK</span>;
      case "error": return <span className="text-[10px] text-red-400">ERR</span>;
      default: return null;
    }
  };

  // ── Collapsed rail ──
  if (collapsed) {
    return (
      <aside className="shrink-0 sticky top-12 self-start p-2 pr-0">
        <div className="pixel-panel u-lift flex flex-col items-center py-2.5 px-1.5 gap-2.5">
          <button
            onClick={() => { setCollapsed(false); try { localStorage.setItem(COLLAPSE_KEY, "0"); } catch {} }}
            title="Expand strats"
            className="text-pixel-gray hover:text-green-400 text-[15px] leading-none"
          >
            »
          </button>
          <button
            onClick={create}
            title="New strat"
            className="text-pixel-gray hover:text-green-400 text-[16px] leading-none border border-pixel-border hover:border-green-400 rounded-[6px] w-6 h-6 grid place-items-center"
          >
            +
          </button>
          <div className="[writing-mode:vertical-rl] text-[9.5px] tracking-[0.24em] text-pixel-gray">
            STRATS
          </div>
        </div>
      </aside>
    );
  }

  return (
    <aside className="shrink-0 w-[252px] sticky top-12 self-start max-h-[calc(100vh-3rem)] overflow-y-auto p-2 pr-0">
      <div className="pixel-panel overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2.5 border-b border-[var(--border)] sticky top-0 bg-[rgb(var(--pixel-black-rgb)/0.85)] backdrop-blur z-10">
        <span className="text-[11px] font-bold tracking-[0.2em] text-pixel-white">STRATS</span>
        <div className="flex items-center gap-1.5">
          <button
            onClick={create}
            className="text-[10.5px] font-semibold tracking-[0.08em] px-2.5 py-0.5 border border-pixel-border hover:border-green-400 hover:text-green-400 text-pixel-gray font-mono rounded-full transition-colors"
            title="Create new strat"
          >
            + NEW
          </button>
          <button
            onClick={() => { setCollapsed(true); try { localStorage.setItem(COLLAPSE_KEY, "1"); } catch {} }}
            className="text-[14px] text-pixel-gray hover:text-pixel-white"
            title="Collapse"
          >
            «
          </button>
        </div>
      </div>

      {/* Strat list */}
      <div className="p-1.5 space-y-1">
        {indexes.length === 0 ? (
          <div className="px-2 py-4 text-center text-[12px] text-pixel-gray">
            No strats — click <span className="text-green-400">+ NEW</span>
          </div>
        ) : (
          [...indexes]
            .sort((a, b) => (a.id === activeId ? -1 : b.id === activeId ? 1 : (b.lastPnlAfterCosts ?? -Infinity) - (a.lastPnlAfterCosts ?? -Infinity)))
            .map((idx) => {
              const isActive = idx.id === activeId;
              const pnlColor = (idx.lastPnlAfterCosts ?? 0) >= 0 ? "text-green-400" : "text-red-400";
              return (
                <div
                  key={idx.id}
                  onClick={() => select(idx.id)}
                  className={`px-2 py-1.5 rounded cursor-pointer border transition-colors ${
                    isActive
                      ? "border-green-400/50 bg-green-400/10"
                      : "border-pixel-border/40 hover:bg-pixel-white/5"
                  }`}
                >
                  <div className="flex items-center gap-1.5">
                    {idx.liveEnabled && <span className="w-2 h-2 rounded-full bg-green-400 animate-pulse shrink-0" title="LIVE" />}
                    {renamingId === idx.id ? (
                      <input
                        value={renameValue}
                        onChange={(e) => setRenameValue(e.target.value)}
                        onKeyDown={(e) => { if (e.key === "Enter") commitRename(); if (e.key === "Escape") { setRenamingId(null); } }}
                        onBlur={commitRename}
                        onClick={(e) => e.stopPropagation()}
                        autoFocus
                        className="flex-1 min-w-0 bg-transparent border-b border-green-400 text-green-400 font-mono text-[12px] outline-none"
                      />
                    ) : (
                      <span
                        onDoubleClick={(e) => { e.stopPropagation(); setRenamingId(idx.id); setRenameValue(idx.name); }}
                        className={`flex-1 min-w-0 truncate font-mono text-[12px] ${isActive ? "text-green-400 font-bold" : "text-pixel-white"}`}
                        title="Double-click to rename"
                      >
                        {idx.name}
                      </span>
                    )}
                    <span className="text-[10px] text-pixel-gray shrink-0">{idx.traders.length}T</span>
                    {syncBadge(idx.id)}
                    <button
                      onClick={(e) => { e.stopPropagation(); remove(idx.id); }}
                      className="text-[13px] text-pixel-gray hover:text-red-400 shrink-0"
                      title="Delete"
                    >
                      ×
                    </button>
                  </div>
                  {idx.lastPnlAfterCosts !== undefined && (
                    <div className="mt-0.5 text-[10px] font-mono text-pixel-gray">
                      P&L <span className={pnlColor}>{fmtPnlShort(idx.lastPnlAfterCosts)}</span>
                    </div>
                  )}
                </div>
              );
            })
        )}
      </div>
      </div>
    </aside>
  );
}
