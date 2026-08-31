"use client";

import { useState, useEffect, useCallback, useMemo, useRef } from "react";
import { useRouter } from "next/navigation";
import {
  fetchTopTradersStream, ActiveTradersProgress,
  fetchTradersPage,
  formatVolume, formatPnl, TopTrader,
  CategorySlug, CATEGORIES,
  matchTraderSearch, matchTraderCategory,
  DEFAULT_ACTIVE_HOURS,
} from "../lib/polymarket";
import { traderMatchesMarketQuery, marketQueryMatchCount } from "../lib/marketQuery";
import { shortAddress } from "@/lib/auth";
import { useFilters, useFilterParams } from "../context/FiltersContext";
import { loadIndexes, getActiveIndexId } from "../lib/indexStore";
import SyncScheduleChip from "./SyncScheduleChip";
import { fetchSyncSchedule } from "../lib/syncSchedule";

import {
  DEFAULT_FORMULA, compileFormula, formatScore, scoreInputs,
  loadSavedFormula, matchScorePreset, saveFormula,
} from "../lib/scoreFormula";
import ScoreRatioChips from "./ScoreRatioChips";
import Sparkline from "./Sparkline";

type TraderSort = "score" | "volume" | "pnl" | "positions" | "last";

type SortDir = "asc" | "desc";
const PAGE_SIZE = 50;

// "12s ago" / "3m ago" / "1h 4m ago" — drops sub-5s precision (the staleness
// ticker re-renders every 5s anyway, so finer granularity is just noise).
function formatAgo(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return "just now";
  const sec = Math.floor(ms / 1000);
  if (sec < 5) return "just now";
  if (sec < 60) return `${sec}s ago`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  return `${hr}h ${min % 60}m ago`;
}

// ETA formatter — "47s" / "3m" / "1h12m". Caps at "—" for unknown/non-finite.
function formatEta(sec: number): string {
  if (!Number.isFinite(sec) || sec < 0) return "—";
  if (sec < 60) return `${Math.round(sec)}s`;
  const min = Math.round(sec / 60);
  if (min < 60) return `${min}m`;
  const hr = Math.floor(min / 60);
  return `${hr}h${(min % 60).toString().padStart(2, "0")}m`;
}

function SortArrow({ active, dir }: { active: boolean; dir: SortDir }) {
  return (
    <span className="inline-block w-3 ml-0.5 text-center">
      {active ? (dir === "desc" ? "\u25BC" : "\u25B2") : ""}
    </span>
  );
}

/* ── Rank badge for top 3 ── */
function RankBadge({ rank }: { rank: number }) {
  if (rank === 1) return <span className="text-[13px] text-yellow-400" title="#1">&#9733;</span>;
  if (rank === 2) return <span className="text-[13px] text-gray-300" title="#2">&#9733;</span>;
  if (rank === 3) return <span className="text-[13px] text-amber-600" title="#3">&#9733;</span>;
  return <span className="text-[13px] text-pixel-gray font-mono">{rank}</span>;
}

interface CopyTradingProps {
  days?: number;
  minTradesPerDay?: number;
  reloadKey?: number;
  search?: string;
  category?: CategorySlug;
  /** Free-text market-topic filter (e.g. "bitcoin") — narrows the leaderboard
      to traders active in matching markets, with stats recomputed from only
      those markets. */
  marketQuery?: string;
  onSelect?: (addr: string) => void;
  selectedAddresses?: string[];
  compact?: boolean;
}

