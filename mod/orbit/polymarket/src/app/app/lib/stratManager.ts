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
  getActiveIndexId, setActiveIndexId, equalWeightTraders, uniqueIndexName,
} from "./indexStore";
import {
  pushStrat, deleteServerStrat, syncStrats,
  publishStrat, unpublishStrat, type PublicStratEntry,
} from "./stratSync";
import { shortAddress } from "./auth";
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
  /** IDENTITY strat: copy exactly ONE trader — the watchlist is that single
      address at weight 1 and stays that way. */
  createIdentity: (address: string) => SavedIndex;
  fork: (id: string) => SavedIndex | null;
  forkDefault: (t: StratTemplate) => SavedIndex;
  /** Fork a strat from the PUBLIC gallery into this account, private. */
  importPublic: (entry: PublicStratEntry) => SavedIndex;
  /** Publish (true) / unpublish (false) one of my strats. Every strat is
      private by default; publishing puts it — plaintext — in the community
      gallery. Resolves false when there's no local token to publish with. */
  setVisibility: (id: string, pub: boolean) => Promise<boolean>;
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

  /** IDENTITY strat: the whole strategy is one leader. Single-address
      watchlist at weight 1, no leaderboard seeding — the strat IS that
      trader, and the name says so. */
  const createIdentity = useCallback((address: string): SavedIndex => {
    const addr = address.trim().toLowerCase();
    const now = Date.now();
    const idx: SavedIndex = {
      id: now.toString(36),
      name: uniqueIndexName(`IDENTITY ${shortAddress(addr)}`),
      identity: addr,
      traders: [{ address: addr, weight: 1 }],
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
    return idx;
  }, [broadcast, localToken]);

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

  /** Fork a PUBLIC gallery strat into a private strat this account owns.
      Same contract as forkIndex: the strategy comes along, the run state and
      the publication do not — imports always land private, stopped, with
      lineage back to the gallery id. */
  const importPublic = useCallback((entry: PublicStratEntry): SavedIndex => {
    const src = entry.strat;
    const now = Date.now();
    const {
      lastPnl: _p, lastPnlAfterCosts: _pc, lastRoi1k: _r,
      lastTradeCount: _tc, lastBacktestAt: _ba,
      visibility: _v, owner: _o,
      ...strategy
    } = src;
    const copy: SavedIndex = {
      ...strategy,
      traders: (src.traders ?? []).map((t) => ({ ...t })),
      ...(src.tradeFilters && { tradeFilters: { ...src.tradeFilters } }),
      ...(src.filter && { filter: { ...src.filter } }),
      ...(src.momentum && { momentum: { ...src.momentum } }),
      id: now.toString(36),
      name: uniqueIndexName(src.name),
      forkedFrom: entry.id,
      liveEnabled: false,
      createdAt: now,
      updatedAt: now,
    };
    saveIndex(copy);
    setActiveIndexId(copy.id);
    broadcast();
    if (localToken) pushStrat(copy, localToken.token);
    return copy;
  }, [broadcast, localToken]);

  /** Flip one of my strats public/private. Publishing needs the local token
      (it is the credential that lets this account unpublish later). */
  const setVisibility = useCallback(async (id: string, pub: boolean): Promise<boolean> => {
    if (!localToken) return false;
    updateIndex(id, {
      visibility: pub ? "public" : "private",
      ...(pub ? { owner: auth.address?.toLowerCase() ?? "" } : {}),
      updatedAt: Date.now(),
    });
    const updated = loadIndexes().find((i) => i.id === id);
    broadcast();
    if (!updated) return false;
    pushStrat(updated, localToken.token); // keep the private copy in sync too
    return pub
      ? publishStrat(updated, updated.owner ?? "", localToken.token)
      : unpublishStrat(id, localToken.token);
  }, [broadcast, localToken, auth.address]);

  const rename = useCallback((id: string, name: string) => {
    updateIndex(id, { name: name.trim() || "Untitled", updatedAt: Date.now() });
    const updated = loadIndexes().find((i) => i.id === id);
    if (updated && localToken) {
      pushStrat(updated, localToken.token);
      // A published strat's gallery card must not go stale on rename.
      if (updated.visibility === "public") {
        publishStrat(updated, updated.owner ?? "", localToken.token);
      }
    }
    broadcast();
  }, [broadcast, localToken]);

  const confirmDelete = useCallback(() => {
    const id = pendingDelete;
    if (!id) return;
    setPendingDelete(null);
    // Deleting a published strat takes it off the gallery too — a card whose
    // owner is gone can never be updated or unpublished again.
    const wasPublic = loadIndexes().find((i) => i.id === id)?.visibility === "public";
    deleteIndex(id);
    const remaining = loadIndexes();
    if (getActiveIndexId() === id) setActiveIndexId(remaining[0]?.id ?? null);
    broadcast();
    if (localToken) {
      deleteServerStrat(id, localToken.token);
      if (wasPublic) unpublishStrat(id, localToken.token);
    }
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
    createIdentity,
    fork,
    forkDefault,
    importPublic,
    setVisibility,
    rename,
    requestDelete: setPendingDelete,
    pendingDelete,
    confirmDelete,
    cancelDelete: () => setPendingDelete(null),
    stopStrat,
  };
}
