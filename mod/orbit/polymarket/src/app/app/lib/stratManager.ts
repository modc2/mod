"use client";

// THE strat manager, as a hook. Create / fork / rename / delete / select, plus
// the localStorage store, the `strat-updated` broadcast and the best-effort
// server sync — one implementation, so the header picker and the STRAT HUB can
// never disagree about what a strat is or which one is active.
//
// It owns state, not UI: the delete CONFIRMATION is a component
// (ConfirmDeleteStrat), and each consumer decides where to put it.

import { useCallback, useEffect, useState } from "react";
import {
  loadIndexes, saveIndex, updateIndex, deleteIndex, forkIndex,
  getActiveIndexId, setActiveIndexId, equalWeightTraders,
} from "./indexStore";
import { pushStrat, deleteServerStrat, syncStrats } from "./stratSync";
import { fetchTopTraderAddresses } from "./polymarket";
import { forkDefaultStrat, type StratTemplate } from "./defaultStrats";
import { stopLiveSession } from "./liveSessions";
import { useAuth } from "../context/AuthContext";
import { useFilters } from "../context/FiltersContext";
import type { SavedIndex } from "./types";

export interface StratManager {
  indexes: SavedIndex[];
  activeId: string | null;
  /** Re-read the store (the hook already polls + listens for `strat-updated`). */
  reload: () => void;
  /** Re-read AND tell every other mounted consumer to do the same. */
  broadcast: () => void;
  select: (id: string) => void;
  create: () => SavedIndex;
  fork: (id: string) => SavedIndex | null;
  forkDefault: (t: StratTemplate) => SavedIndex;
  rename: (id: string, name: string) => void;
  /** Open the delete confirmation for `id` (nothing is deleted yet). */
  requestDelete: (id: string) => void;
  pendingDelete: string | null;
  confirmDelete: () => void;
  cancelDelete: () => void;
  /** Take one strat's session down without touching the wallet's others. */
  stopStrat: (id: string) => Promise<void>;
}

export function useStratManager(): StratManager {
  const { localToken, auth } = useAuth();
  const { category, marketQuery, daysAgo, minPerDay } = useFilters();
  const [indexes, setIndexes] = useState<SavedIndex[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);
  const [initialSynced, setInitialSynced] = useState(false);

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

  // Initial local↔server strat merge — a fresh browser pulls the account's
  // strats down no matter which page it lands on.
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

  const broadcast = useCallback(() => {
    reload();
    window.dispatchEvent(new Event("strat-updated"));
  }, [reload]);

  const select = useCallback((id: string) => {
    setActiveIndexId(id);
    setActiveId(id);
    broadcast();
  }, [broadcast]);

  const create = useCallback((): SavedIndex => {
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
    broadcast();
    if (localToken) pushStrat(idx, localToken.token);

    // Seed with the top 10 traders matching the current leaderboard filters.
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
    return idx;
  }, [broadcast, localToken, category, marketQuery, daysAgo, minPerDay]);

  /** Fork a saved strat: an independent copy of the strategy (watchlist,
      weights, every param), stopped and un-funded, named "<NAME> COPY". The
      original keeps running untouched; the engine is keyed per strat id. */
  const fork = useCallback((id: string): SavedIndex | null => {
    const copy = forkIndex(id);
    if (!copy) return null;
    setActiveIndexId(copy.id);
    setActiveId(copy.id);
    broadcast();
    if (localToken) pushStrat(copy, localToken.token);
    return copy;
  }, [broadcast, localToken]);

  /** Fork a built-in template into a user-owned strat: instant strat with the
      recipe's params, traders seeded async from the live leaderboard. */
  const forkDefault = useCallback((t: StratTemplate): SavedIndex => {
    const idx = forkDefaultStrat(t, (seeded) => {
      broadcast();
      if (localToken) pushStrat(seeded, localToken.token);
    });
    broadcast();
    if (localToken) pushStrat(idx, localToken.token);
    return idx;
  }, [broadcast, localToken]);

  const rename = useCallback((id: string, name: string) => {
    updateIndex(id, { name: name.trim() || "Untitled", updatedAt: Date.now() });
    const updated = loadIndexes().find((i) => i.id === id);
    if (updated && localToken) pushStrat(updated, localToken.token);
    broadcast();
  }, [broadcast, localToken]);

  const confirmDelete = useCallback(() => {
    const id = pendingDelete;
    if (!id) return;
    setPendingDelete(null);
    deleteIndex(id);
    const remaining = loadIndexes();
    if (getActiveIndexId() === id) setActiveIndexId(remaining[0]?.id ?? null);
    broadcast();
    if (localToken) deleteServerStrat(id, localToken.token);
  }, [pendingDelete, broadcast, localToken]);

  const stopStrat = useCallback(async (id: string) => {
    updateIndex(id, { liveEnabled: false, updatedAt: Date.now() });
    broadcast();
    if (auth.address) await stopLiveSession(auth.address, id);
  }, [broadcast, auth.address]);

  return {
    indexes,
    activeId,
    reload,
    broadcast,
    select,
    create,
    fork,
    forkDefault,
    rename,
    requestDelete: setPendingDelete,
    pendingDelete,
    confirmDelete,
    cancelDelete: () => setPendingDelete(null),
    stopStrat,
  };
}
