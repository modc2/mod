"use client";

import {
  createContext, useContext, useState, useEffect, useRef, ReactNode, useCallback,
} from "react";
import { useSearchParams, useRouter, usePathname } from "next/navigation";
import { CategorySlug } from "../lib/polymarket";

export type SortMode = "volume" | "liquidity" | "end_date_min";

interface FiltersContextValue {
  search: string;
  setSearch: (v: string) => void;
  sort: SortMode;
  setSort: (v: SortMode) => void;
  category: CategorySlug;
  setCategory: (v: CategorySlug) => void;
  // Free-text market-topic filter for traders (e.g. "bitcoin", "price of
  // bitcoin"). Narrows the leaderboard to traders active in matching markets
  // and recomputes their stats from only those markets. Distinct from `search`
  // (which also matches wallet addresses) and broader than the fixed
  // `category` keyword buckets.
  marketQuery: string;
  setMarketQuery: (v: string) => void;
  daysAgo: string;
  setDaysAgo: (v: string) => void;
  minTrades: string;
  setMinTrades: (v: string) => void;
  minPerDay: string;
  setMinPerDay: (v: string) => void;
  minVolume: string;
  setMinVolume: (v: string) => void;
  minBuyVolume: string;
  setMinBuyVolume: (v: string) => void;
  minSellVolume: string;
  setMinSellVolume: (v: string) => void;
  minPnl: string;
  setMinPnl: (v: string) => void;
  reloadKey: number;
  reload: () => void;
}

const FiltersContext = createContext<FiltersContextValue | null>(null);

const STORAGE_KEY = "poly8bit_filters_v1";

export function useFilters() {
  const ctx = useContext(FiltersContext);
  if (!ctx) throw new Error("useFilters must be used inside <FiltersProvider>");
  return ctx;
}

// ── URL param mapping ──
const PARAM_MAP = {
  daysAgo: "days",
  search: "q",
  category: "cat",
  marketQuery: "mq",
  minTrades: "mint",
  minPerDay: "minpd",
  minVolume: "minvol",
  minBuyVolume: "minbuy",
  minSellVolume: "minsell",
  minPnl: "minpnl",
} as const;

// Defaults — when a value equals its default, omit from URL
const DEFAULTS: Record<string, string> = {
  daysAgo: "",
  search: "",
  category: "",
  marketQuery: "",
  minTrades: "",
  minPerDay: "0",
  minVolume: "100",
  minBuyVolume: "",
  minSellVolume: "",
  minPnl: "",
};

/**
 * Call this hook in any page that should sync filter state with URL params.
 * Only trader pages should call this — other pages (markets, portfolio) don't
 * need trader filters in their URLs.
 */
