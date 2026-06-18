"use client";

import { useState, useEffect, useCallback } from "react";
import { loadIndexes, saveIndex, deleteIndex, updateIndex, getActiveIndexId, setActiveIndexId } from "../lib/indexStore";
import { pushStrat, deleteServerStrat, syncStrats } from "../lib/stratSync";
import { useAuth } from "../context/AuthContext";
import type { SavedIndex } from "../lib/types";

interface StratPickerProps {
  onStratChange?: () => void;
}

type SyncState = "idle" | "syncing" | "synced" | "error";
type SortKey = "name" | "pnl" | "roi";

const REBALANCE_OPTIONS = [
  { label: "OFF", value: 0 },
  { label: "1M", value: 1 },
  { label: "2M", value: 2 },
  { label: "5M", value: 5 },
  { label: "10M", value: 10 },
  { label: "15M", value: 15 },
  { label: "1H", value: 60 },
  { label: "4H", value: 240 },
  { label: "1D", value: 1440 },
];

function formatPnlShort(v: number | undefined): string {
  if (v === undefined) return "—";
  const sign = v >= 0 ? "+" : "";
  if (Math.abs(v) >= 1000) return `${sign}${(v / 1000).toFixed(1)}k`;
  return `${sign}${v.toFixed(0)}`;
}

function formatPctShort(v: number | undefined): string {
  if (v === undefined) return "—";
  const sign = v >= 0 ? "+" : "";
  if (Math.abs(v) >= 1000) return `${sign}${(v / 1000).toFixed(1)}k%`;
  return `${sign}${v.toFixed(1)}%`;
}