export default function CopyTrading({
  days = 7,
  minTradesPerDay = 0,
  reloadKey = 0,
  search = "",
  category = "",
  marketQuery = "",
  onSelect,
  selectedAddresses = [],
  compact = false,
}: CopyTradingProps = {}) {
  const router = useRouter();
  const [traders, setTraders] = useState<TopTrader[]>([]);
  // Full streamed dataset — used as a client-side fallback for search/category
  // filtering while the server cache is cold (during streaming, or after a
  // restart that wiped the cache).
  const [streamedAll, setStreamedAll] = useState<TopTrader[]>([]);
  const [totalTraders, setTotalTraders] = useState(0);
  const [loading, setLoading] = useState(true);
  // Default sort is SCORE, which is the win-rate preset out of the box
  // (DEFAULT_FORMULA) and pages server-side by the preset's own sort key.
  // P&L ranked the whale who risked $2M to make 3% above the trader
  // compounding a real edge on $5k — the wrong end of a leaderboard you
  // copy PROPORTIONALLY from.
  const [traderSort, setTraderSort] = useState<TraderSort>("score");
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const [page, setPage] = useState(0);
  const [cacheWarm, setCacheWarm] = useState(false);

  const {
    daysAgo, setDaysAgo,
    category: ctxCategory, setCategory,
    marketQuery: ctxMarketQuery, setMarketQuery,
    minTrades, setMinTrades,
    minPerDay, setMinPerDay,
    minVolume, setMinVolume, minBuyVolume, setMinBuyVolume,
    minSellVolume, setMinSellVolume,
    minPnl, setMinPnl,
    reload,
  } = useFilters();

  const [showFilters, setShowFilters] = useState(false);
  const [formula, setFormula] = useState<string>(DEFAULT_FORMULA);
  useEffect(() => { setFormula(loadSavedFormula()); }, []);
  useEffect(() => { saveFormula(formula); }, [formula]);

  const compiled = useMemo(() => compileFormula(formula), [formula]);
  // When the SCORE column pages server-side, a preset formula pages by its
  // own metric (preset keys ARE the server sort keys); a hand-written formula
  // falls back to sharpe pool order and is re-ranked client-side when warm.
  const serverScoreSort = matchScorePreset(formula)?.key ?? "sharpe";
  const scoreFor = useCallback(
    (t: TopTrader): number =>
      compiled.fn ? compiled.fn(scoreInputs(t)) : Number.NEGATIVE_INFINITY,
    [compiled],
  );

  // Header keyword search — a draft of the shared `marketQuery` filter, pushed
  // to context after a short debounce so each keystroke doesn't refire the
  // paged fetch. The FILTERS-panel MARKET QUERY input binds to the same
  // context value, so the two stay in sync via the adopt-external effect.
  const [kwDraft, setKwDraft] = useState("");
  const kwPushedRef = useRef("");
  useEffect(() => {
    // External change (URL seed, FILTERS panel input, RESET ALL) → adopt it.
    if (ctxMarketQuery !== kwPushedRef.current) {
      setKwDraft(ctxMarketQuery);
      kwPushedRef.current = ctxMarketQuery;
    }
  }, [ctxMarketQuery]);
  useEffect(() => {
    if (kwDraft === ctxMarketQuery) return;
    const t = setTimeout(() => {
      kwPushedRef.current = kwDraft;
      setMarketQuery(kwDraft);
    }, 350);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kwDraft]);

  const [progress, setProgress] = useState<ActiveTradersProgress | null>(null);
  const [source, setSource] = useState<"memory" | "disk" | "fresh" | null>(null);

  const [refreshing, setRefreshing] = useState(false);
  const [hasLoaded, setHasLoaded] = useState(false);
  // Track when the trader payload last landed so we can show a "5s ago"
  // indicator and auto-refresh in the background past MAX_STALENESS_MS.
  const [lastUpdated, setLastUpdated] = useState<number | null>(null);
  // Wall-clock ms of when the SERVER last refreshed the payload from Polymarket.
  // Distinct from `lastUpdated` (client fetch time) so the user sees real source
  // age — a 5h-old cache hit doesn't look like a fresh "5s ago".
  const [syncedAt, setSyncedAt] = useState<number | null>(null);
  const [nowTick, setNowTick] = useState(Date.now()); // for "Xs ago" rerender
  const [stratFilter, setStratFilter] = useState(false);
  // MIN TRADES 24H — drops traders below this 24h activity floor. Defaults
  // to "1" so the leaderboard hides dormants by default (matches the spirit
  // of "don't pick inactive traders"). Lives outside FiltersContext because
  // it's an activity floor, not a scoring window.
  const [minTrades24h, setMinTrades24h] = useState("1");
  // LAST TRADE ≤ HRS — drops traders whose most recent trade is older than
  // this many hours. Defaults to DEFAULT_ACTIVE_HOURS: the board you land on
  // is traders who are trading NOW, because that is the only kind you can
  // actually copy. Blank/0 disables it. Both floors go to the server with the
  // page request, so they narrow the WHOLE cached board (and its row count),
  // not the 50 rows that already came back.
  const [maxLastTradeHrs, setMaxLastTradeHrs] = useState(String(DEFAULT_ACTIVE_HOURS));
  // How many traders the two floors above removed, straight from the server.
  // An empty board means something different depending on it.
  const [activityDropped, setActivityDropped] = useState(0);
  const [stratAddrs, setStratAddrs] = useState<Set<string>>(new Set());
  const [stratName, setStratName] = useState<string | null>(null);
  const inFlightRef = useRef(false);
  // AbortController for the in-flight stream. A new SYNC click aborts
  // the prior one and starts fresh — without this, an HMR-dropped or
  // network-stalled stream leaves inFlightRef stuck true and every
  // subsequent click silently no-ops ("sync doesn't work, button does
  // nothing"). Also wall-clock guards in case abort doesn't trip.
  const streamAbortRef = useRef<AbortController | null>(null);
  const streamStartedAtRef = useRef(0);
  const pageRef = useRef(0);

  // Rolling samples of (timestamp, done) for the active sync. Used to derive
  // a smoothed enrich rate and ETA — bare `done/total` doesn't tell the user
  // whether the run is healthy (>10/s) or rate-limited (<2/s). Cleared when
  // a new sync starts (setProgress(null)).
  const rateSamplesRef = useRef<Array<{ ts: number; done: number; phase: string }>>([]);
  const [rateInfo, setRateInfo] = useState<{ rate: number; etaSec: number; phase: string } | null>(null);

  // Server-side paginated fetch — used when cache is warm
  const loadPage = useCallback(
    async (opts: {
      pg?: number; sort?: string; order?: string; silent?: boolean; force?: boolean;
    } = {}) => {
      const pg = opts.pg ?? pageRef.current;
      const sortKey = opts.sort || (traderSort === "score" ? serverScoreSort : traderSort);
      const orderKey = opts.order || sortDir;
      if (!opts.silent) setRefreshing(true);
      try {
        const result = await fetchTradersPage({
          days,
          minPerDay: minTradesPerDay,
          pool: 2000,
          sort: sortKey,
          order: orderKey,
          page: pg,
          pageSize: PAGE_SIZE,
          search: search || undefined,
          category: category || undefined,
          marketQuery: marketQuery || undefined,
          minVolume: Number(minVolume) || undefined,
          minPnl: minPnl !== "" ? Number(minPnl) : undefined,
          minTrades: Number(minTrades) || undefined,
          minBuyVolume: Number(minBuyVolume) || undefined,
          minSellVolume: Number(minSellVolume) || undefined,
          // Activity floors run server-side against the same cached aggregate
          // the page comes from — so `total` counts the traders you can
          // actually see, and the board stays a cache read.
          minTrades24h: Number(minTrades24h) || undefined,
          maxLastTradeHrs: Number(maxLastTradeHrs) || undefined,
          force: opts.force,
        });
        if (result.cold) {
          // Cache is cold — fall back to streaming
          return false;
        }
        setTraders(result.traders);
        setTotalTraders(result.total);
        setActivityDropped(result.activityDropped ?? 0);
        setSource(result.source as "memory" | "disk" | "fresh");
        setCacheWarm(true);
        setHasLoaded(true);
        setLoading(false);
        setLastUpdated(Date.now());
        if (typeof result.syncedAt === "number" && result.syncedAt > 0) {
          setSyncedAt(result.syncedAt * 1000);
        }
        return true;
      } catch {
        return false;
      } finally {
        setRefreshing(false);
      }
    },
    [days, minTradesPerDay, traderSort, serverScoreSort, sortDir, search, category, marketQuery,
     minVolume, minPnl, minTrades, minBuyVolume, minSellVolume,
     minTrades24h, maxLastTradeHrs],
  );

  // Streaming load — used for cold cache (pipeline needs to run) AND
  // for the manual SYNC button (force=true bypasses every cache layer).
  const loadStream = useCallback(
    async (opts: { force?: boolean } = {}) => {
      // If a stream is already in flight, abort it before starting a new
      // one. The old request was either stalled by HMR, dropped by the
      // network, or just slow — either way, a fresh user-triggered SYNC
      // should always win over a stuck one.
      if (inFlightRef.current && streamAbortRef.current) {
        streamAbortRef.current.abort();
      }
      const controller = new AbortController();
      streamAbortRef.current = controller;
      streamStartedAtRef.current = Date.now();
      inFlightRef.current = true;
      setLoading(true);
      setRefreshing(true);
      setProgress(null);
      setRateInfo(null);
      rateSamplesRef.current = [];
      setSource(null);
      // The streamed path filters client-side, so the server's drop count no
      // longer describes what's on screen — clear it rather than explain an
      // empty board with a number from the last paged read.
      setActivityDropped(0);
      try {
        const { traders: data, source: src, syncedAt: streamSyncedAt } = await fetchTopTradersStream(
          2000,
          { daysWindow: days, minTradesPerDay, force: opts.force, signal: controller.signal },
          (p) => {
            setProgress(p);
            // Trust the `kept` field from progress — partials are bandwidth-capped
            // (top 500 by PnL), but `kept` reflects the true running total.
            if (p.phase === "enrich" && typeof p.kept === "number" && p.kept > 0) {
              setTotalTraders((prev) => Math.max(prev, p.kept));
            }
            // Sample for rate/ETA. Reset the window when phase changes (leaderboard
            // → enrich) since the two phases run at very different rates.
            const samples = rateSamplesRef.current;
            if (samples.length > 0 && samples[samples.length - 1].phase !== p.phase) {
              rateSamplesRef.current = [];
            }
            const now = Date.now();
            rateSamplesRef.current.push({ ts: now, done: p.done, phase: p.phase });
            // Keep ~last 30s of samples — long enough to smooth bursts, short
            // enough that ETA reflects current throughput (not the slow warm-up).
            const cutoff = now - 30_000;
            while (rateSamplesRef.current.length > 2 && rateSamplesRef.current[0].ts < cutoff) {
              rateSamplesRef.current.shift();
            }
            const ss = rateSamplesRef.current;
            if (ss.length >= 2 && p.total > 0) {
              const first = ss[0];
              const last = ss[ss.length - 1];
              const dt = (last.ts - first.ts) / 1000;
              const dDone = last.done - first.done;
              if (dt > 0.5 && dDone > 0) {
                const rate = dDone / dt;
                const remaining = Math.max(0, p.total - p.done);
                const etaSec = rate > 0 ? remaining / rate : 0;
                setRateInfo({ rate, etaSec, phase: p.phase });
              }
            }
          },
          (partial) => {
            // Keep the full partial for client-side filtering, and show
            // first page in the table. Don't shrink visible total to the
            // partial length — progress.kept is more authoritative.
            setStreamedAll(partial);
            setTraders(partial.slice(0, PAGE_SIZE));
            setTotalTraders((prev) => Math.max(prev, partial.length));
            setLoading(false);
          },
        );
        // After stream completes, cache is warm — switch to server-side pagination
        setCacheWarm(true);
        setSource(src);
        setStreamedAll(data);
        // Show all results (first page) — totalTraders reflects full dataset
        setTraders(data.slice(0, PAGE_SIZE));
        setTotalTraders(data.length);
        setLastUpdated(Date.now());
        if (streamSyncedAt > 0) setSyncedAt(streamSyncedAt * 1000);
      } catch (e) {
        // AbortError = a newer SYNC click replaced this one; leave the
        // previous traders visible (the new request will repopulate).
        // Any other failure clears the table so the user knows.
        const aborted = e instanceof Error && e.name === "AbortError";
        if (!aborted) setTraders([]);
      } finally {
        // Only reset inFlight if THIS controller is still the current
        // one — a re-click during this finally would have replaced it
        // and we don't want to clobber its in-flight state.
        if (streamAbortRef.current === controller) {
          setLoading(false);
          setRefreshing(false);
          setHasLoaded(true);
          inFlightRef.current = false;
          streamAbortRef.current = null;
        }
      }
    },
    [days, minTradesPerDay],
  );

  // Safety net: if the in-flight stream has been running for >5 minutes,
  // assume it's stalled (HMR dropped the body without throwing, network
  // proxy is buffering, etc.) and clear the inFlight lock so the user's
  // next SYNC click can fire. The Rust pipeline is bounded at <2min for
  // pool=2000, so 5min is a generous ceiling.
  useEffect(() => {
    const t = setInterval(() => {
      if (!inFlightRef.current) return;
      const age = Date.now() - streamStartedAtRef.current;
      if (age > 5 * 60_000) {
        if (streamAbortRef.current) streamAbortRef.current.abort();
        streamAbortRef.current = null;
        inFlightRef.current = false;
        setRefreshing(false);
        setLoading(false);
      }
    }, 30_000);
    return () => clearInterval(t);
  }, []);

  // Keep refs to latest fetch functions so effects don't go stale
  const loadPageRef = useRef(loadPage);
  const loadStreamRef = useRef(loadStream);
  useEffect(() => { loadPageRef.current = loadPage; }, [loadPage]);
  useEffect(() => { loadStreamRef.current = loadStream; }, [loadStream]);

  // Initial load: try paged first, fall back to streaming
  // Fires when the pipeline parameters change (days window, min trades/day)
  useEffect(() => {
    setTraders([]);
    setTotalTraders(0);
    setSource(null);
    setProgress(null);
    setRateInfo(null);
    rateSamplesRef.current = [];
    setCacheWarm(false);
    setPage(0);
    pageRef.current = 0;
    (async () => {
      const warm = await loadPageRef.current({ pg: 0 });
      if (!warm) await loadStreamRef.current();
    })();
  }, [days, minTradesPerDay, reloadKey]);

  // Re-fetch page when sort/filter/page changes (server-side). If the
  // server cache went cold (e.g. API restarted) we transparently fall back
  // to streaming so the next filter change has data to work against.
  useEffect(() => {
    if (!cacheWarm) return;
    pageRef.current = page;
    (async () => {
      const ok = await loadPageRef.current({ pg: page });
      if (!ok) {
        setCacheWarm(false);
        await loadStreamRef.current();
      }
    })();
  }, [cacheWarm, page, traderSort, serverScoreSort, sortDir, search, category, marketQuery,
      minVolume, minPnl, minTrades, minBuyVolume, minSellVolume,
      minTrades24h, maxLastTradeHrs]);

  // Background staleness check. Re-fetches current page silently once data
  // crosses MAX_STALENESS_MS so the leaderboard never gets older than this
  // without the user feeling a load. STALENESS_TICK_MS is just the polling
  // cadence; the actual fetch only fires when lastUpdated is past the budget.
  useEffect(() => {
    if (!cacheWarm) return;
    const MAX_STALENESS_MS = 60_000;
    const STALENESS_TICK_MS = 10_000;
    const t = setInterval(() => {
      const age = lastUpdated ? Date.now() - lastUpdated : Infinity;
      if (age >= MAX_STALENESS_MS) {
        void loadPage({ pg: pageRef.current, silent: true });
      }
    }, STALENESS_TICK_MS);
    return () => clearInterval(t);
  }, [loadPage, cacheWarm, lastUpdated]);

  // Background source-data refresh. The page-cache check above keeps the
  // CLIENT view fresh, but the chip the user sees ("sync 18h 31m ago") is
  // server `syncedAt` — when Polymarket data was actually pulled. Once that
  // crosses the SERVER's sync cadence (sync.rs; hourly by default, owner-set)
  // we kick off `loadStream({ force: true })` from the browser as a safety
  // net — the schedule is the contract, so a tab left open must not re-pull
  // on its own faster than the owner asked for. Floor of 10min keeps the old
  // behavior for deployments that pause auto-sync entirely. Guarded by
  // inFlightRef and a per-attempt cooldown so a long-running force-sync (the
  // pipeline takes 2–5 minutes on a cold scan) doesn't queue up.
  const [serverIntervalMs, setServerIntervalMs] = useState<number | null>(null);
  const sourceStalenessMs = Math.max(10 * 60_000, serverIntervalMs ?? 0);
  useEffect(() => {
    const read = () =>
      fetchSyncSchedule()
        .then((s) => setServerIntervalMs(s.enabled ? s.intervalSecs * 1000 : null))
        .catch(() => setServerIntervalMs(null));
    void read();
    const t = setInterval(read, 5 * 60_000);
    return () => clearInterval(t);
  }, []);
  const lastForceAtRef = useRef(0);
  // Flagged true when the most recent loadStream was kicked off by the
  // auto-refresh path (vs the user clicking ↻ SYNC or initial cold start).
  // Drives the SYNCING IN PROGRESS banner so we only surface it for the
  // background auto-trigger — the user pressing ↻ SYNC already sees the
  // SYNCING badge animate, no banner needed for that case.
  const [autoSyncActive, setAutoSyncActive] = useState(false);
  useEffect(() => {
    const MAX_SOURCE_STALENESS_MS = sourceStalenessMs;
    const MIN_RETRY_MS = 2 * 60_000;
    const TICK_MS = 30_000;
    const t = setInterval(() => {
      if (inFlightRef.current) return;
      const stamp = syncedAt ?? lastUpdated;
      if (!stamp) return;
      const age = Date.now() - stamp;
      if (age < MAX_SOURCE_STALENESS_MS) return;
      if (Date.now() - lastForceAtRef.current < MIN_RETRY_MS) return;
      lastForceAtRef.current = Date.now();
      setAutoSyncActive(true);
      void loadStreamRef.current({ force: true }).finally(() => {
        setAutoSyncActive(false);
      });
    }, TICK_MS);
    return () => clearInterval(t);
  }, [syncedAt, lastUpdated, sourceStalenessMs]);

  // 5-second tick so the "Xs ago" label re-renders even when nothing else changes.
  useEffect(() => {
    const t = setInterval(() => setNowTick(Date.now()), 5_000);
    return () => clearInterval(t);
  }, []);

  const handleSort = (col: TraderSort) => {
    if (traderSort === col) setSortDir((d) => (d === "desc" ? "asc" : "desc"));
    else { setTraderSort(col); setSortDir("desc"); }
  };

  const filterQs = useFilterParams({ excludeSearch: true });
  const goToTrader = (addr: string) => {
    router.push(`/traders/${addr}${filterQs ? `?${filterQs}` : ""}`);
  };

  // When the server cache is warm, `traders` already holds the filtered
  // current page — no extra work needed (score sort is the one exception
  // since the formula is client-only).
  //
  // When the cache is cold (initial load, during streaming, or after the
  // API restarted) we filter+sort+paginate the streamed dataset on the
  // client so the FILTERS panel produces an immediate visible effect
  // instead of silently no-op'ing until the pipeline finishes.
  // When stratFilter is on and streamedAll is empty (warm-cache path skipped
  // streaming), fetch the full dataset so client-side filtering can show all
  // strategy traders instead of just whoever is on the current page.
  useEffect(() => {
    if (!stratFilter || streamedAll.length > 0) return;
    (async () => {
      try {
        const result = await fetchTradersPage({
          days,
          minPerDay: minTradesPerDay,
          pool: 2000,
          sort: "pnl",
          order: "desc",
          page: 0,
          pageSize: 5000,
        });
        if (!result.cold && result.traders.length > 0) {
          setStreamedAll(result.traders);
          return;
        }
      } catch {}
      // Cold cache (or empty paged result) — fall back to the streaming load
      // so strat-filtered addresses have a dataset to filter against.
      loadStreamRef.current();
    })();
  }, [stratFilter, streamedAll.length, days, minTradesPerDay]);

  const clientView = useMemo(() => {
    // Use client-side filtering when cache is cold OR when stratFilter needs
    // the full dataset (server pagination can't filter by address list).
    // The activity floors used to ask for this path too, back when only the
    // client knew about them — but on a warm cache there is no streamed
    // dataset to filter, so the request fell through to a per-page filter and
    // the two paths disagreed: board-wide after a cold sync, page-only
    // otherwise. They're server parameters now, applied to the same cached
    // aggregate the page comes from; this path only mirrors them while the
    // stream is the only data there is.
    if (cacheWarm && !stratFilter) return null;
    if (!stratFilter && streamedAll.length === 0) return null;

    // When STRAT is on, anchor the list to the strat's address set so traders
    // that didn't make the leaderboard pool still show up (with placeholder
    // zeros). Otherwise filter from the full leaderboard.
    let baseList: TopTrader[];
    if (stratFilter && stratAddrs.size > 0) {
      const byAddr = new Map<string, TopTrader>();
      for (const t of streamedAll) byAddr.set(t.address.toLowerCase(), t);
      baseList = Array.from(stratAddrs).map((addr) =>
        byAddr.get(addr) ?? {
          address: addr,
          volume: 0, buyVolume: 0, sellVolume: 0,
          pnl: 0, winRate: 0, sharpe: 0, exitEntry: -1, positions: 0,
          marketTitles: [], recentTrades: 0,
        },
      );
    } else {
      baseList = streamedAll;
    }

    const cat = category || "";
    let list = baseList.filter((t) => {
      if (search && !matchTraderSearch(t, search)) return false;
      if (cat && !matchTraderCategory(t.marketTitles, cat)) return false;
      // Market-topic filter — keep only traders active in markets matching the
      // free-text query (mirrors the server-side recompute path).
      if (marketQuery && !traderMatchesMarketQuery(t.marketTitles, marketQuery)) return false;
      const mv = Number(minVolume);
      if (Number.isFinite(mv) && mv > 0 && t.volume < mv) return false;
      const mp = Number(minPnl);
      if (minPnl !== "" && Number.isFinite(mp) && t.pnl < mp) return false;
      const mt = Number(minTrades);
      if (minTrades !== "" && Number.isFinite(mt) && t.recentTrades < mt) return false;
      // Activity floor — drop traders below the 24h trade threshold so
      // dormants don't surface even when their week-long stats look great.
      const m24 = Number(minTrades24h);
      if (minTrades24h !== "" && Number.isFinite(m24) && (t.trades24h ?? 0) < m24) return false;
      // Recency floor — drop traders whose last trade is older than N hours.
      // Missing lastTradeTs = unknown/dormant → hidden while the filter is on.
      const mlh = Number(maxLastTradeHrs);
      if (maxLastTradeHrs !== "" && Number.isFinite(mlh) && mlh > 0) {
        if (!t.lastTradeTs || Date.now() / 1000 - t.lastTradeTs > mlh * 3600) return false;
      }
      const mbv = Number(minBuyVolume);
      if (minBuyVolume !== "" && Number.isFinite(mbv) && t.buyVolume < mbv) return false;
      const msv = Number(minSellVolume);
      if (minSellVolume !== "" && Number.isFinite(msv) && t.sellVolume < msv) return false;
      return true;
    });
    // Sort. When a category is selected, vibe-first (more in-category titles
    // win), then by the primary metric — mirrors the server-side ordering.
    const dir = sortDir === "desc" ? -1 : 1;
    const inCat = (titles: string[]) => cat ? titles.filter((m) => matchTraderCategory([m], cat)).length : 0;
    list = [...list].sort((a, b) => {
      // Topic-query match count wins first when a market query is active, so
      // traders heaviest in the queried topic surface at the top.
      if (marketQuery) {
        const d = marketQueryMatchCount(b.marketTitles, marketQuery) - marketQueryMatchCount(a.marketTitles, marketQuery);
        if (d !== 0) return d;
      }
      if (cat) {
        const d = inCat(b.marketTitles) - inCat(a.marketTitles);
        if (d !== 0) return d;
      }
      if (traderSort === "score") return dir * (scoreFor(a) - scoreFor(b));
      if (traderSort === "volume") return dir * (a.volume - b.volume);
      if (traderSort === "positions") return dir * (a.recentTrades - b.recentTrades);
      // Missing lastTradeTs → 0 so unknown-recency traders sink on desc.
      if (traderSort === "last") return dir * ((a.lastTradeTs ?? 0) - (b.lastTradeTs ?? 0));
      return dir * (a.pnl - b.pnl); // default: pnl
    });
    return list;
  }, [cacheWarm, streamedAll, search, category, marketQuery, minVolume, minPnl,
      minTrades, minTrades24h, maxLastTradeHrs, minBuyVolume, minSellVolume, sortDir, traderSort, scoreFor,
      stratFilter, stratAddrs]);

  const sortedTraders = useMemo(() => {
    if (clientView) {
      const start = page * PAGE_SIZE;
      return clientView.slice(start, start + PAGE_SIZE);
    }
    if (traderSort === "score" && cacheWarm) {
      return [...traders].sort((a, b) => {
        const cmp = scoreFor(a) - scoreFor(b);
        return sortDir === "desc" ? -cmp : cmp;
      });
    }
    return traders;
  }, [clientView, page, traders, traderSort, sortDir, scoreFor, cacheWarm]);

  // When showing client-side filtered results, total reflects the filtered
  // size, not the streamed length.
  const visibleTotal = clientView ? clientView.length : totalTraders;

  // Reset page on filter/sort change
  useEffect(() => { setPage(0); }, [search, category, marketQuery, traderSort, sortDir,
    minVolume, minPnl, minTrades, minBuyVolume, minSellVolume, stratFilter,
    minTrades24h, maxLastTradeHrs]);

  const totalPages = Math.max(1, Math.ceil(visibleTotal / PAGE_SIZE));
  const safePage = Math.min(page, totalPages - 1);
  const toggleStratFilter = useCallback(() => {
    if (stratFilter) {
      setStratFilter(false);
      setStratAddrs(new Set());
      setStratName(null);
    } else {
      const indexes = loadIndexes();
      const activeId = getActiveIndexId();
      const active = activeId ? indexes.find(i => i.id === activeId) : indexes[0];
      if (active && active.traders.length > 0) {
        setStratAddrs(new Set(active.traders.map(t => t.address.toLowerCase())));
        setStratName(active.name);
        setStratFilter(true);
      }
    }
  }, [stratFilter]);

  // Keep stratAddrs in sync with the active strat as it mutates (add/remove
  // from this page, the sidebar, or another tab). Without this, adding a
  // trader while STRAT filter is on would leave the new address out of the
  // filtered view.
  useEffect(() => {
    if (!stratFilter) return;
    const refresh = () => {
      const indexes = loadIndexes();
      const activeId = getActiveIndexId();
      const active = activeId ? indexes.find((i) => i.id === activeId) : indexes[0];
      if (active) {
        setStratAddrs(new Set(active.traders.map((t) => t.address.toLowerCase())));
        setStratName(active.name);
      }
    };
    refresh();
    window.addEventListener("strat-updated", refresh);
    return () => window.removeEventListener("strat-updated", refresh);
  }, [stratFilter]);

  const pageTraders = useMemo(() => {
    // The activity floors are already applied — server-side for the warm-paged
    // path, in `clientView` for the streamed one — so this only narrows to the
    // strat's address set. Re-filtering the page here is what made a 50-row
    // page render as five rows under a pager still counting the full board.
    const list = sortedTraders;
    if (!stratFilter || stratAddrs.size === 0) return list;
    return list.filter(t => stratAddrs.has(t.address.toLowerCase()));
  }, [sortedTraders, stratFilter, stratAddrs]);

  // Pre-lowercase the selected set so the ✓ / + ADD toggle compares
  // case-insensitively — different code paths persist addresses in mixed cases.
  const selectedLower = useMemo(
    () => new Set(selectedAddresses.map((a) => a.toLowerCase())),
    [selectedAddresses],
  );

  // The ranking column names what it actually holds: on any preset (win rate
  // out of the box) that IS the metric, and "SCORE" hid the one number the
  // board is sorted by. Any edit to the formula turns it back into a generic
  // SCORE.
  const scorePreset = matchScorePreset(formula);
  const columns: { key: TraderSort; label: string; hint: string }[] = [
    {
      key: "score",
      label: scorePreset ? scorePreset.label : "SCORE",
      hint: scorePreset ? scorePreset.hint : `Custom score = ${formula}`,
    },
    { key: "pnl", label: "P&L", hint: "Realized profit over the window — a size story, not a skill one" },
    { key: "volume", label: "VOL", hint: "USDC traded in the window" },
    { key: "positions", label: "TRADES", hint: "Positions taken in the window" },
  ];

  // Filter input helpers
  const onInt = (set: (v: string) => void, max: number) => (e: React.ChangeEvent<HTMLInputElement>) => {
    const v = e.target.value;
    if (v === "") return set("");
    if (!/^\d+$/.test(v)) return;
    const n = Number(v);
    if (n > max) return set(String(max));
    set(String(n));
  };
  const onDec = (set: (v: string) => void) => (e: React.ChangeEvent<HTMLInputElement>) => {
    const v = e.target.value;
    if (v === "") return set("");
    if (!/^-?\d*\.?\d*$/.test(v)) return;
    set(v);
  };
  const onEnter = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") (e.target as HTMLInputElement).blur();
  };

  // Count active advanced filters
  const activeFilterCount = [
    minTrades, minBuyVolume, minSellVolume, minPnl,
  ].filter((v) => v !== "").length
    + (minVolume !== "100" && minVolume !== "" ? 1 : 0)
    + (minPerDay !== "0" && minPerDay !== "" ? 1 : 0)
    + (ctxCategory ? 1 : 0)
    + (ctxMarketQuery ? 1 : 0)
    + (formula !== DEFAULT_FORMULA ? 1 : 0);

  // Stats summary (computed from current page — approximate when paginated)
  const pagePnl = traders.reduce((s, t) => s + t.pnl, 0);
  const pageVol = traders.reduce((s, t) => s + t.volume, 0);

  // Staleness signal lives on the header's color-coded "sync {age}" chip
  // (green <5min → amber <30min → red) plus the always-present ↻ SYNC button,
  // so we don't duplicate it with a separate STALE DATA stripe. The only
  // full-width banner we keep is the transient AUTO-SYNCING notice, which
  // explains why a fresh pull kicked off after the data went stale.
  const staleStamp = syncedAt ?? lastUpdated;
  const staleAgeMs = staleStamp ? nowTick - staleStamp : 0;
  const isStale = staleStamp != null && staleAgeMs >= sourceStalenessMs;
  const showSyncingBanner = isStale && (autoSyncActive || loading || refreshing);

  // Why the board is empty. Under the default 6h lens the answer is almost
  // never "no good traders in this window" — it's "everyone good has been
  // quiet", or, when the snapshot itself is older than the window, "this data
  // predates the question", which no amount of relaxing the other filters
  // fixes. Say which, and name the knob.
  const recencyHrs = Number(maxLastTradeHrs);
  const snapshotOlderThanWindow =
    Number.isFinite(recencyHrs) && recencyHrs > 0 && staleAgeMs > recencyHrs * 3600_000;

  return (
    <div className="space-y-3">
      {showSyncingBanner && (
        <div className="pixel-panel px-4 py-1.5 border border-green-400/60 bg-green-400/5 text-green-400 flex items-center gap-2 font-mono text-[12px]">
          <span className="w-1.5 h-1.5 bg-green-400 animate-pulse shrink-0" />
          <span className="tracking-wider shrink-0">AUTO-SYNCING</span>
          <span className="text-pixel-gray-light shrink-0">leaderboard was {formatAgo(staleAgeMs)} stale</span>
        </div>
      )}
      {/* ── Single-line header ── */}
      <div className="pixel-panel px-4 py-2.5">
        <div className="flex items-center gap-3 flex-wrap">
          {/* Title + days + count */}
          <span className="text-[15px] text-pixel-white tracking-wider shrink-0">TOP TRADERS</span>

          <div className="flex items-center gap-1.5 shrink-0">
            <span className="text-[13px] text-pixel-gray tracking-wider">DAYS</span>
            <input type="text" inputMode="numeric" value={daysAgo} onChange={onInt(setDaysAgo, 365)}
              onKeyDown={onEnter} placeholder="7"
              className="pixel-input-sm w-10 text-center font-mono text-[13px]" />
          </div>

          {/* Keyword filter — always visible (the FILTERS panel has the same
              field, but topic filtering is the leaderboard's primary lens so
              it shouldn't hide behind a toggle). Matching traders keep only
              markets that hit the query, and P&L/VOL/TRADES are recomputed
              from just those markets server-side. */}
          <div className="relative flex-1 min-w-[160px] max-w-[340px]">
            <input
              type="text"
              value={kwDraft}
              onChange={(e) => setKwDraft(e.target.value)}
              onKeyDown={onEnter}
              placeholder="KEYWORD — e.g. bitcoin, nba"
              title="Filter traders by market keyword — P&L / volume / trades are recomputed from only the matching markets. Comma or | = OR, space = AND (e.g. 'bitcoin, btc')."
              className={`pixel-input-sm w-full font-mono text-[13px] pr-6 ${
                kwDraft ? "!border-green-400/60 !text-green-300" : ""
              }`}
            />
            {kwDraft && (
              <button
                onClick={() => { setKwDraft(""); kwPushedRef.current = ""; setMarketQuery(""); }}
                className="absolute right-1.5 top-1/2 -translate-y-1/2 text-[13px] text-pixel-gray hover:text-pixel-white"
                title="Clear keyword filter"
              >
                x
              </button>
            )}
          </div>
          {ctxMarketQuery && (
            <span
              className="text-[11px] font-mono tracking-wider text-green-400 border border-green-400/40 bg-green-400/5 px-1.5 py-0.5 shrink-0"
              title={`Each trader's P&L, volume and trade counts below are recomputed from only their markets matching "${ctxMarketQuery}" — not their overall totals.`}
            >
              PERF: MATCHING MARKETS ONLY
            </span>
          )}

          {visibleTotal > 0 && !loading && (
            <span className="text-[13px] text-pixel-gray font-mono shrink-0">
              {visibleTotal} traders
            </span>
          )}

          {/* Right side: source + filters */}
          <div className="ml-auto flex items-center gap-2 shrink-0">
            {source && (
              <span
                title={source === "fresh" ? "Pulled from Polymarket just now" : "Served from the server's cache — press ↻ SYNC for a fresh pull"}
                className={`text-[12px] font-mono tracking-wider px-1.5 py-0.5 border ${
                source === "fresh" ? "border-yellow-500/40 text-yellow-400" : "border-pixel-border text-pixel-gray"
              }`}>
                {source === "fresh" ? "FRESH" : "CACHED"}
              </span>
            )}
            {/* Manual SYNC button — bypasses the 60s cache by routing
                through the streaming path so the user sees enrichment
                progress (`enriching 1240/2000`) instead of an opaque
                SYNCING… for the 2-5 min a fresh aggregation takes.
                loadStream is the same call used on cold-cache cold-start;
                we just trigger it explicitly here. */}
            <button
              onClick={() => { void loadStream({ force: true }); }}
              disabled={refreshing || loading}
              className="pixel-btn text-[11px] px-2 py-0.5 border-green-400/60 text-green-400 hover:bg-green-400/10 disabled:opacity-40 disabled:cursor-not-allowed"
              title={
                (refreshing || loading) && rateInfo
                  ? `Enrich rate ${rateInfo.rate.toFixed(1)}/s · ETA ${formatEta(rateInfo.etaSec)} · phase ${rateInfo.phase}`
                  : "Force a fresh pull from Polymarket — bypasses the cache and streams progress"
              }
            >
              {(refreshing || loading) && progress ? (
                <>
                  {progress.phase === "enrich"
                    ? `SYNCING ${progress.done}/${progress.total}`
                    : `SYNCING ${progress.done}/${progress.total} (leaderboard)`}
                  {rateInfo && rateInfo.phase === progress.phase && (
                    <span className="text-pixel-gray-light ml-1">
                      · {rateInfo.rate.toFixed(1)}/s · ETA {formatEta(rateInfo.etaSec)}
                    </span>
                  )}
                </>
              ) : refreshing || loading
                ? "SYNCING…"
                : "↻ SYNC"}
            </button>
            {(syncedAt ?? lastUpdated) && (() => {
              // Prefer the server's syncedAt — it tells the user when the data
              // was last actually pulled from Polymarket, not when the client
              // hit the cache. Falls back to lastUpdated if the server didn't
              // expose one (old binary).
              const stamp = syncedAt ?? lastUpdated!;
              const age = nowTick - stamp;
              const color =
                age < 5 * 60_000 ? "text-green-400" :
                age < 30 * 60_000 ? "text-amber-400" :
                "text-red-400";
              const tooltip = syncedAt
                ? `Source data last synced ${new Date(stamp).toLocaleTimeString()} (Polymarket data-api)`
                : `Client cache fetched ${new Date(stamp).toLocaleTimeString()} — server didn't expose source sync time`;
              return (
                <span
                  className={`text-[11px] font-mono tracking-wider ${color}`}
                  title={tooltip}
                >
                  sync {formatAgo(age)}
                </span>
              );
            })()}
            {refreshing && <span className="text-[12px] text-green-400 animate-pulse">&#9679;</span>}

            {/* Server-side schedule behind that "sync {age}" number — the API
                re-warms the leaderboards hourly by default whether or not this
                console is open. The chip lets the owner change the cadence,
                pause it, or force a background run. */}
            <SyncScheduleChip />

            {stratFilter && <button
              onClick={toggleStratFilter}
              className={`pixel-btn text-[13px] px-2 py-0.5 shrink-0 flex items-center gap-1.5 transition-colors ${
                stratFilter
                  ? "border-green-400 text-green-400 bg-green-400/10"
                  : "border-pixel-border text-pixel-gray hover:text-pixel-white hover:border-pixel-white"
              }`}
              title={stratFilter ? `Showing traders in ${stratName}` : "Filter to active strategy traders"}
            >
              STRAT
              {stratFilter && stratName && (
                <span className="text-[12px] bg-green-500/20 text-green-400 px-1 py-px border border-green-500/40 max-w-[60px] truncate">
                  {stratName}
                </span>
              )}
            </button>}

            <button
              onClick={() => setShowFilters((v) => !v)}
              className={`pixel-btn text-[13px] px-2 py-0.5 shrink-0 flex items-center gap-1.5 transition-colors ${
                showFilters
                  ? "border-pixel-white text-pixel-white"
                  : activeFilterCount > 0
                  ? "border-green-500/60 text-green-400"
                  : "border-pixel-border text-pixel-gray hover:text-pixel-white hover:border-pixel-white"
              }`}
            >
              FILTERS
              {activeFilterCount > 0 && (
                <span className="text-[12px] bg-green-500/20 text-green-400 px-1 py-px border border-green-500/40">
                  {activeFilterCount}
                </span>
              )}
            </button>
          </div>
        </div>

        {/* ── Expandable advanced filters ── */}
        {showFilters && (
          <div className="border-t-2 border-pixel-border mt-2.5 pt-3 space-y-3">
            {/* Semantic market category — filters traders (and their trades on
                the profile page) by topic keywords. e.g. "CRYPTO" keeps only
                BTC/ETH/SOL markets. */}
            <div className="flex items-center gap-1.5 flex-wrap">
              <span className="text-[12px] text-pixel-gray tracking-wider shrink-0 mr-1">MARKET</span>
              {CATEGORIES.map((c) => {
                const active = ctxCategory === c.slug;
                return (
                  <button
                    key={c.slug || "all"}
                    onClick={() => setCategory(c.slug)}
                    className={`pixel-btn text-[12px] px-2 py-0.5 transition-colors ${
                      active
                        ? "border-green-400 text-green-400 bg-green-400/10"
                        : "border-pixel-border text-pixel-gray hover:text-pixel-white hover:border-pixel-white"
                    }`}
                  >
                    {c.label}
                  </button>
                );
              })}
            </div>

            {/* Free-text market-topic filter — finer than the category chips.
                e.g. "bitcoin" or "price of bitcoin" surfaces the best traders
                for that theme, with their stats recomputed from only matching
                markets. Binds to FiltersContext so it URL-syncs + persists. */}
            <div className="flex items-center gap-1.5">
              <span className="text-[12px] text-pixel-gray tracking-wider shrink-0 mr-1">MARKET QUERY</span>
              <input
                type="text"
                value={ctxMarketQuery}
                onChange={(e) => setMarketQuery(e.target.value)}
                onKeyDown={onEnter}
                placeholder="e.g. bitcoin, price of bitcoin — any market topic"
                title="Keep only traders active in markets matching this query; their P&L/volume are recomputed from just those markets."
                className="pixel-input-sm flex-1 min-w-[160px] font-mono"
              />
              {ctxMarketQuery && (
                <button
                  onClick={() => setMarketQuery("")}
                  className="pixel-btn text-[12px] px-2 py-1 border-pixel-border text-pixel-gray hover:text-pixel-white shrink-0"
                  title="Clear market query"
                >
                  CLR
                </button>
              )}
            </div>

            <div className="grid grid-cols-[repeat(auto-fill,minmax(130px,1fr))] gap-x-4 gap-y-2">
              {([
                { label: "MIN VOLUME", value: minVolume, onChange: onDec(setMinVolume), ph: "100" },
                { label: "MIN TRADES", value: minTrades, onChange: onDec(setMinTrades), ph: "any" },
                { label: "MIN TRADES/DAY", value: minPerDay, onChange: onDec(setMinPerDay), ph: "0" },
                // Activity floor — 1 = at least one trade in last 24h, 0 = no
                // filter, blank = no filter. Defaults to 1 so dormants are
                // hidden out of the box (user explicitly asked to not see them).
                { label: "MIN TRADES 24H", value: minTrades24h, onChange: onDec(setMinTrades24h), ph: "1" },
                // Recency floor — hide traders whose last trade is older than
                // this many hours. Defaults to DEFAULT_ACTIVE_HOURS; blank/0
                // disables. Widen it to see the dormant-but-profitable names.
                { label: "LAST TRADE ≤ HRS", value: maxLastTradeHrs, onChange: onDec(setMaxLastTradeHrs), ph: String(DEFAULT_ACTIVE_HOURS) },
                { label: "MIN BUY VOL", value: minBuyVolume, onChange: onDec(setMinBuyVolume), ph: "any" },
                { label: "MIN SELL VOL", value: minSellVolume, onChange: onDec(setMinSellVolume), ph: "any" },
                { label: "MIN P&L", value: minPnl, onChange: onDec(setMinPnl), ph: "any" },
              ] as const).map((f) => (
                <div key={f.label} className="flex flex-col gap-1">
                  <label className="text-[12px] text-pixel-gray tracking-wider">{f.label}</label>
                  <input
                    type="text"
                    inputMode="decimal"
                    value={f.value}
                    onChange={f.onChange}
                    onKeyDown={onEnter}
                    placeholder={f.ph}
                    className="pixel-input-sm w-full font-mono"
                  />
                </div>
              ))}
            </div>

            {/* Score formula — preset chips parameterize the metric, and the
                input shows a preset is just a formula you can keep editing. */}
            <div className="flex items-center gap-2 flex-wrap">
              <label className="text-[12px] text-pixel-gray tracking-wider shrink-0">SCORE =</label>
              <ScoreRatioChips
                formula={formula}
                setFormula={setFormula}
                canSave={!compiled.error}
                btnClass="pixel-btn text-[12px] px-2 py-1 shrink-0"
                idleClass="border-pixel-border text-pixel-gray hover:text-pixel-white"
              />
              <input type="text" value={formula} onChange={(e) => setFormula(e.target.value)} onKeyDown={onEnter} spellCheck={false}
                placeholder={DEFAULT_FORMULA} className="pixel-input-sm flex-1 min-w-[140px] font-mono" />
              <button onClick={() => setFormula(DEFAULT_FORMULA)} title="Back to the default — win rate"
                className="pixel-btn text-[12px] px-2 py-1 border-pixel-border text-pixel-gray hover:text-pixel-white shrink-0">RST</button>
              {compiled.error
                ? <span className="text-[12px] text-red-400 shrink-0 truncate max-w-[160px]">ERR: {compiled.error.slice(0, 30)}</span>
                : <span className="text-[12px] text-green-500 shrink-0">&#10003;</span>}
            </div>

            {/* Reset all */}
            <div className="flex items-center justify-end">
              <button onClick={() => { setDaysAgo(""); setCategory(""); setMarketQuery(""); setMinTrades(""); setMinTrades24h("1"); setMaxLastTradeHrs("24"); setMinPerDay("0"); setMinVolume("100"); setMinBuyVolume(""); setMinSellVolume(""); setMinPnl(""); setFormula(DEFAULT_FORMULA); reload(); }}
                className="pixel-btn text-[12px] px-3 py-1 border-pixel-border text-pixel-gray hover:text-pixel-white hover:border-red-400 hover:text-red-400 transition-colors">
                RESET ALL
              </button>
            </div>
          </div>
        )}
      </div>

      {/* ── Progress ── */}
      {loading && (() => {
        const lbDone = progress?.phase === "leaderboard" ? progress.done : 0;
        const lbTotal = progress?.phase === "leaderboard" ? progress.total : 0;
        const enrDone = progress?.phase === "enrich" ? progress.done : 0;
        const enrTotal = progress?.phase === "enrich" ? progress.total : 0;
        const enrKept = progress?.phase === "enrich" ? progress.kept : 0;
        const hoursScraped = progress?.phase === "enrich" ? progress.hoursScraped : 0;
        const hoursTarget = progress?.phase === "enrich" ? progress.hoursTarget : days * 24;
        let pct = 2;
        let label = "INITIALIZING";
        if (progress?.phase === "leaderboard" && lbTotal > 0) {
          pct = Math.round((lbDone / lbTotal) * 50);
          label = `LEADERBOARD ${lbDone}/${lbTotal}`;
        } else if (progress?.phase === "enrich" && enrTotal > 0) {
          pct = 50 + Math.round((enrDone / enrTotal) * 50);
          label = `ENRICHING ${enrDone}/${enrTotal} \u00b7 ${enrKept} kept \u00b7 ${hoursScraped}/${hoursTarget}h scraped`;
        }
        // Color the rate green when healthy (>=5/s), amber when slow (1-5/s),
        // red when crawling (<1/s, almost certainly rate-limited).
        const rateColor = rateInfo
          ? rateInfo.rate >= 5 ? "text-green-400"
            : rateInfo.rate >= 1 ? "text-amber-400"
            : "text-red-400"
          : "text-pixel-gray-light";
        return (
          <div className="pixel-panel p-2">
            <div className="flex items-center gap-3 font-mono text-[12px]">
              <div className="w-2 h-2 bg-green-400 animate-pulse shrink-0" />
              <span className="text-pixel-white shrink-0">{label}</span>
              {rateInfo && progress && rateInfo.phase === progress.phase && (
                <span className={`shrink-0 ${rateColor}`} title={rateInfo.rate < 1 ? "Slow \u2014 likely Polymarket rate-limiting" : "Enrich throughput"}>
                  {rateInfo.rate.toFixed(1)}/s {"\u00b7"} ETA {formatEta(rateInfo.etaSec)}
                </span>
              )}
              <div className="pixel-bar flex-1 h-2">
                <div className="pixel-bar-fill bg-green-400/60 transition-all" style={{ width: `${pct}%` }} />
              </div>
              <span className="text-pixel-gray-light shrink-0">{pct}%</span>
            </div>
          </div>
        );
      })()}

      {/* ── Table ── */}
      {loading && traders.length === 0 ? (
        <div className="pixel-panel p-8 text-center space-y-2">
          <div className="text-[15px] text-pixel-white">SCANNING POLYMARKET</div>
          <div className="text-[12px] text-pixel-gray">Results are cached hourly. Subsequent loads are instant.</div>
        </div>
      ) : traders.length > 0 ? (
        <>
          <div className="pixel-panel overflow-hidden">
            <div className="overflow-x-auto">
            <table className="pixel-table" style={{ minWidth: "920px", tableLayout: "fixed" }}>
              <colgroup>
                <col style={{ width: "32px" }} />
                <col style={{ width: "130px" }} />
                <col style={{ width: "140px" }} />
                <col style={{ width: "100px" }} />
                <col style={{ width: "100px" }} />
                <col style={{ width: "100px" }} />
                <col style={{ width: "72px" }} />
                <col style={{ width: "80px" }} />
                <col style={{ width: "64px" }} />
                <col style={{ width: "100px" }} />
              </colgroup>
              <thead className="sticky">
                <tr>
                  <th className="num text-center">#</th>
                  <th>ADDRESS</th>
                  <th className="text-center">P&L CURVE</th>
                  {columns.map((col) => (
                    <th key={col.key}
                      className={`sortable num ${traderSort === col.key ? "sorted" : ""} text-right`}
                      title={col.hint}
                      onClick={() => handleSort(col.key)}>
                      {col.label}
                      <SortArrow active={traderSort === col.key} dir={sortDir} />
                    </th>
                  ))}
                  <th className="num text-right" title="Trades in last 24h — flags dormant traders">24H</th>
                  <th
                    className={`sortable num ${traderSort === "last" ? "sorted" : ""} text-right`}
                    title="Time since this trader's most recent trade"
                    onClick={() => handleSort("last")}>
                    LAST
                    <SortArrow active={traderSort === "last"} dir={sortDir} />
                  </th>
                  <th className="text-center"></th>
                </tr>
              </thead>
              <tbody>
                {pageTraders.length === 0 && (
                  <tr>
                    <td colSpan={10} className="text-center py-8 text-[13px] text-pixel-gray">
                      EVERY ROW IS HIDDEN BY THE CURRENT FILTERS
                      {activityDropped > 0 && (
                        <div className="mt-2 text-[12px] text-pixel-gray-light">
                          {activityDropped.toLocaleString()} hidden because they didn&apos;t trade in the last {maxLastTradeHrs}h.{" "}
                          <button
                            onClick={() => { setMaxLastTradeHrs(""); setMinTrades24h(""); }}
                            className="pixel-btn text-[11px] px-2 py-0.5 ml-1"
                          >
                            SHOW EVERYONE
                          </button>
                        </div>
                      )}
                    </td>
                  </tr>
                )}
                {pageTraders.map((trader, i) => {
                  const rowNum = safePage * PAGE_SIZE + i + 1;
                  const pnlColor = trader.pnl > 0 ? "text-green-400" : trader.pnl < 0 ? "text-red-400" : "text-pixel-gray-light";
                  const sc = scoreFor(trader);
                  const scValid = Number.isFinite(sc);
                  const scCls = !scValid ? "text-pixel-gray" : sc > 0 ? "text-green-400" : sc < 0 ? "text-red-400" : "text-pixel-gray-light";

                  return (
                    <tr key={trader.address} className="cursor-pointer group" onClick={() => goToTrader(trader.address)}>
                      <td className="text-center !px-0">
                        <RankBadge rank={rowNum} />
                      </td>
                      <td>
                        <div className="flex items-center gap-1.5">
                          <span className="text-pixel-white font-mono text-[14px] group-hover:text-green-400 transition-colors truncate">
                            {shortAddress(trader.address)}
                          </span>
                        </div>
                      </td>
                      <td className="!px-2">
                        <div className="flex items-center justify-center">
                          <Sparkline data={trader.pnlCurve || []} width={120} height={26} />
                        </div>
                      </td>
                      <td className={`num text-right font-mono ${scCls}`} title={`score = ${formula}`}>
                        {scValid ? formatScore(sc) : "---"}
                      </td>
                      <td className={`num text-right font-mono ${pnlColor}`}>
                        {formatPnl(trader.pnl)}
                      </td>
                      <td className="num text-right text-pixel-white font-mono">
                        {formatVolume(trader.volume)}
                      </td>
                      <td className="num text-right text-pixel-gray-light font-mono">
                        {trader.recentTrades || trader.positions}
                      </td>
                      {/* 24H column — color-coded so active traders pop:
                          0 = red (dormant), 1-2 = amber, 3+ = green. */}
                      {(() => {
                        const c24 = trader.trades24h ?? 0;
                        const cls = c24 === 0
                          ? "text-red-400"
                          : c24 < 3
                            ? "text-amber-400"
                            : "text-green-400";
                        const title = c24 === 0
                          ? "No trades in last 24h — likely dormant"
                          : `${c24} trade${c24 === 1 ? "" : "s"} in last 24h`;
                        return (
                          <td className={`num text-right font-mono ${cls}`} title={title}>
                            {c24}
                          </td>
                        );
                      })()}
                      {/* LAST TRADE — relative timestamp of most recent
                          in-window trade. Green if < 1h (firing now),
                          amber if < 24h (recent), red if older (dormant).
                          Falls back to "—" when the backend payload pre-
                          dates the lastTradeTs field (older disk cache). */}
                      {(() => {
                        const ts = trader.lastTradeTs;
                        if (!ts) return <td className="num text-right text-pixel-gray-light font-mono" title="No last-trade timestamp in payload — sync to refresh">—</td>;
                        const ageSec = Math.max(0, Math.floor(Date.now() / 1000) - ts);
                        const cls = ageSec < 3600
                          ? "text-green-400"
                          : ageSec < 86_400
                            ? "text-amber-400"
                            : "text-red-400";
                        const label = ageSec < 60
                          ? `${ageSec}s`
                          : ageSec < 3600
                            ? `${Math.floor(ageSec / 60)}m`
                            : ageSec < 86_400
                              ? `${Math.floor(ageSec / 3600)}h`
                              : `${Math.floor(ageSec / 86_400)}d`;
                        return (
                          <td
                            className={`num text-right font-mono ${cls}`}
                            title={`Last trade ${new Date(ts * 1000).toLocaleString()}`}
                          >
                            {label}
                          </td>
                        );
                      })()}
                      <td
                        className="text-center !px-2 relative"
                        onClick={(e) => { e.stopPropagation(); if (onSelect) onSelect(trader.address); }}
                      >
                        {onSelect ? (
                          selectedLower.has(trader.address.toLowerCase()) ? (
                            // IN STRAT: bright filled GREEN — selection
                            // status reads at a glance. Hover swaps to RED
                            // (with REMOVE label) so the destructive action
                            // signals before the click.
                            // Notes:
                            //  - `group` on the button itself anchors the
                            //    `group-hover:` label swap; without it the
                            //    REMOVE text would never appear.
                            //  - `!` prefix is required because `.pixel-btn`
                            //    in globals.css sets border/color/background
                            //    after `@tailwind utilities;` and would
                            //    otherwise win the cascade and flatten every
                            //    state to neutral grey.
                            <button
                              type="button"
                              onClick={(e) => { e.stopPropagation(); onSelect(trader.address); }}
                              className="pixel-btn group text-[12px] px-2 py-0.5 !border-green-400 !text-green-300 !bg-green-500/25 font-semibold hover:!border-red-400 hover:!text-red-200 hover:!bg-red-500/40 transition-all whitespace-nowrap"
                              title="In strat — click to remove"
                            >
                              <span className="group-hover:hidden">✓ IN STRAT</span>
                              <span className="hidden group-hover:inline">✕ REMOVE</span>
                            </button>
                          ) : (
                            // + ADD: dashed amber outline — visually distinct
                            // from the filled IN STRAT state without competing
                            // for attention. Hover brightens to solid.
                            <button
                              type="button"
                              onClick={(e) => { e.stopPropagation(); onSelect(trader.address); }}
                              className="pixel-btn text-[12px] px-2 py-0.5 !border !border-dashed !border-amber-400/50 !text-amber-300/80 !bg-transparent hover:!border-solid hover:!border-amber-400 hover:!text-amber-200 hover:!bg-amber-500/15 transition-all whitespace-nowrap"
                              title="Add to active strat"
                            >
                              + ADD
                            </button>
                          )
                        ) : null}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            </div>
          </div>

          {/* Pagination */}
          {totalPages > 1 && (
            <div className="flex items-center justify-between pt-1 px-1">
              <span className="text-[12px] text-pixel-gray font-mono">
                {safePage * PAGE_SIZE + 1}-{Math.min((safePage + 1) * PAGE_SIZE, visibleTotal)} of {visibleTotal}
              </span>
              <div className="flex items-center gap-1">
                <button onClick={() => setPage((p) => Math.max(0, p - 1))} disabled={safePage === 0}
                  className="pixel-btn text-[12px] px-2 py-0.5 border-pixel-border text-pixel-gray hover:text-pixel-white disabled:opacity-20 disabled:cursor-not-allowed">PREV</button>
                {Array.from({ length: totalPages }, (_, i) => i)
                  .filter((i) => i === 0 || i === totalPages - 1 || Math.abs(i - safePage) <= 2)
                  .reduce<(number | "dots")[]>((acc, i) => {
                    const last = acc[acc.length - 1];
                    if (last !== undefined && last !== "dots" && i - (last as number) > 1) acc.push("dots");
                    acc.push(i);
                    return acc;
                  }, [])
                  .map((tok, idx) =>
                    tok === "dots" ? (
                      <span key={`e${idx}`} className="text-[12px] text-pixel-gray px-0.5">...</span>
                    ) : (
                      <button key={tok} onClick={() => setPage(tok)}
                        className={`pixel-btn text-[12px] w-6 py-0.5 ${
                          safePage === tok
                            ? "border-pixel-white text-pixel-white"
                            : "border-pixel-border text-pixel-gray hover:text-pixel-white"
                        }`}>{tok + 1}</button>
                    ),
                  )}
                <button onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))} disabled={safePage === totalPages - 1}
                  className="pixel-btn text-[12px] px-2 py-0.5 border-pixel-border text-pixel-gray hover:text-pixel-white disabled:opacity-20 disabled:cursor-not-allowed">NEXT</button>
              </div>
            </div>
          )}
        </>
      ) : hasLoaded && !loading ? (
        <div className="pixel-panel p-8 text-center space-y-3">
          <div className="text-[14px] text-pixel-gray tracking-wider">NOTHING TO SHOW</div>
          {activityDropped > 0 ? (
            <>
              <div className="text-[12px] text-pixel-gray-light max-w-[600px] mx-auto leading-relaxed">
                {activityDropped.toLocaleString()} traders are on the board, but the board only shows
                people who traded in the last {maxLastTradeHrs}h
                {Number(minTrades24h) > 0 ? ` (at least ${minTrades24h} today)` : ""}
                {snapshotOlderThanWindow
                  ? ` — and the data was last refreshed ${formatAgo(staleAgeMs)} ago, so nobody qualifies yet.`
                  : "."}
              </div>
              <div className="flex justify-center gap-2 flex-wrap">
                {snapshotOlderThanWindow && (
                  <button
                    onClick={() => { void loadStream({ force: true }); }}
                    disabled={refreshing || loading}
                    className="pixel-btn text-[12px] px-3 py-1 border-green-400/60 text-green-400 hover:bg-green-400/10 disabled:opacity-40"
                    title="Pull a fresh leaderboard from Polymarket (takes a few minutes)"
                  >
                    ↻ REFRESH THE DATA
                  </button>
                )}
                <button
                  onClick={() => { setMaxLastTradeHrs(""); setMinTrades24h(""); }}
                  className="pixel-btn text-[12px] px-3 py-1"
                  title="Drop the recent-activity filter and show every trader on the board"
                >
                  SHOW EVERYONE
                </button>
              </div>
            </>
          ) : (
            <div className="text-[12px] text-pixel-gray-light">
              Try a broader keyword, or open FILTERS and loosen them.
            </div>
          )}
        </div>
      ) : null}
    </div>
  );
}