export function useUrlSync() {
  const filters = useFilters();
  const searchParams = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const initialized = useRef(false);
  const skipUrlWrite = useRef(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // 1. On mount: read URL params into filter state (URL wins)
  useEffect(() => {
    if (initialized.current) return;
    initialized.current = true;

    const read = (param: string) => searchParams.get(param);
    const d = read(PARAM_MAP.daysAgo);
    const q = read(PARAM_MAP.search);
    const cat = read(PARAM_MAP.category);
    const mq = read(PARAM_MAP.marketQuery);
    const mt = read(PARAM_MAP.minTrades);
    const mpd = read(PARAM_MAP.minPerDay);
    const mv = read(PARAM_MAP.minVolume);
    const mb = read(PARAM_MAP.minBuyVolume);
    const ms = read(PARAM_MAP.minSellVolume);
    const mp = read(PARAM_MAP.minPnl);

    // Only seed from URL if at least one param is present
    const hasUrlParams = d !== null || q !== null || cat !== null || mq !== null || mt !== null || mpd !== null ||
      mv !== null || mb !== null || ms !== null || mp !== null;
    if (!hasUrlParams) return;

    skipUrlWrite.current = true;
    if (d !== null) filters.setDaysAgo(d);
    if (q !== null) filters.setSearch(q);
    if (cat !== null) filters.setCategory(cat as CategorySlug);
    if (mq !== null) filters.setMarketQuery(mq);
    if (mt !== null) filters.setMinTrades(mt);
    if (mpd !== null) filters.setMinPerDay(mpd);
    if (mv !== null) filters.setMinVolume(mv);
    if (mb !== null) filters.setMinBuyVolume(mb);
    if (ms !== null) filters.setMinSellVolume(ms);
    if (mp !== null) filters.setMinPnl(mp);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 2. On filter change: debounced URL write
  useEffect(() => {
    if (!initialized.current) return;
    if (skipUrlWrite.current) {
      skipUrlWrite.current = false;
      return;
    }

    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      const params = new URLSearchParams();
      const vals: Record<string, string> = {
        daysAgo: filters.daysAgo,
        search: filters.search,
        category: filters.category,
        marketQuery: filters.marketQuery,
        minTrades: filters.minTrades,
        minPerDay: filters.minPerDay,
        minVolume: filters.minVolume,
        minBuyVolume: filters.minBuyVolume,
        minSellVolume: filters.minSellVolume,
        minPnl: filters.minPnl,
      };
      for (const [key, param] of Object.entries(PARAM_MAP)) {
        const val = vals[key];
        if (val && val !== DEFAULTS[key]) {
          params.set(param, val);
        }
      }
      const qs = params.toString();
      const target = qs ? `${pathname}?${qs}` : pathname;
      router.replace(target, { scroll: false });
    }, 300);

    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [
    filters.daysAgo, filters.search, filters.category, filters.marketQuery,
    filters.minTrades, filters.minPerDay, filters.minVolume, filters.minBuyVolume,
    filters.minSellVolume, filters.minPnl, pathname, router,
  ]);

  // 3. On popstate / external URL change: re-read into state.
  //    Skip the FIRST fire (mount) — effect #1 handles initial seeding.
  //    This effect only handles subsequent URL changes (back/forward nav).
  const mountedRef = useRef(false);
  useEffect(() => {
    if (!initialized.current) return;
    if (!mountedRef.current) {
      mountedRef.current = true;
      return;
    }
    const d = searchParams.get(PARAM_MAP.daysAgo) ?? "";
    const q = searchParams.get(PARAM_MAP.search) ?? "";
    const cat = searchParams.get(PARAM_MAP.category) ?? "";
    const mq = searchParams.get(PARAM_MAP.marketQuery) ?? "";
    const mt = searchParams.get(PARAM_MAP.minTrades) ?? "";
    const mpd = searchParams.get(PARAM_MAP.minPerDay) ?? "0";
    const mv = searchParams.get(PARAM_MAP.minVolume) ?? "100";
    const mb = searchParams.get(PARAM_MAP.minBuyVolume) ?? "";
    const ms = searchParams.get(PARAM_MAP.minSellVolume) ?? "";
    const mp = searchParams.get(PARAM_MAP.minPnl) ?? "";

    skipUrlWrite.current = true;
    if (d !== filters.daysAgo) filters.setDaysAgo(d);
    if (q !== filters.search) filters.setSearch(q);
    if (cat !== filters.category) filters.setCategory(cat as CategorySlug);
    if (mq !== filters.marketQuery) filters.setMarketQuery(mq);
    if (mt !== filters.minTrades) filters.setMinTrades(mt);
    if (mpd !== filters.minPerDay) filters.setMinPerDay(mpd);
    if (mv !== filters.minVolume) filters.setMinVolume(mv);
    if (mb !== filters.minBuyVolume) filters.setMinBuyVolume(mb);
    if (ms !== filters.minSellVolume) filters.setMinSellVolume(ms);
    if (mp !== filters.minPnl) filters.setMinPnl(mp);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams]);
}

/** Build a query string from current filter state (for navigation links).
 *  Pass `excludeSearch: true` to omit the search/q param — useful when
 *  navigating to a page where search has a different meaning. */
export function useFilterParams(opts?: { excludeSearch?: boolean }): string {
  const f = useFilters();
  const params = new URLSearchParams();
  const vals: Record<string, string> = {
    daysAgo: f.daysAgo,
    search: opts?.excludeSearch ? "" : f.search,
    category: f.category,
    marketQuery: f.marketQuery,
    minTrades: f.minTrades,
    minPerDay: f.minPerDay,
    minVolume: f.minVolume,
    minBuyVolume: f.minBuyVolume,
    minSellVolume: f.minSellVolume,
    minPnl: f.minPnl,
  };
  for (const [key, param] of Object.entries(PARAM_MAP)) {
    const val = vals[key];
    if (val && val !== DEFAULTS[key]) params.set(param, val);
  }
  return params.toString();
}

export function FiltersProvider({ children }: { children: ReactNode }) {
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<SortMode>("volume");
  const [category, setCategory] = useState<CategorySlug>("");
  const [marketQuery, setMarketQuery] = useState<string>("");
  const [daysAgo, setDaysAgo] = useState<string>("");
  const [minTrades, setMinTrades] = useState<string>("");
  const [minPerDay, setMinPerDay] = useState<string>("0");
  const [minVolume, setMinVolume] = useState<string>("100");
  const [minBuyVolume, setMinBuyVolume] = useState<string>("");
  const [minSellVolume, setMinSellVolume] = useState<string>("");
  const [minPnl, setMinPnl] = useState<string>("");
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    try {
      const raw = sessionStorage.getItem(STORAGE_KEY);
      if (!raw) return;
      const saved = JSON.parse(raw) as Partial<FiltersContextValue>;
      if (typeof saved.search === "string") setSearch(saved.search);
      if (typeof saved.sort === "string") setSort(saved.sort as SortMode);
      if (typeof saved.category === "string") setCategory(saved.category as CategorySlug);
      if (typeof saved.marketQuery === "string") setMarketQuery(saved.marketQuery);
      if (typeof saved.daysAgo === "string") setDaysAgo(saved.daysAgo);
      if (typeof (saved as Record<string, unknown>).minTrades === "string") setMinTrades((saved as Record<string, unknown>).minTrades as string);
      if (typeof saved.minPerDay === "string") setMinPerDay(saved.minPerDay);
      if (typeof saved.minVolume === "string") setMinVolume(saved.minVolume);
      if (typeof saved.minBuyVolume === "string") setMinBuyVolume(saved.minBuyVolume);
      if (typeof saved.minSellVolume === "string") setMinSellVolume(saved.minSellVolume);
      if (typeof saved.minPnl === "string") setMinPnl(saved.minPnl);
    } catch {}
  }, []);

  useEffect(() => {
    try {
      sessionStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({
          search, sort, category, marketQuery, daysAgo, minTrades, minPerDay,
          minVolume, minBuyVolume, minSellVolume, minPnl,
        }),
      );
    } catch {}
  }, [search, sort, category, marketQuery, daysAgo, minTrades, minPerDay, minVolume, minBuyVolume, minSellVolume, minPnl]);

  const reload = useCallback(() => setReloadKey((k) => k + 1), []);

  // Auto-refresh every leaderboard/trader view hourly — matches the backend's
  // own hourly cache-warm cycle, so pinned filter presets ("BTC traders,
  // ≥3 trades/day") stay current without the user manually hitting reload.
  useEffect(() => {
    const t = setInterval(() => setReloadKey((k) => k + 1), 60 * 60 * 1000);
    return () => clearInterval(t);
  }, []);

  return (
    <FiltersContext.Provider
      value={{
        search, setSearch,
        sort, setSort,
        category, setCategory,
        marketQuery, setMarketQuery,
        daysAgo, setDaysAgo,
        minTrades, setMinTrades,
        minPerDay, setMinPerDay,
        minVolume, setMinVolume,
        minBuyVolume, setMinBuyVolume,
        minSellVolume, setMinSellVolume,
        minPnl, setMinPnl,
        reloadKey, reload,
      }}
    >
      {children}
    </FiltersContext.Provider>
  );
}
