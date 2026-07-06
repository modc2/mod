"use client";

// Global filter state shared across leaderboard / subnets / validators pages.
// Persists to localStorage so a refresh doesn't blow away the user's view.

import {
  createContext, useCallback, useContext, useEffect, useState, type ReactNode,
} from "react";

export type SortKey = "pnl_pct" | "pnl_tao" | "total_stake_tao" | "num_subnets";
export type SortDir = "asc" | "desc";

interface FiltersContextValue {
  // Time window (days) for PnL calcs
  days: number;
  setDays: (d: number) => void;
  // Free-text search (matches label or SS58 prefix)
  search: string;
  setSearch: (s: string) => void;
  // Sort
  sortKey: SortKey;
  sortDir: SortDir;
  setSort: (k: SortKey, d?: SortDir) => void;
  toggleSort: (k: SortKey) => void;
  // Minimum subnets to qualify for leaderboard
  minSubnets: number;
  setMinSubnets: (n: number) => void;
  // Subnet filter for portfolio/positions views
  netuidFilter: number | null;
  setNetuidFilter: (n: number | null) => void;
  // Refresh tick — anything subscribing to this re-fetches
  reloadKey: number;
  reload: () => void;
}

const FiltersContext = createContext<FiltersContextValue | null>(null);

const STORAGE = "ct_filters_v1";

interface StoredFilters {
  days?: number;
  sortKey?: SortKey;
  sortDir?: SortDir;
  minSubnets?: number;
}

export function useFilters() {
  const ctx = useContext(FiltersContext);
  if (!ctx) throw new Error("useFilters must be used inside <FiltersProvider>");
  return ctx;
}

export function FiltersProvider({ children }: { children: ReactNode }) {
  const [days, setDaysState] = useState(7);
  const [search, setSearch] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("pnl_pct");
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const [minSubnets, setMinSubnetsState] = useState(0);
  const [netuidFilter, setNetuidFilter] = useState<number | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  // Hydrate persisted prefs once
  useEffect(() => {
    try {
      const raw = localStorage.getItem(STORAGE);
      if (!raw) return;
      const v: StoredFilters = JSON.parse(raw);
      if (typeof v.days === "number") setDaysState(v.days);
      if (v.sortKey) setSortKey(v.sortKey);
      if (v.sortDir) setSortDir(v.sortDir);
      if (typeof v.minSubnets === "number") setMinSubnetsState(v.minSubnets);
    } catch {}
  }, []);

  const persist = useCallback((next: Partial<StoredFilters>) => {
    try {
      const cur: StoredFilters = JSON.parse(localStorage.getItem(STORAGE) || "{}");
      localStorage.setItem(STORAGE, JSON.stringify({ ...cur, ...next }));
    } catch {}
  }, []);

  const setDays = useCallback((d: number) => {
    setDaysState(d);
    persist({ days: d });
  }, [persist]);

  const setMinSubnets = useCallback((n: number) => {
    setMinSubnetsState(n);
    persist({ minSubnets: n });
  }, [persist]);

  const setSort = useCallback((k: SortKey, d: SortDir = "desc") => {
    setSortKey(k);
    setSortDir(d);
    persist({ sortKey: k, sortDir: d });
  }, [persist]);

  const toggleSort = useCallback((k: SortKey) => {
    if (k === sortKey) {
      const next: SortDir = sortDir === "desc" ? "asc" : "desc";
      setSortDir(next);
      persist({ sortDir: next });
    } else {
      setSortKey(k);
      setSortDir("desc");
      persist({ sortKey: k, sortDir: "desc" });
    }
  }, [sortKey, sortDir, persist]);

  const reload = useCallback(() => setReloadKey((k) => k + 1), []);

  return (
    <FiltersContext.Provider
      value={{
        days, setDays,
        search, setSearch,
        sortKey, sortDir, setSort, toggleSort,
        minSubnets, setMinSubnets,
        netuidFilter, setNetuidFilter,
        reloadKey, reload,
      }}
    >
      {children}
    </FiltersContext.Provider>
  );
}