export default function StratPicker({ onStratChange }: StratPickerProps) {
  const { localToken } = useAuth();
  const [indexes, setIndexes] = useState<SavedIndex[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [syncStates, setSyncStates] = useState<Record<string, SyncState>>({});
  const [initialSynced, setInitialSynced] = useState(false);
  const [sortKey, setSortKey] = useState<SortKey>("pnl");
  const [editingRebalance, setEditingRebalance] = useState<string | null>(null);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [collapsed, setCollapsed] = useState(false);

  // Refresh indexes from localStorage periodically (backtest snapshots update there)
  useEffect(() => {
    const interval = setInterval(() => {
      setIndexes(loadIndexes());
      setActiveId(getActiveIndexId());
    }, 3000);
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    const local = loadIndexes();
    setIndexes(local);
    setActiveId(getActiveIndexId());

    if (localToken && !initialSynced) {
      setInitialSynced(true);
      syncStrats(local, localToken.token).then((merged) => {
        for (const s of merged) {
          const existing = local.find((l) => l.id === s.id);
          if (!existing) saveIndex(s);
        }
        setIndexes(loadIndexes());
        const allSynced: Record<string, SyncState> = {};
        for (const s of merged) allSynced[s.id] = "synced";
        setSyncStates(allSynced);
      });
    }
  }, [localToken, initialSynced]);

  const select = (id: string) => {
    setActiveIndexId(id);
    setActiveId(id);
    onStratChange?.();
  };

  const pushToServer = useCallback(async (idx: SavedIndex) => {
    if (!localToken) return;
    setSyncStates((prev) => ({ ...prev, [idx.id]: "syncing" }));
    const ok = await pushStrat(idx, localToken.token);
    setSyncStates((prev) => ({ ...prev, [idx.id]: ok ? "synced" : "error" }));
  }, [localToken]);

  const create = () => {
    const existing = loadIndexes();
    const num = existing.length + 1;
    const now = Date.now();
    const idx: SavedIndex = {
      id: now.toString(36),
      name: `Strat ${num}`,
      traders: [],
      backtestDays: 7,
      // 1-minute poll floor — a sub-minute cadence × N traders trips
      // Polymarket's data-api rate limit (429 / Cloudflare 1015).
      rebalanceMinutes: 1,
      createdAt: now,
      updatedAt: now,
    };
    saveIndex(idx);
    setActiveIndexId(idx.id);
    setActiveId(idx.id);
    setIndexes(loadIndexes());
    onStratChange?.();
    pushToServer(idx);
  };

  const remove = (id: string) => {
    deleteIndex(id);
    const remaining = loadIndexes();
    setIndexes(remaining);
    if (activeId === id) {
      const next = remaining[0] || null;
      setActiveId(next?.id ?? null);
      setActiveIndexId(next?.id ?? null);
    }
    onStratChange?.();
    if (localToken) {
      deleteServerStrat(id, localToken.token);
      setSyncStates((prev) => {
        const next = { ...prev };
        delete next[id];
        return next;
      });
    }
  };

  const startRename = (id: string, currentName: string) => {
    setRenamingId(id);
    setRenameValue(currentName);
  };

  const commitRename = () => {
    if (!renamingId) return;
    const name = renameValue.trim() || "Untitled";
    updateIndex(renamingId, { name, updatedAt: Date.now() });
    setIndexes(loadIndexes());
    const updated = loadIndexes().find((i) => i.id === renamingId);
    if (updated) pushToServer(updated);
    setRenamingId(null);
    setRenameValue("");
    onStratChange?.();
  };

  const setRebalance = (id: string, minutes: number) => {
    updateIndex(id, { rebalanceMinutes: minutes, updatedAt: Date.now() });
    setIndexes(loadIndexes());
    setEditingRebalance(null);
    const updated = loadIndexes().find((i) => i.id === id);
    if (updated) pushToServer(updated);
  };

  const activeIndex = indexes.find((i) => i.id === activeId) ?? null;

  // Sort strats for leaderboard — active strat always pins to top
  const sorted = [...indexes].sort((a, b) => {
    if (a.id === activeId) return -1;
    if (b.id === activeId) return 1;
    if (sortKey === "pnl") return (b.lastPnlAfterCosts ?? -Infinity) - (a.lastPnlAfterCosts ?? -Infinity);
    if (sortKey === "roi") return (b.lastRoi1k ?? -Infinity) - (a.lastRoi1k ?? -Infinity);
    return a.name.localeCompare(b.name);
  });

  const syncBadge = (id: string) => {
    const state = syncStates[id];
    if (!localToken) return null;
    switch (state) {
      case "syncing": return <span className="text-[13px] text-amber-400 animate-pulse ml-1">SYNC</span>;
      case "synced": return <span className="text-[13px] text-green-400 ml-1">OK</span>;
      case "error": return <span className="text-[13px] text-red-400 ml-1">ERR</span>;
      default: return null;
    }
  };

  return (
    <div className="pixel-panel border-2 border-pixel-border bg-pixel-black">
      {/* Header */}
      <div
        className="px-4 py-2.5 border-b-2 border-pixel-border flex items-center justify-between cursor-pointer select-none"
        onClick={() => setCollapsed((v) => !v)}
      >
        <div className="flex items-center gap-3">
          <span className={`text-[11px] text-pixel-gray transition-transform ${collapsed ? "-rotate-90" : ""}`}>▼</span>
          <span className="text-[15px] text-pixel-white font-bold tracking-wider">STRATS</span>
          {collapsed && activeIndex && (
            <span className="text-[13px] font-mono text-green-400">{activeIndex.name}</span>
          )}
          {collapsed && activeIndex?.lastPnlAfterCosts !== undefined && (
            <span className={`text-[13px] font-mono ${(activeIndex.lastPnlAfterCosts ?? 0) >= 0 ? "text-green-400" : "text-red-400"}`}>
              {formatPnlShort(activeIndex.lastPnlAfterCosts)}
            </span>
          )}
        </div>
        <button
          onClick={(e) => { e.stopPropagation(); create(); }}
          className="text-[15px] text-pixel-gray hover:text-green-400 transition-colors px-3 py-1.5 border border-pixel-border hover:border-green-400 font-mono"
          title="Create new strat"
        >
          + NEW
        </button>
      </div>

      {!collapsed && <>
      {/* Leaderboard header */}
      {indexes.length > 0 && (
        <div className="px-4 py-1.5 border-b border-pixel-border/50 flex items-center text-[15px] text-pixel-gray tracking-wider">
          <span className="flex-1 min-w-0">NAME</span>
          <span className="w-16 text-right">P&L</span>
          <span className="w-16 text-right">ROI</span>
          <span className="w-12 text-right">TXS</span>
          <span className="w-12 text-right">REB</span>
          <span className="w-10" />
        </div>
      )}

      {/* Strat list / leaderboard */}
      <div className="max-h-[300px] overflow-y-auto">
        {sorted.length === 0 ? (
          <div className="px-4 py-5 text-center text-[16px] text-pixel-gray">
            NO STRATS — CLICK + NEW
          </div>
        ) : (
          sorted.map((idx, rank) => {
            const isActive = idx.id === activeId;
            const hasPnl = idx.lastPnlAfterCosts !== undefined;
            const pnlColor = (idx.lastPnlAfterCosts ?? 0) >= 0 ? "text-green-400" : "text-red-400";
            const roiColor = (idx.lastRoi1k ?? 0) >= 0 ? "text-green-400" : "text-red-400";
            const rebLabel = REBALANCE_OPTIONS.find((o) => o.value === (idx.rebalanceMinutes ?? 0))?.label ?? "OFF";

            return (
              <div key={idx.id}>
                <div
                  className={`px-4 py-2 flex items-center border-b border-pixel-border/30 cursor-pointer hover:bg-pixel-white/5 transition-colors ${
                    isActive ? "bg-pixel-white/10 border-l-2 border-l-green-400" : "border-l-2 border-l-transparent"
                  }`}
                  onClick={() => select(idx.id)}
                >
                  <div className="flex items-center gap-2 flex-1 min-w-0">
                    <span className="text-[15px] text-pixel-gray w-5 shrink-0 text-right">{rank + 1}.</span>
                    {isActive && <div className="w-2 h-2 bg-green-400 shrink-0" />}
                    {renamingId === idx.id ? (
                      <input
                        type="text"
                        value={renameValue}
                        onChange={(e) => setRenameValue(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") commitRename();
                          if (e.key === "Escape") { setRenamingId(null); setRenameValue(""); }
                        }}
                        onBlur={commitRename}
                        className="text-[15px] font-mono bg-transparent border-b border-green-400 text-green-400 outline-none w-28"
                        autoFocus
                        onClick={(e) => e.stopPropagation()}
                      />
                    ) : (
                      <span
                        className={`text-[15px] font-mono truncate ${isActive ? "text-green-400 font-bold" : "text-pixel-white"}`}
                        onDoubleClick={(e) => { e.stopPropagation(); startRename(idx.id, idx.name); }}
                        title="Double-click to rename"
                      >
                        {idx.name}
                      </span>
                    )}
                    <span className="text-[15px] text-pixel-gray shrink-0">{idx.traders.length}T</span>
                    {idx.liveEnabled && <div className="w-2.5 h-2.5 rounded-full bg-green-400 animate-pulse shrink-0" title="LIVE" />}
                    {syncBadge(idx.id)}
                  </div>
                  <div className="flex items-center shrink-0">
                    <span className={`w-16 text-right text-[15px] font-mono ${hasPnl ? pnlColor : "text-pixel-gray"}`}>
                      {formatPnlShort(idx.lastPnlAfterCosts)}
                    </span>
                    <span className={`w-16 text-right text-[15px] font-mono ${hasPnl ? roiColor : "text-pixel-gray"}`}>
                      {formatPctShort(idx.lastRoi1k)}
                    </span>
                    <span className="w-12 text-right text-[16px] text-pixel-gray font-mono">
                      {idx.lastTradeCount ?? "—"}
                    </span>
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        setEditingRebalance(editingRebalance === idx.id ? null : idx.id);
                      }}
                      className={`w-12 text-right text-[16px] font-mono transition-colors ${
                        (idx.rebalanceMinutes ?? 0) > 0
                          ? "text-green-400 hover:text-amber-400"
                          : "text-pixel-gray hover:text-pixel-white"
                      }`}
                      title="Rebalance period"
                    >
                      {rebLabel}
                    </button>
                    <div className="w-10 flex items-center justify-end gap-1">
                      {localToken && syncStates[idx.id] !== "synced" && (
                        <button
                          onClick={(e) => { e.stopPropagation(); pushToServer(idx); }}
                          className="text-[14px] text-amber-400 hover:text-green-400"
                          title="Save to server"
                        >
                          ↑
                        </button>
                      )}
                      <button
                        onClick={(e) => { e.stopPropagation(); remove(idx.id); }}
                        className="text-[16px] text-pixel-gray hover:text-red-400"
                      >
                        x
                      </button>
                    </div>
                  </div>
                </div>

                {/* Rebalance period selector */}
                {editingRebalance === idx.id && (
                  <div className="px-4 py-2 border-b border-pixel-border/30 bg-pixel-black/80 flex items-center gap-2">
                    <span className="text-[15px] text-pixel-gray mr-1">REBALANCE:</span>
                    {REBALANCE_OPTIONS.map((opt) => (
                      <button
                        key={opt.value}
                        onClick={(e) => { e.stopPropagation(); setRebalance(idx.id, opt.value); }}
                        className={`text-[15px] px-2.5 py-0.5 rounded transition-colors ${
                          (idx.rebalanceMinutes ?? 0) === opt.value
                            ? "text-green-400 bg-green-400/10"
                            : "text-pixel-gray hover:text-pixel-white"
                        }`}
                      >
                        {opt.label}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>
      </>}
    </div>
  );
}
