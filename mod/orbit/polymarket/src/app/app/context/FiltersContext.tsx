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

// Has ANY useUrlSync instance seeded from the URL since this JS context
// loaded? Module-scoped on purpose: it survives client-side navigations and
// resets on a real page load. The keyword (?q=) seeds from the URL only on
// that first load — afterwards the LIVE context is the user's most recent
// action, and re-adopting an older history entry's q (back-nav to the board,
// remount of a page whose URL was written before the user cleared the box)
// resurrects a keyword they just removed. Topic/window params (?mq=, ?days=)
// keep seeding on every mount: FindTraders hands a profile its ranking slice
// through exactly those params.
let urlSeededThisSession = false;

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
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // 1. On mount: read URL params into filter state (URL wins; q first-load only)
  useEffect(() => {
    if (initialized.current) return;
    initialized.current = true;
    const firstLoad = !urlSeededThisSession;
    urlSeededThisSession = true;

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

    if (d !== null) filters.setDaysAgo(d);
    if (q !== null && firstLoad) filters.setSearch(q);
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

  // 2. On filter change: debounced URL write.
  //
  // The write is gated on an actual DIFF against the live URL, never on a
  // "skip the next write" flag. The old flag was set unconditionally whenever
  // the URL changed — including by our own replace — so the very next filter
  // change was silently swallowed and the URL sat one change behind the
  // screen: clear the keyword and ?q= kept the old one, reload/back brought
  // it back. Diffing can't desync that way, and it also suppresses the
  // pointless rewrite after effect #1/#3 adopt URL → state (state now equals
  // the URL, so there is no diff).
  useEffect(() => {
    if (!initialized.current) return;

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
      // Compare against the URL as it is NOW (window.location, not the
      // hook's render-time snapshot) — only a real difference is worth a
      // router.replace. Params outside PARAM_MAP (e.g. ?tf=) don't count as
      // a diff on their own, so they survive until a mapped param changes.
      const live = new URLSearchParams(window.location.search);
      const differs = Object.values(PARAM_MAP).some(
        (param) => (params.get(param) ?? "") !== (live.get(param) ?? ""),
      );
      if (!differs) return;
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
  //    `q` is deliberately NOT adopted here: a history entry's keyword is a
  //    snapshot of the box at write time, and the user may have cleared or
  //    changed it since — the live context wins (see urlSeededThisSession).
  const mountedRef = useRef(false);
  useEffect(() => {
    if (!initialized.current) return;
    if (!mountedRef.current) {
      mountedRef.current = true;
      return;
    }
    const d = searchParams.get(PARAM_MAP.daysAgo) ?? "";
    const cat = searchParams.get(PARAM_MAP.category) ?? "";
    const mq = searchParams.get(PARAM_MAP.marketQuery) ?? "";
    const mt = searchParams.get(PARAM_MAP.minTrades) ?? "";
    const mpd = searchParams.get(PARAM_MAP.minPerDay) ?? "0";
    const mv = searchParams.get(PARAM_MAP.minVolume) ?? "100";
    const mb = searchParams.get(PARAM_MAP.minBuyVolume) ?? "";
    const ms = searchParams.get(PARAM_MAP.minSellVolume) ?? "";
    const mp = searchParams.get(PARAM_MAP.minPnl) ?? "";

    if (d !== filters.daysAgo) filters.setDaysAgo(d);
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
