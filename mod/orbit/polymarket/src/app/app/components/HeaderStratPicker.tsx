"use client";

// One-line strat selector for the top header: the active strat's name opens
// a dropdown of every saved strat (click to switch), and the [+] beside it
// creates a new strat. Mirrors StratSidebar's semantics exactly — same
// indexStore localStorage store, same `strat-updated` window event, same
// best-effort server sync — so the sidebar, CopyIndex, checklist and this
// picker can never disagree about which strat is active.

import { useCallback, useEffect, useRef, useState } from "react";
import {
  loadIndexes,
  saveIndex,
  updateIndex,
  getActiveIndexId,
  setActiveIndexId,
  equalWeightTraders,
} from "../lib/indexStore";
import { pushStrat } from "../lib/stratSync";
import { useAuth } from "../context/AuthContext";
import { useFilters } from "../context/FiltersContext";
import { fetchTopTraderAddresses } from "../lib/polymarket";
import { useEmbedded } from "../lib/embedded";
import type { SavedIndex } from "../lib/types";

export default function HeaderStratPicker() {
  const embedded = useEmbedded();
  const { localToken } = useAuth();
  const { category, marketQuery, daysAgo, minPerDay } = useFilters();
  const [indexes, setIndexes] = useState<SavedIndex[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  const reload = useCallback(() => {
    setIndexes(loadIndexes());
    setActiveId(getActiveIndexId());
  }, []);

  // Same hydration pattern as StratSidebar: poll localStorage so edits made
  // anywhere (sidebar, leaderboard toggles, backtest writes) show up here.
  useEffect(() => {
    reload();
    const t = setInterval(reload, 2000);
    window.addEventListener("strat-updated", reload);
    return () => {
      clearInterval(t);
      window.removeEventListener("strat-updated", reload);
    };
  }, [reload]);

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

      {open && (
        <div
          className="absolute left-0 top-full mt-1.5 z-50 min-w-[220px] max-w-[300px] rounded-[var(--radius-sm)] backdrop-blur-md p-1.5 flex flex-col gap-0.5 max-h-[60vh] overflow-y-auto"
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
              <button
                key={idx.id}
                onClick={() => select(idx.id)}
                className={`relative flex items-center gap-2 rounded-[var(--radius-sm)] px-3 py-2 text-left transition-colors ${
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
                <span className="flex-1 min-w-0 truncate text-[12px] font-mono font-semibold">
                  {idx.name}
                </span>
                <span className="text-[10px] text-pixel-gray shrink-0">{idx.traders.length}T</span>
              </button>
            );
          })}
          <button
            onClick={create}
            className="mt-0.5 rounded-[var(--radius-sm)] border border-dashed border-pixel-border px-3 py-2 text-left text-[11px] font-mono font-semibold tracking-[0.08em] text-pixel-gray hover:text-green-400 hover:border-green-400/60 transition-colors"
          >
            + NEW STRAT
          </button>
        </div>
      )}
    </div>
  );
}
