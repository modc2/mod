"use client";

import { useState, useEffect, useMemo, useCallback, useRef, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { fetchPositions, fetchWalletTradesUntil, fetchWalletTradesIncremental, formatVolume, formatPnl, fetchTradersPage, fetchTopTraderAddresses, TopTrader, CATEGORIES, CategorySlug } from "../lib/polymarket";
import { LeaderboardPreset, loadPresets, savePresets } from "../lib/leaderboardPresets";
import { PolymarketPosition, PolymarketTrade, SavedIndex, TraderRoiStats, TradeFilters } from "../lib/types";
import { tradeMatchesFilters, tradeFiltersActive } from "../lib/tradeFilters";
import { getStrat } from "../lib/strats/registry";
import type { TraderTrade as StratTraderTrade } from "../lib/strats/base";
import { marketMatchesQuery } from "../lib/marketQuery";
import { shortAddress } from "@/lib/auth";
import { useFilterParams, useFilters } from "../context/FiltersContext";
import { useAuth } from "../context/AuthContext";
import { useCopyEngine } from "../context/CopyEngineContext";
import CopyTrading from "./CopyTrading";
import PnlChart from "./PnlChart";
import type { CurvePoint } from "./PnlChart";
import { computeFifoTrades, buildPnlCurve, buildCombinedPnlCurve, aggregateToRebalanceWindows } from "../lib/pnlEngine";
import { loadIndexes, saveIndex, deleteIndex, updateIndex, getActiveIndexId, setActiveIndexId, equalWeightTraders } from "../lib/indexStore";
import LivePanel from "./LivePanel";
import StratSourceViewer from "./StratSourceViewer";
import UserStratsPanel from "./UserStratsPanel";
import ThemeToggle from "./ThemeToggle";

interface TraderSummary {
  address: string;
  positions: number;
  filteredPositions: number;
  totalPnl: number;
  loaded: boolean;
}

interface BacktestDay {
  date: string;
  buys: number;
  sells: number;
  buyVolume: number;
  sellVolume: number;
  netFlow: number;
  cumFlow: number;
  trades: number;
}

interface SimulatedTrade {
  timestamp: number;
  market: string;
  conditionId: string;
  side: "BUY" | "SELL";
  traderSize: number;
  traderPrice: number;
  traderNotional: number;
  mirrorNotional: number;
  mirrorSize: number;
  fee: number;
  gas: number;
  netCost: number;
}

interface TraderBacktest {
  address: string;
  trades: number;
  buyVolume: number;
  sellVolume: number;
  netFlow: number;
  openValue: number;
  estimatedPnl: number;
  totalFees: number;
  totalGas: number;
  pnlAfterCosts: number;
  days: BacktestDay[];
  simulatedTrades: SimulatedTrade[];
}

// Polymarket fee structure (Polygon CLOB)
// Taker fee: ~2% on matched notional; maker rebate not applicable for copy-trading
// Gas: ~0.01 MATIC per tx on Polygon ≈ $0.005 per trade at typical MATIC prices
// Note: fees are estimated on a simulated copy basis — not the trader's raw volume,
// because your copy position scales to your capital, not theirs.
const TAKER_FEE_BPS = 200; // 2% = 200 bps
const GAS_PER_TRADE_USD = 0.005;
const DEFAULT_CAPITAL = 1000;

// "12s" / "3m" / "1h4m" — terse relative-age formatter for lag chips.
function formatAgoShort(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return "—";
  const sec = Math.floor(ms / 1000);
  if (sec < 60) return `${sec}s`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m`;
  const hr = Math.floor(min / 60);
  return `${hr}h${(min % 60).toString().padStart(2, "0")}m`;
}

// Deterministic per-trade sample filter. Same (samplePct, key) always returns
// the same boolean — keeps the chart + feed in lockstep without re-sampling
// every render. Uses FNV-1a so a one-char tweak to the key reshuffles cleanly.
function keepInSample(samplePct: number, key: string): boolean {
  if (samplePct >= 100) return true;
  if (samplePct <= 0) return false;
  let h = 2166136261;
  for (let i = 0; i < key.length; i++) {
    h ^= key.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return ((h >>> 0) % 100) < samplePct;
}

function computeBacktest(
  trades: PolymarketTrade[],
  positions: PolymarketPosition[],
  windowDays: number,
  address: string,
  capital: number = DEFAULT_CAPITAL,
  rebalancePeriodHours: number = 0,
  rebalanceHour: number = 0,
): TraderBacktest {
  const cutoff = Date.now() - windowDays * 24 * 60 * 60 * 1000;
  const replay = rebalancePeriodHours > 0
    ? aggregateToRebalanceWindows(trades, rebalancePeriodHours, rebalanceHour)
    : trades;
  const allWindowTrades = replay
    .filter((t) => t.timestamp >= cutoff)
    .sort((a, b) => a.timestamp - b.timestamp);

  // Filter out SELLs without a prior BUY in the window (copy-trader wouldn't hold those)
  const windowInv = new Map<string, number>();
  const windowTrades = allWindowTrades.filter((t) => {
    const key = t.conditionId || t.market;
    if (t.side === "BUY") {
      windowInv.set(key, (windowInv.get(key) || 0) + t.size);
      return true;
    }
    const inv = windowInv.get(key) || 0;
    if (inv <= 1e-9) return false;
    const sold = Math.min(t.size, inv);
    windowInv.set(key, inv - sold);
    return true;
  });

  let buyVolume = 0;
  let sellVolume = 0;

  const dayMap = new Map<string, { buys: number; sells: number; buyVol: number; sellVol: number }>();

  for (const t of windowTrades) {
    const d = new Date(t.timestamp);
    const key = `${d.getMonth() + 1}/${d.getDate()}`;
    const day = dayMap.get(key) || { buys: 0, sells: 0, buyVol: 0, sellVol: 0 };

    const vol = t.price * t.size;
    if (t.side === "BUY") {
      buyVolume += vol;
      day.buys++;
      day.buyVol += vol;
    } else {
      sellVolume += vol;
      day.sells++;
      day.sellVol += vol;
    }
    dayMap.set(key, day);
  }

  let cumFlow = 0;
  const days: BacktestDay[] = [];
  for (const [date, d] of dayMap) {
    const netFlow = d.sellVol - d.buyVol;
    cumFlow += netFlow;
    days.push({
      date,
      buys: d.buys,
      sells: d.sells,
      buyVolume: Math.round(d.buyVol * 100) / 100,
      sellVolume: Math.round(d.sellVol * 100) / 100,
      netFlow: Math.round(netFlow * 100) / 100,
      cumFlow: Math.round(cumFlow * 100) / 100,
      trades: d.buys + d.sells,
    });
  }

  // Daily rebalancing: assume all positions are closed, only count realized P&L
  const openValue = 0; // positions.reduce((s, p) => s + p.value, 0);
  const netFlow = sellVolume - buyVolume;
  const estimatedPnl = netFlow; // + openValue;

  // Fee/gas cost estimation — scaled to simulated capital.
  // copyRatio = how much of the trader's volume you'd replicate with your capital.
  // Use max(buy, sell) to prevent blow-up when a trader mostly sells old positions.
  const traderVol = Math.max(buyVolume, sellVolume, 1);
  const copyRatio = capital / traderVol;
  const totalNotional = (buyVolume + sellVolume) * copyRatio;
  // Polymarket fee uses min(price, 1-price) per trade; approximate as avg ~40% of notional
  const totalFees = windowTrades.reduce((sum, t) => {
    const mirrorShares = t.size * copyRatio;
    return sum + mirrorShares * Math.min(t.price, 1 - t.price) * (TAKER_FEE_BPS / 10_000);
  }, 0);
  const totalGas = windowTrades.length * GAS_PER_TRADE_USD;
  // Scale PnL to simulated capital (matches the ROI metric)
  const scaledPnl = estimatedPnl * copyRatio;
  const pnlAfterCosts = scaledPnl - totalFees - totalGas;

  // Build individual simulated trades scaled to user's capital
  const simulatedTrades: SimulatedTrade[] = windowTrades.map((t) => {
    const traderNotional = t.price * t.size;
    const mirrorNotional = Math.round(traderNotional * copyRatio * 100) / 100;
    const mirrorSize = Math.round(t.size * copyRatio * 100) / 100;
    const fee = Math.round(mirrorSize * Math.min(t.price, 1 - t.price) * (TAKER_FEE_BPS / 10_000) * 100) / 100;
    const gas = GAS_PER_TRADE_USD;
    const netCost = t.side === "BUY"
      ? Math.round((mirrorNotional + fee + gas) * 100) / 100
      : Math.round((mirrorNotional - fee - gas) * 100) / 100;
    return {
      timestamp: t.timestamp,
      market: t.market,
      conditionId: t.conditionId,
      side: t.side,
      traderSize: t.size,
      traderPrice: t.price,
      traderNotional: Math.round(traderNotional * 100) / 100,
      mirrorNotional,
      mirrorSize,
      fee,
      gas,
      netCost,
    };
  });

  return {
    address,
    trades: windowTrades.length,
    buyVolume: Math.round(buyVolume * 100) / 100,
    sellVolume: Math.round(sellVolume * 100) / 100,
    netFlow: Math.round(netFlow * 100) / 100,
    openValue: Math.round(openValue * 100) / 100,
    estimatedPnl: Math.round(estimatedPnl * 100) / 100,
    totalFees: Math.round(totalFees * 100) / 100,
    totalGas: Math.round(totalGas * 100) / 100,
    pnlAfterCosts: Math.round(pnlAfterCosts * 100) / 100,
    days,
    simulatedTrades,
  };
}

/* ── Add Trader Bar ── */
function AddTraderBar({ watchlist, onAdd }: { watchlist: string[]; onAdd: (addr: string) => void }) {
  const [input, setInput] = useState("");
  const [results, setResults] = useState<TopTrader[]>([]);
  const [searching, setSearching] = useState(false);
  const [focused, setFocused] = useState(false);
  const [coldCache, setColdCache] = useState(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout>>();
  const wrapRef = useRef<HTMLDivElement>(null);

  const isAddress = (s: string) => /^0x[a-fA-F0-9]{40}$/.test(s.trim());

  const doSearch = useCallback(async (q: string) => {
    if (!q.trim() || isAddress(q)) { setResults([]); return; }
    setSearching(true);
    try {
      const res = await fetchTradersPage({ search: q, pageSize: 5, pool: 2000 });
      if (res.cold) { setColdCache(true); setResults([]); return; }
      setColdCache(false);
      setResults(res.traders.filter((t) => !watchlist.includes(t.address)));
    } catch {
      setResults([]);
    } finally {
      setSearching(false);
    }
  }, [watchlist]);

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    if (!input.trim() || isAddress(input)) { setResults([]); return; }
    debounceRef.current = setTimeout(() => doSearch(input), 400);
    return () => { if (debounceRef.current) clearTimeout(debounceRef.current); };
  }, [input, doSearch]);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setFocused(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const handleSubmit = () => {
    const v = input.trim();
    if (!v) return;
    if (isAddress(v)) {
      if (watchlist.includes(v.toLowerCase()) || watchlist.includes(v)) return;
      onAdd(v);
      setInput("");
      setResults([]);
    }
  };

  const handlePick = (addr: string) => {
    onAdd(addr);
    setInput("");
    setResults([]);
    setFocused(false);
  };

  const alreadyAdded = isAddress(input.trim()) && (watchlist.includes(input.trim().toLowerCase()) || watchlist.includes(input.trim()));

  return (
    <div ref={wrapRef} className="relative">
      <div className="flex items-center gap-2">
        <span className="text-[12px] text-pixel-gray tracking-wider shrink-0">ADD TRADER</span>
        <div className="relative flex-1">
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onFocus={() => setFocused(true)}
            onKeyDown={(e) => { if (e.key === "Enter") handleSubmit(); }}
            placeholder="0x... ADDRESS OR SEARCH NAME"
            className="pixel-input-sm w-full font-mono text-[14px] pr-16"
            spellCheck={false}
          />
          {searching && (
            <span className="absolute right-2 top-1/2 -translate-y-1/2 text-[12px] text-green-400 animate-pulse">...</span>
          )}
          {isAddress(input.trim()) && !alreadyAdded && (
            <button
              onClick={handleSubmit}
              className="absolute right-1 top-1/2 -translate-y-1/2 pixel-btn text-[12px] px-2 py-0 border-green-400 text-green-400 hover:bg-green-400/10"
            >
              ADD
            </button>
          )}
          {alreadyAdded && (
            <span className="absolute right-2 top-1/2 -translate-y-1/2 text-[12px] text-pixel-gray">ALREADY ADDED</span>
          )}
        </div>
      </div>

      {focused && results.length > 0 && (
        <div className="absolute z-50 left-0 right-0 mt-1 pixel-panel border-2 border-pixel-border bg-pixel-black max-h-[200px] overflow-y-auto">
          {results.map((t) => (
            <button
              key={t.address}
              onClick={() => handlePick(t.address)}
              className="w-full flex items-center justify-between px-3 py-2 hover:bg-pixel-white/5 transition-colors text-left"
            >
              <div className="flex items-center gap-2">
                <span className="text-[14px] font-mono text-pixel-white">{shortAddress(t.address)}</span>
                <span className={`text-[13px] font-mono ${t.pnl >= 0 ? "text-green-400" : "text-red-400"}`}>
                  {formatPnl(t.pnl)}
                </span>
              </div>
              <div className="flex items-center gap-3 text-[12px] text-pixel-gray font-mono">
                <span>VOL {formatVolume(t.volume)}</span>
                <span>{t.recentTrades || t.positions} trades</span>
              </div>
            </button>
          ))}
        </div>
      )}
      {focused && coldCache && input.trim() && !isAddress(input.trim()) && (
        <div className="absolute z-50 left-0 right-0 mt-1 pixel-panel border-2 border-pixel-border bg-pixel-black px-3 py-2">
          <span className="text-[13px] text-pixel-gray">TRADER CACHE WARMING — PASTE 0x ADDRESS DIRECTLY</span>
        </div>
      )}
    </div>
  );
}

interface CopyIndexProps {
  searchFilter: string;
  compact?: boolean;
}

// Labeled input/select group used across the backtest controls. Renders a
// small uppercase label above a bordered chip that can hold inputs, selects,
// and adornments (prefix/suffix).
function Field({
  label,
  prefix,
  suffix,
  children,
}: {
  label: string;
  prefix?: string;
  suffix?: string;
  children: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1">
      <span className="text-[11px] text-pixel-gray tracking-[0.18em] leading-none">{label}</span>
      <div className="inline-flex items-center gap-1 h-[28px] px-2 border border-pixel-border bg-pixel-black/60 hover:border-pixel-white/30 focus-within:border-green-400/70 transition-colors">
        {prefix && <span className="text-[12px] text-pixel-gray font-mono">{prefix}</span>}
        {children}
        {suffix && <span className="text-[12px] text-pixel-gray font-mono tracking-wider ml-0.5">{suffix}</span>}
      </div>
    </div>
  );
}

export default function CopyIndex({ searchFilter, compact }: CopyIndexProps) {
  const router = useRouter();
  const filterQs = useFilterParams({ excludeSearch: true });
  const { localToken, auth } = useAuth();
  // Pull the live engine state so the chart can switch its data source to
  // the engine's actual order log when mode === "LIVE" — historical replay
  // is meaningless when the user is monitoring real-time trading.
  const { isLive, engineState } = useCopyEngine();

  // ── Strategy management ──
  const [savedIndexes, setSavedIndexes] = useState<SavedIndex[]>([]);
  const [activeIndex, setActiveIndex] = useState<SavedIndex | null>(null);
  const [creatingNew, setCreatingNew] = useState(false);
  const [newName, setNewName] = useState("");
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);

  const chartPanelRef = useRef<HTMLDivElement>(null);

  // ── Data state ──
  const [traderData, setTraderData] = useState<Map<string, PolymarketPosition[]>>(new Map());
  const [traderTrades, setTraderTrades] = useState<Map<string, PolymarketTrade[]>>(new Map());
  const [loadedCount, setLoadedCount] = useState(0);
  const [loading, setLoading] = useState(true);
  const [lastUpdated, setLastUpdated] = useState<number | null>(null);
  const [utcNow, setUtcNow] = useState(Date.now());

  // ── Backtest ──
  const [backtestDays, setBacktestDays] = useState(3);
  const [backtestDaysInput, setBacktestDaysInput] = useState("3");
  const [capital, setCapital] = useState(DEFAULT_CAPITAL);
  const [minTrade, setMinTrade] = useState(1);
  const [maxTrade, setMaxTrade] = useState(100);
  // Top-N cap (mirrors CopyEngineConfig.maxPerCycle). The backtest applies
  // the same sampling the live engine does so the displayed P&L reflects
  // what live will actually execute after Sharpe-rank filtering.
  const [maxPerCycle, setMaxPerCycle] = useState(3);
  // Free-text market-topic filter — restricts the strat (backtest + live) to
  // markets whose title matches the query (e.g. "price of bitcoin"). Empty =
  // every market the watched traders touch.
  const [marketQuery, setMarketQuery] = useState("");
  // Uncommitted input mirror — lets the user type freely before the query is
  // committed (blur / Enter) so the backtest doesn't re-run on each keystroke.
  const [marketQueryInput, setMarketQueryInput] = useState("");
  // ── Semantic per-trade filters (side / price band / size band / category) ──
  // The gate that makes a strat unique: two strats on the same traders with
  // different filters copy different slices of the flow. Source of truth is
  // `tradeFilters`; the four numeric band inputs keep uncommitted string
  // mirrors (commit on blur / Enter) so the backtest doesn't churn per
  // keystroke. Side + category commit immediately.
  const [tradeFilters, setTradeFilters] = useState<TradeFilters>({});
  const [priceMinInput, setPriceMinInput] = useState("");
  const [priceMaxInput, setPriceMaxInput] = useState("");
  const [sizeMinInput, setSizeMinInput] = useState("");
  const [sizeMaxInput, setSizeMaxInput] = useState("");
  // SHOW ALL TRADES — when true, the linkedTrades pipeline skips the TRADE
  // SIZE filter and surfaces every upstream trade regardless of mirror
  // amount. Declared here (not next to feedOrder) so it's available to the
  // linkedTrades useMemo that fires earlier in the render.
  const [showAllTrades, setShowAllTrades] = useState(false);
  const [maxTradesPerHour, setMaxTradesPerHour] = useState(10);
  // SAMPLE %: deterministically thin the in-window trades to this fraction.
  // 100 = keep all (default), 50 = keep ~half, 10 = keep ~tenth. Curve, feed,
  // and fee/gas/total stats all derive from the sampled set.
  const [samplePct, setSamplePct] = useState(100);
  // Per-trade replay is the only mode now — `rebalancePeriod`/`rebalanceHour`
  // are kept for backwards compat with persisted SavedIndex but always loaded
  // as 0/0 going forward. The aggregation path in computeBacktest stays no-op
  // unless an older strat still has a non-zero value.
  const [rebalancePeriod, setRebalancePeriod] = useState<number>(0); // hours (0 = per-trade)
  const [rebalanceHour, setRebalanceHour] = useState<number>(0); // 0-23 (unused when per-trade)
  const [rebalanceMinutes, setRebalanceMinutes] = useState<number>(5 / 60); // minutes between live polls (5s)
  const [customDaysInput, setCustomDaysInput] = useState("");
  const [expandedTrader, setExpandedTrader] = useState<string | null>(null);
  const [showSimTrades, setShowSimTrades] = useState<Record<string, boolean>>({});
  const [simTradeLimit, setSimTradeLimit] = useState<Record<string, number>>({});
  const [refreshKey, setRefreshKey] = useState(0);

  // ── Weights (local state, persisted on change) ──
  const [traderWeights, setTraderWeights] = useState<Record<string, number>>({});

  // ── Mode toggle (STRATS = manage, BACKTEST = test, LIVE = copy) ──
  // Default to LIVE so a returning user lands directly on their copy engine
  // rather than the strategy-management screen. The LIVE tab is itself
  // disabled until a watchlist exists, so first-time-no-strat users still
  // see STRATS through the disabled-tab fallback in the tabs render block.
  // Land on STRATS — it always has content (build/browse a strat), unlike LIVE
  // which is blank until a wallet's connected and an engine is running. The
  // LIVE tab badges itself RUNNING so a live session is still obvious at a
  // glance from here.
  const [mode, setMode] = useState<"STRATS" | "HUB" | "BACKTEST" | "LIVE">("STRATS");

  // STRAT subtabs — SOURCE (the strat's code), TRADERS (leaderboard +
  // watchlist editor), PARAMS (every tuning knob). Params used to live in
  // a docked panel above the tabs on every view; they're a subtab now and
  // render nowhere else. Selection persists across reloads.
  const [stratTab, setStratTab] = useState<"SOURCE" | "TRADERS" | "PARAMS">(() => {
    if (typeof window === "undefined") return "TRADERS";
    const v = window.localStorage.getItem("stratSubTab");
    return v === "SOURCE" || v === "PARAMS" ? v : "TRADERS";
  });
  useEffect(() => {
    if (typeof window === "undefined") return;
    try { window.localStorage.setItem("stratSubTab", stratTab); } catch {}
  }, [stratTab]);

  // ── Embedded top-traders leaderboard (STRATS mode) ──
  // The standalone /traders page was folded into strat management: discovering
  // who to copy and assembling a strat now live in one place. The leaderboard
  // reads the shared TopBar filters (category / window / market topic), and
  // selecting a trader toggles them in/out of the active strat.
  const [browseOpen, setBrowseOpen] = useState(false);
  const {
    category: browseCategory,
    daysAgo: browseDaysAgo,
    minPerDay: browseMinPerDayRaw,
    marketQuery: browseMarketQuery,
    reloadKey: browseReloadKey,
    setCategory: setBrowseCategory,
    setDaysAgo: setBrowseDaysAgo,
    setMinPerDay: setBrowseMinPerDay,
    setMarketQuery: setBrowseMarketQuery,
  } = useFilters();
  const browseDays = Number(browseDaysAgo) > 0 ? Number(browseDaysAgo) : 7;
  const browseMinTradesPerDay = browseMinPerDayRaw !== "" && Number.isFinite(Number(browseMinPerDayRaw))
    ? Math.max(0, Number(browseMinPerDayRaw))
    : 0;

  // ── Pinned leaderboard presets (named filter combos, e.g. "BTC ≥3/day") ──
  const [presets, setPresets] = useState<LeaderboardPreset[]>([]);
  useEffect(() => { setPresets(loadPresets()); }, []);
  const applyPreset = (p: LeaderboardPreset) => {
    setBrowseCategory(p.category as CategorySlug);
    setBrowseMarketQuery(p.marketQuery);
    setBrowseDaysAgo(p.daysAgo);
    setBrowseMinPerDay(p.minPerDay);
    setBrowseOpen(true);
  };
  const saveCurrentAsPreset = () => {
    const name = (typeof window !== "undefined" ? window.prompt("Name this leaderboard (e.g. \"BTC ≥3/day\"):") : "")?.trim();
    if (!name) return;
    const next = [...presets, {
      id: Date.now().toString(36),
      name,
      category: browseCategory,
      marketQuery: browseMarketQuery,
      daysAgo: browseDaysAgo,
      minPerDay: browseMinPerDayRaw,
    }];
    setPresets(next);
    savePresets(next);
  };
  const deletePreset = (id: string) => {
    const next = presets.filter((p) => p.id !== id);
    setPresets(next);
    savePresets(next);
  };

  // UTC clock tick
  useEffect(() => {
    const t = setInterval(() => setUtcNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);

  // Derive watchlist from active strategy (only enabled traders)
  const watchlist = useMemo(
    () => (activeIndex ? activeIndex.traders.filter((t) => t.enabled !== false).map((t) => t.address) : []),
    [activeIndex],
  );
  // Full list including hidden traders (for the editor panel)
  const allTraderAddrs = useMemo(
    () => (activeIndex ? activeIndex.traders.map((t) => t.address) : []),
    [activeIndex],
  );
  const watchlistKey = watchlist.join(",");

  // ── Init: load or auto-create a single default strat ──
  useEffect(() => {
    let indexes = loadIndexes();

    // Auto-migrate legacy flat watchlist (first load only)
    if (indexes.length === 0) {
      try {
        const legacy = localStorage.getItem("poly8bit_watchlist");
        if (legacy) {
          const addrs = JSON.parse(legacy) as string[];
          if (addrs.length > 0) {
            const now = Date.now();
            const migrated: SavedIndex = {
              id: now.toString(36),
              name: "Default",
              traders: addrs.map((a) => ({ address: a, weight: 1 / addrs.length })),
              backtestDays: 3,
              createdAt: now,
              updatedAt: now,
            };
            saveIndex(migrated);
            indexes = [migrated];
          }
        }
      } catch {}
    }

    // Always ensure at least one strat exists
    let seeded: SavedIndex | null = null;
    if (indexes.length === 0) {
      const now = Date.now();
      const def: SavedIndex = {
        id: now.toString(36),
        name: "Default",
        traders: [],
        backtestDays: 3,
        createdAt: now,
        updatedAt: now,
      };
      saveIndex(def);
      indexes = [def];
      seeded = def;
    }

    setSavedIndexes(indexes);
    const activeId = getActiveIndexId();
    const found = activeId ? indexes.find((i) => i.id === activeId) : null;
    const active = found || indexes[0];
    setActiveIndex(active);
    setActiveIndexId(active.id);

    // Brand-new strat with nobody on it — seed it with the top 10 traders
    // matching the current leaderboard filters instead of leaving it at 0
    // traders (which used to render an empty "NO TRADES" feed by default).
    if (seeded) {
      fetchTopTraderAddresses(
        { days: browseDays, minPerDay: browseMinTradesPerDay, category: browseCategory, marketQuery: browseMarketQuery },
        10,
      ).then((addrs) => {
        if (addrs.length === 0) return;
        const traders = equalWeightTraders(addrs);
        updateIndex(seeded!.id, { traders, updatedAt: Date.now() });
        setSavedIndexes(loadIndexes());
        setActiveIndex((cur) => (cur && cur.id === seeded!.id ? { ...cur, traders } : cur));
      });
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshKey]);

  // Load weights + backtest days from active strategy
  useEffect(() => {
    if (!activeIndex) {
      setTraderWeights({});
      setBacktestDays(3);
      setBacktestDaysInput("3");
      return;
    }
    const w: Record<string, number> = {};
    for (const t of activeIndex.traders) w[t.address] = Math.round(t.weight * 100);
    setTraderWeights(w);
    if (activeIndex.backtestDays) {
      setBacktestDays(activeIndex.backtestDays);
      setBacktestDaysInput(String(activeIndex.backtestDays));
      if (![3, 7, 14, 30].includes(activeIndex.backtestDays)) {
        setCustomDaysInput(String(activeIndex.backtestDays));
      } else {
        setCustomDaysInput("");
      }
    }
    setCapital(activeIndex.capital ?? DEFAULT_CAPITAL);
    setMinTrade(activeIndex.minTrade ?? 1);
    setMaxTrade(activeIndex.maxTrade ?? 100);
    setMaxTradesPerHour(activeIndex.maxTradesPerHour ?? 10);
    setMaxPerCycle(activeIndex.maxPerCycle ?? 3);
    setMarketQuery(activeIndex.marketQuery ?? "");
    setMarketQueryInput(activeIndex.marketQuery ?? "");
    const tf = activeIndex.tradeFilters ?? {};
    setTradeFilters(tf);
    setPriceMinInput(tf.minPrice != null ? String(Math.round(tf.minPrice * 100)) : "");
    setPriceMaxInput(tf.maxPrice != null ? String(Math.round(tf.maxPrice * 100)) : "");
    setSizeMinInput(tf.minNotional != null ? String(tf.minNotional) : "");
    setSizeMaxInput(tf.maxNotional != null ? String(tf.maxNotional) : "");
    // Force per-trade replay on load — REBALANCE/AT selects were removed,
    // and any non-zero persisted value would silently aggregate trades into
    // windows with no way to undo from the UI.
    setRebalancePeriod(0);
    setRebalanceHour(0);
    // Treat the legacy 1-minute default as "unset" and surface the new 5s
    // default — keeps strats saved before the 5s switch from being stuck
    // on the slow cadence.
    setRebalanceMinutes(
      activeIndex.rebalanceMinutes && activeIndex.rebalanceMinutes !== 1
        ? activeIndex.rebalanceMinutes
        : 5 / 60,
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeIndex?.id]);

  // ── Persist helper ──
  const persistIndex = useCallback((idx: SavedIndex) => {
    const updated = { ...idx, updatedAt: Date.now() };
    updateIndex(updated.id, updated);
    setActiveIndex(updated);
    setSavedIndexes(loadIndexes());
    // Broadcast so the /traders page (and any other listener) re-reads the
    // active strat — keeps the +ADD/✓ toggle in sync across surfaces.
    window.dispatchEvent(new Event("strat-updated"));
  }, []);

  // ── Strategy CRUD ──
  const createStrategy = (name: string) => {
    const now = Date.now();
    const idx: SavedIndex = {
      id: now.toString(36),
      name: name.trim() || "Untitled",
      traders: [],
      backtestDays: 3,
      createdAt: now,
      updatedAt: now,
    };
    saveIndex(idx);
    setActiveIndex(idx);
    setActiveIndexId(idx.id);
    setSavedIndexes(loadIndexes());
    setCreatingNew(false);
    setNewName("");
  };

  const selectStrategy = (id: string) => {
    const fresh = loadIndexes().find((i) => i.id === id);
    if (!fresh) return;
    setActiveIndex(fresh);
    setActiveIndexId(id);
  };

  const handleDeleteStrategy = (id: string) => {
    deleteIndex(id);
    const remaining = loadIndexes();
    setSavedIndexes(remaining);
    if (activeIndex?.id === id) {
      const next = remaining[0] || null;
      setActiveIndex(next);
      setActiveIndexId(next?.id ?? null);
    }
    setConfirmDeleteId(null);
  };

  const handleRename = (id: string, name: string) => {
    if (!name.trim()) { setRenamingId(null); return; }
    const idx = loadIndexes().find((i) => i.id === id);
    if (idx) persistIndex({ ...idx, name: name.trim() });
    setRenamingId(null);
  };

  // ── Weight updates (persist immediately) ──
  const updateWeight = (addr: string, pct: number) => {
    setTraderWeights((prev) => ({ ...prev, [addr]: pct }));
    if (activeIndex) {
      const traders = activeIndex.traders.map((t) =>
        t.address === addr ? { ...t, weight: pct / 100 } : t,
      );
      updateIndex(activeIndex.id, { traders, updatedAt: Date.now() });
    }
  };

  // ── Bulk weight operations ──
  const equalizeWeights = () => {
    if (!activeIndex || activeIndex.traders.length === 0) return;
    // Only equalize enabled traders; leave disabled at 0
    const enabled = activeIndex.traders.filter((t) => t.enabled !== false);
    const n = enabled.length;
    if (n === 0) return;
    const base = Math.floor(100 / n);
    const remainder = 100 - base * n;
    const enabledAddrs = new Set(enabled.map((t) => t.address));
    const newWeights: Record<string, number> = {};
    let ei = 0;
    const traders = activeIndex.traders.map((t) => {
      if (!enabledAddrs.has(t.address)) {
        newWeights[t.address] = 0;
        return { ...t, weight: 0 };
      }
      const w = base + (ei < remainder ? 1 : 0);
      ei++;
      newWeights[t.address] = w;
      return { ...t, weight: w / 100 };
    });
    setTraderWeights(newWeights);
    persistIndex({ ...activeIndex, traders });
  };

  const normalizeWeights = () => {
    if (!activeIndex || activeIndex.traders.length === 0) return;
    const enabled = activeIndex.traders.filter((t) => t.enabled !== false);
    const enabledAddrs = new Set(enabled.map((t) => t.address));
    const sum = enabled.reduce((s, t) => s + (traderWeights[t.address] || 0), 0);
    if (sum <= 0) { equalizeWeights(); return; }
    const newWeights: Record<string, number> = {};
    let assigned = 0;
    let ei = 0;
    const traders = activeIndex.traders.map((t) => {
      if (!enabledAddrs.has(t.address)) {
        newWeights[t.address] = 0;
        return { ...t, weight: 0 };
      }
      const raw = traderWeights[t.address] || 0;
      const w = ei < enabled.length - 1
        ? Math.round((raw / sum) * 100)
        : 100 - assigned;
      assigned += w;
      ei++;
      newWeights[t.address] = w;
      return { ...t, weight: w / 100 };
    });
    setTraderWeights(newWeights);
    persistIndex({ ...activeIndex, traders });
  };

  // ── Backtest days (persist to strategy) ──
  const updateBacktestDays = (days: number) => {
    setBacktestDays(days);
    if (activeIndex) {
      updateIndex(activeIndex.id, { backtestDays: days, updatedAt: Date.now() });
    }
  };

  const handleCustomDays = (val: string) => {
    setCustomDaysInput(val);
    const n = parseInt(val, 10);
    if (n > 0 && n <= 365) {
      updateBacktestDays(n);
    }
  };

  // ── Capital (persist to strategy) ──
  const capitalLabel = capital >= 1000 ? `$${Math.round(capital / 1000)}K` : `$${capital}`;
  const updateCapital = (amt: number) => {
    const clamped = Math.max(1, Math.round(amt));
    setCapital(clamped);
    if (activeIndex) {
      updateIndex(activeIndex.id, { capital: clamped, updatedAt: Date.now() });
    }
  };
  const capitalPresets = [1000, 5000, 10000, 50000];

  // ── Trade size limits (persist to strategy) ──
  const updateMinTrade = (amt: number) => {
    const clamped = Math.max(0, amt);
    setMinTrade(clamped);
    if (activeIndex) {
      updateIndex(activeIndex.id, { minTrade: clamped, updatedAt: Date.now() });
    }
  };

  const updateMaxTrade = (amt: number) => {
    const clamped = Math.max(1, amt);
    setMaxTrade(clamped);
    if (activeIndex) {
      updateIndex(activeIndex.id, { maxTrade: clamped, updatedAt: Date.now() });
    }
  };

  const updateMaxTradesPerHour = (max: number) => {
    const clamped = Math.max(1, max);
    setMaxTradesPerHour(clamped);
    if (activeIndex) {
      updateIndex(activeIndex.id, { maxTradesPerHour: clamped, updatedAt: Date.now() });
    }
  };

  // ── Poll interval (persist to strategy) ──
  const updateRebalanceMinutes = (minutes: number) => {
    setRebalanceMinutes(minutes);
    if (activeIndex) {
      updateIndex(activeIndex.id, { rebalanceMinutes: minutes, updatedAt: Date.now() });
    }
  };

  // ── Market-topic filter (persist to strategy) ──
  // Restricts the strat to markets whose title matches the query; empty string
  // means "all markets". Persisted on commit (blur / Enter) so a half-typed
  // query doesn't churn the backtest on every keystroke.
  const updateMarketQuery = (q: string) => {
    const trimmed = q.trim();
    setMarketQuery(trimmed);
    if (activeIndex) {
      updateIndex(activeIndex.id, { marketQuery: trimmed, updatedAt: Date.now() });
    }
  };

  // ── Top-N per cycle (persist to strategy) ──
  const updateMaxPerCycle = (n: number) => {
    const clamped = Math.max(1, n);
    setMaxPerCycle(clamped);
    if (activeIndex) {
      updateIndex(activeIndex.id, { maxPerCycle: clamped, updatedAt: Date.now() });
    }
  };

  // ── Semantic per-trade filters (persist to strategy) ──
  // Merge-patch one dimension (side / price band / size band / category),
  // leaving the others untouched. Backtest + live engine both read these.
  const patchTradeFilters = (changes: Partial<TradeFilters>) => {
    const next = { ...tradeFilters, ...changes };
    setTradeFilters(next);
    if (activeIndex) {
      updateIndex(activeIndex.id, { tradeFilters: next, updatedAt: Date.now() });
    }
  };

  const toggleTradeCategory = (slug: string) => {
    const current = tradeFilters.categories ?? [];
    patchTradeFilters({
      categories: current.includes(slug) ? current.filter((c) => c !== slug) : [...current, slug],
    });
  };

  // ── Data fetching ──
  // `silent=true` skips the loading spinner — used by the background refresh
  // so the curve top-up doesn't flash the empty state on every interval tick.
  // Mirror traderTrades into a ref so the silent-refresh path can read the
  // current map without retriggering fetchAll's useCallback identity on every
  // state change. The ref is updated below in a tiny useEffect.
  const traderTradesRef = useRef<Map<string, PolymarketTrade[]>>(new Map());

  const fetchAll = useCallback(async (addresses: string[], silent = false) => {
    if (addresses.length === 0) {
      setLoading(false);
      return;
    }
    if (!silent) {
      setLoading(true);
      setLoadedCount(0);
    }

    const cutoffSec = Math.floor((Date.now() - 90 * 86400_000) / 1000);
    let done = 0;
    const promises = addresses.map(async (addr) => {
      try {
        // Silent refreshes ride the incremental path — fetches /activity
        // backwards from "now" until it hits a trade we've already seen,
        // then merges. Avoids re-pulling the same 90-day history every 60s.
        const existing = traderTradesRef.current.get(addr) || [];
        const tradesFetcher = silent && existing.length > 0
          ? fetchWalletTradesIncremental(addr, existing, cutoffSec)
          : fetchWalletTradesUntil(addr, cutoffSec, undefined, 2000);
        const [positions, trades] = await Promise.all([
          fetchPositions(addr),
          tradesFetcher,
        ]);
        done++;
        if (!silent) setLoadedCount(done);
        return { addr, positions, trades };
      } catch {
        done++;
        if (!silent) setLoadedCount(done);
        return { addr, positions: [] as PolymarketPosition[], trades: [] as PolymarketTrade[] };
      }
    });

    await Promise.allSettled(
      promises.map(async (p) => {
        const result = await p;
        setTraderData((prev) => {
          const next = new Map(prev);
          next.set(result.addr, result.positions);
          return next;
        });
        setTraderTrades((prev) => {
          const next = new Map(prev);
          next.set(result.addr, result.trades);
          return next;
        });
        return result;
      }),
    );

    if (!silent) setLoading(false);
    setLastUpdated(Date.now());
  }, []);

  // Mirror traderTrades into the ref so the silent-refresh path sees the
  // latest cached series when picking a baseline for incremental fetching.
  useEffect(() => {
    traderTradesRef.current = traderTrades;
  }, [traderTrades]);

  // Re-fetch when watchlist addresses actually change or when manually refreshed
  useEffect(() => {
    if (watchlist.length === 0) {
      setLoading(false);
      return;
    }
    fetchAll(watchlist);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [watchlistKey, refreshKey]);

  // Background refresh — re-poll every BACKTEST_REFRESH_MS so the curve's
  // right edge keeps catching new trades. Without this the chart looks
  // sparse near "now" because data was only pulled once on mount. Guards:
  //   • only runs when there's a watchlist (no point polling 0 addrs)
  //   • only when the user is on a tab that uses traderTrades (STRATS hides
  //     the curve so polling is wasted; LIVE has its own copy engine on a
  //     1-min cadence already)
  useEffect(() => {
    if (watchlist.length === 0) return;
    if (mode === "STRATS") return;
    const BACKTEST_REFRESH_MS = 60_000;
    const t = setInterval(() => {
      // Re-fetch the whole watchlist silently. Reuses the same setTraderTrades
      // path so the curve + linked feed rebuild via their existing useMemos.
      fetchAll(watchlist, true);
    }, BACKTEST_REFRESH_MS);
    return () => clearInterval(t);
  }, [watchlistKey, mode, fetchAll]);

  // Listen for external strategy updates (e.g. trader added from the leaderboard)
  useEffect(() => {
    const handler = () => {
      const indexes = loadIndexes();
      setSavedIndexes(indexes);
      const aid = getActiveIndexId();
      const found = aid ? indexes.find((i) => i.id === aid) : indexes[0];
      if (found) {
        setActiveIndex(found);
        // Re-hydrate backtest/sizing params so external strat edits flow
        // into the backtest + config bar without a strat switch.
        setCapital(found.capital ?? DEFAULT_CAPITAL);
        setMinTrade(found.minTrade ?? 1);
        setMaxTrade(found.maxTrade ?? 100);
        setMaxTradesPerHour(found.maxTradesPerHour ?? 10);
        setMaxPerCycle(found.maxPerCycle ?? 3);
        setMarketQuery(found.marketQuery ?? "");
        setMarketQueryInput(found.marketQuery ?? "");
        setTradeFilters(found.tradeFilters ?? {});
        const current = new Set(watchlist);
        const newAddrs = found.traders.map((t) => t.address).filter((a) => !current.has(a));
        if (newAddrs.length > 0) fetchAll(newAddrs);
      }
    };
    window.addEventListener("strat-updated", handler);
    return () => window.removeEventListener("strat-updated", handler);
  }, [watchlist, fetchAll]);

  // ── Add / remove traders (auto-equalize weights) ──
  const addTrader = (addr: string) => {
    if (!activeIndex) return;
    if (activeIndex.traders.some((t) => t.address === addr)) return;
    const n = activeIndex.traders.length + 1;
    const base = Math.floor(100 / n);
    const remainder = 100 - base * n;
    const newWeights: Record<string, number> = {};
    const traders = [...activeIndex.traders, { address: addr, weight: 0 }].map((t, i) => {
      const w = base + (i < remainder ? 1 : 0);
      newWeights[t.address] = w;
      return { ...t, weight: w / 100 };
    });
    setTraderWeights((prev) => ({ ...prev, ...newWeights }));
    persistIndex({ ...activeIndex, traders });
    fetchAll([addr]);
  };

  const removeTrader = (addr: string) => {
    if (!activeIndex) return;
    const remaining = activeIndex.traders.filter((t) => t.address !== addr);
    // Re-normalize remaining weights
    if (remaining.length > 0) {
      const sum = remaining.reduce((s, t) => s + (traderWeights[t.address] || 0), 0);
      const newWeights: Record<string, number> = {};
      let assigned = 0;
      const traders = remaining.map((t, i) => {
        const raw = traderWeights[t.address] || 0;
        const w = sum > 0
          ? (i < remaining.length - 1 ? Math.round((raw / sum) * 100) : 100 - assigned)
          : Math.floor(100 / remaining.length) + (i < 100 % remaining.length ? 1 : 0);
        assigned += w;
        newWeights[t.address] = w;
        return { ...t, weight: w / 100 };
      });
      setTraderWeights(newWeights);
      persistIndex({ ...activeIndex, traders });
    } else {
      setTraderWeights({});
      persistIndex({ ...activeIndex, traders: [] });
    }
    setTraderData((prev) => { const m = new Map(prev); m.delete(addr); return m; });
    setTraderTrades((prev) => { const m = new Map(prev); m.delete(addr); return m; });
  };

  // Wipe the active strat back to a clean slate — empties the watchlist,
  // clears weights, drops cached trader trades/positions, and resets the
  // tuning knobs to defaults. Asks for confirmation since this is one-click
  // away from the live engine (a running strat with no traders would just
  // sit there doing nothing — still worth a prompt).
  const resetStrat = () => {
    if (!activeIndex) return;
    if (typeof window !== "undefined") {
      const ok = window.confirm(
        `Reset "${activeIndex.name}"? This removes all ${activeIndex.traders.length} traders, weights, and saved tuning. The strat itself stays — only its contents are cleared.`,
      );
      if (!ok) return;
    }
    setTraderWeights({});
    setTraderData(new Map());
    setTraderTrades(new Map());
    setCapital(DEFAULT_CAPITAL);
    setMinTrade(1);
    setMaxTrade(100);
    setMaxTradesPerHour(10);
    setSamplePct(100);
    setRebalancePeriod(0);
    setRebalanceHour(0);
    setRebalanceMinutes(5 / 60);
    persistIndex({
      ...activeIndex,
      traders: [],
      capital: DEFAULT_CAPITAL,
      minTrade: 1,
      maxTrade: 100,
      maxTradesPerHour: 10,
      rebalancePeriod: 0,
      rebalanceHour: 0,
      rebalanceMinutes: 5 / 60,
      // Drop the cached backtest snapshot so the leaderboard doesn't show
      // stale +145 / +1.4% next to an empty strat.
      lastPnl: undefined,
      lastPnlAfterCosts: undefined,
      lastRoi1k: undefined,
      lastTradeCount: undefined,
      lastBacktestAt: undefined,
    });
  };

  const toggleTrader = (addr: string) => {
    if (!activeIndex) return;
    const traders = activeIndex.traders.map((t) =>
      t.address === addr ? { ...t, enabled: t.enabled === false ? true : false } : t
    );
    persistIndex({ ...activeIndex, traders });
  };

  const goToTrader = (addr: string) => {
    router.push(`/traders/${addr}${filterQs ? `?${filterQs}` : ""}`);
  };


  // ── Backtests ──
  const backtests = useMemo((): TraderBacktest[] => {
    return watchlist.map((addr) => {
      // Honor the strat's market-topic filter so the preview P&L reflects only
      // the markets the live strat would actually trade.
      const trades = (traderTrades.get(addr) || [])
        .filter((t) => marketMatchesQuery(t.market, marketQuery));
      const positions = traderData.get(addr) || [];
      return computeBacktest(trades, positions, backtestDays, addr, capital, rebalancePeriod, rebalanceHour);
    }).sort((a, b) => b.estimatedPnl - a.estimatedPnl);
  }, [watchlist, traderTrades, traderData, backtestDays, capital, rebalancePeriod, rebalanceHour, marketQuery]);

  const totalBacktestPnl = backtests.reduce((s, b) => s + b.estimatedPnl, 0);
  // Only sum weights of enabled traders
  const enabledSet = new Set(watchlist);
  const totalWeight = Object.entries(traderWeights)
    .filter(([addr]) => enabledSet.has(addr))
    .reduce((s, [, w]) => s + w, 0);

  // Aggregate fee/gas totals (already scaled to $1K copy per trader)
  const totalFees = backtests.reduce((s, b) => s + b.totalFees, 0);
  const totalGas = backtests.reduce((s, b) => s + b.totalGas, 0);
  const totalCosts = totalFees + totalGas;
  const totalTradeCount = backtests.reduce((s, b) => s + b.trades, 0);
  const totalScaledPnl = backtests.reduce((s, b) => s + b.pnlAfterCosts + b.totalFees + b.totalGas, 0);

  // Fee burden: what % of scaled gross PnL is eaten by fees
  const feeBurdenPct = totalScaledPnl > 0 ? (totalCosts / totalScaledPnl) * 100 : 0;
  const feesExceedPnl = totalScaledPnl > 0 && totalCosts > totalScaledPnl;
  const feesWarning = feeBurdenPct > 50 || feesExceedPnl || (totalScaledPnl <= 0 && totalCosts > 5);

  // Weighted gross PnL (raw, trader-scale — for display)
  const weightedBacktestPnl = useMemo(() => {
    if (totalWeight <= 0) return totalBacktestPnl;
    return backtests.reduce((s, bt) => {
      const w = (traderWeights[bt.address] || 0) / totalWeight;
      return s + bt.estimatedPnl * w;
    }, 0);
  }, [backtests, traderWeights, totalWeight, totalBacktestPnl]);

  // Weighted net PnL after costs (scaled to $1K)
  const weightedPnlAfterCosts = useMemo(() => {
    if (totalWeight <= 0) return backtests.reduce((s, b) => s + b.pnlAfterCosts, 0);
    return backtests.reduce((s, bt) => {
      const w = (traderWeights[bt.address] || 0) / totalWeight;
      return s + bt.pnlAfterCosts * w;
    }, 0);
  }, [backtests, traderWeights, totalWeight]);

  // ROI per trader as percentage of the capital allocated to this trader.
  // pnlAfterCosts is already scaled to user capital, so ROI% = pnl / capital * 100.
  const roiPerTrader = (bt: TraderBacktest) => capital > 0 ? (bt.pnlAfterCosts / capital) * 100 : 0;
  const tradersWithBuys = backtests.filter((bt) => bt.buyVolume > 0);
  // Combined ROI (%): weighted P&L over total capital, expressed as percent
  const combinedRoi1k = useMemo(() => {
    if (tradersWithBuys.length === 0 || capital <= 0) return 0;
    if (totalWeight <= 0) {
      // Equal weight: average of per-trader ROI%
      return tradersWithBuys.reduce((s, bt) => s + (bt.pnlAfterCosts / capital) * 100, 0) / tradersWithBuys.length;
    }
    return tradersWithBuys.reduce((s, bt) => {
      const w = (traderWeights[bt.address] || 0) / totalWeight;
      return s + ((bt.pnlAfterCosts / capital) * 100) * w;
    }, 0);
  }, [tradersWithBuys, traderWeights, totalWeight, capital]);

  // ── Strat instance — single source of truth for live + backtest ──
  // The live engine instantiates its OWN copy from the registry (so the
  // backtest can re-render without disturbing the live cycle's state),
  // but both reference the same class with the same opts. Drop a new
  // strat into src/app/app/lib/strats/registry.ts and BOTH adopt it.
  const backtestStrat = useMemo(
    () => getStrat(undefined, { maxPerCycle, marketQuery, tradeFilters }),
    [maxPerCycle, marketQuery, tradeFilters],
  );

  // ── Per-trader 30d ROI stats (drives top-N sampling) ──
  // Computed from the same `traderTrades` cache the backtest already
  // loaded — no extra fetches. The shape MUST match what the live engine
  // builds in fetchTraderRoiStats so the strat's scoreCandidate behaves
  // identically in live + backtest.
  const traderStatsMap = useMemo(() => {
    const out = new Map<string, TraderRoiStats>();
    const sharpeCutoffMs = Date.now() - 30 * 86400_000;
    for (const addr of watchlist) {
      const trades = (traderTrades.get(addr) || [])
        .filter((t) => marketMatchesQuery(t.market, marketQuery));
      const positions = traderData.get(addr) || [];
      const annotated = computeFifoTrades(trades, positions, sharpeCutoffMs);
      const inWin = annotated.filter((t) => t.timestamp >= sharpeCutoffMs);
      let cashDeployed = 0;
      const returns: number[] = [];
      for (const t of inWin) {
        if (t.side === "BUY") cashDeployed += t.price * t.size;
        if (t.side !== "SELL" || !t.hasBasis || !t.buyPrice) continue;
        const entryNotional = t.buyPrice * t.size;
        if (entryNotional <= 0) continue;
        returns.push(t.realized / entryNotional);
      }
      const n = returns.length;
      let roi = 0, stdev = 0;
      if (n > 0) {
        roi = returns.reduce((s, r) => s + r, 0) / n;
        if (n >= 2) {
          const variance = returns.reduce((s, r) => s + (r - roi) ** 2, 0) / (n - 1);
          stdev = Math.sqrt(variance);
        }
      }
      const sharpe = n >= 3 && stdev > 0 ? roi / stdev : 0;
      out.set(addr, {
        address: addr.toLowerCase(),
        windowDays: 30,
        roi, stdev, sampleSize: n, sharpe, cashDeployed,
        syncedAt: Date.now(),
      });
    }
    return out;
  }, [watchlist, traderTrades, traderData, marketQuery]);

  // ── Top-N sampling: which BUY IDs survive the strat's filter ──
  // Goes through the SAME strat class the live engine uses (registry).
  // Swapping the strat in registry.ts updates BOTH live behavior AND
  // this backtest preview — no separate inline math to keep in sync.
  const keptBuyIds = useMemo(() => {
    if (watchlist.length === 0) return new Set<string>();
    const strat = backtestStrat;
    const cycleBucketMs = Math.max(60_000, Math.round((rebalanceMinutes || 1) * 60_000));
    const windowCutoffMs = Date.now() - backtestDays * 86400_000;
    const totalW = watchlist.reduce((s, a) => s + (traderWeights[a] || 0), 0) || 1;

    type Cand = { id: string; ts: number; score: number };
    const buys: Cand[] = [];
    for (const addr of watchlist) {
      const trades = traderTrades.get(addr) || [];
      const stats = traderStatsMap.get(addr) ?? null;
      const weight = traderWeights[addr] || 0;
      const weightFraction = weight / totalW;
      for (const t of trades) {
        if (t.timestamp < windowCutoffMs) continue;
        if (t.side !== "BUY") continue;
        const stratTrade: StratTraderTrade = {
          ...t, trader: addr, weight, weightFraction,
          copyRatio: 0, notional: t.price * t.size,
        };
        // Same pre-filter the live engine applies — a filtered BUY never
        // enters the rank race, so it drops out of the backtest curve too.
        if (!strat.shouldMirror(stratTrade)) continue;
        const score = strat.scoreCandidate(stratTrade, stats);
        buys.push({ id: t.id, ts: t.timestamp, score });
      }
    }
    const buckets = new Map<number, Cand[]>();
    for (const c of buys) {
      const b = Math.floor(c.ts / cycleBucketMs);
      let arr = buckets.get(b);
      if (!arr) { arr = []; buckets.set(b, arr); }
      arr.push(c);
    }
    const kept = new Set<string>();
    const cap = strat.maxPerCycle();
    for (const cands of buckets.values()) {
      cands.sort((a, b) => b.score - a.score);
      for (let i = 0; i < cands.length && i < cap; i++) {
        if (cands[i].score > 0) kept.add(cands[i].id);
      }
    }
    return kept;
  }, [watchlist, traderTrades, traderStatsMap, rebalanceMinutes, backtestDays, backtestStrat, traderWeights]);

  // ── Combined FIFO PnL curve (scaled to user's capital) ──
  const combinedCurveData = useMemo((): { combined: CurvePoint[]; perTrader: { address: string; points: CurvePoint[]; weight: number }[] } => {
    if (watchlist.length === 0 || loading) return { combined: [], perTrader: [] };
    const cutoffMs = Date.now() - backtestDays * 24 * 60 * 60 * 1000;
    const traderCurves: { address: string; points: CurvePoint[]; weight: number }[] = [];

    for (const addr of watchlist) {
      // Honor the strat's market-topic filter — same gate `backtests` and
      // `traderStatsMap` apply. Without this, a SELL outside the query (or
      // BUYs that never went through `keptBuyIds`) still leaked into the
      // curve, so the chart didn't match the filtered preview numbers.
      const rawTrades = (traderTrades.get(addr) || [])
        .filter((t) => marketMatchesQuery(t.market, marketQuery));
      const positions = traderData.get(addr) || [];
      if (rawTrades.length === 0) continue;

      const replayTradesRaw = rebalancePeriod > 0
        ? aggregateToRebalanceWindows(rawTrades, rebalancePeriod, rebalanceHour)
        : rawTrades;
      // Apply SAMPLE % only to in-window trades — pre-window trades stay
      // intact so FIFO basis tracking still sees the prior inventory.
      const replaySampled = samplePct >= 100
        ? replayTradesRaw
        : replayTradesRaw.filter((t, i) =>
            t.timestamp < cutoffMs ||
            keepInSample(samplePct, `${addr}:${t.timestamp}:${i}`),
          );
      // Top-N Sharpe sampling — drop in-window BUYs that lost the per-cycle
      // rank race. SELLs always kept (close existing positions). Pre-window
      // trades pass through so FIFO basis stays intact.
      const replayTrades = rebalancePeriod > 0
        ? replaySampled
        : replaySampled.filter((t) =>
            t.timestamp < cutoffMs || t.side === "SELL" || keptBuyIds.has(t.id),
          );
      const annotated = computeFifoTrades(replayTrades, positions, cutoffMs);
      const curve = buildPnlCurve(annotated, positions, cutoffMs);
      if (curve.length === 0) continue;

      // Scale PnL curve to user's capital allocation for this trader.
      // Use max(buyVol, sellVol) as denominator — prevents amplification when
      // a trader mostly sells old positions in the window (tiny buyVol but huge PnL).
      const wFrac = totalWeight > 0 ? (traderWeights[addr] || 0) / totalWeight : 1 / watchlist.length;
      const windowTrades = replayTrades.filter((t) => t.timestamp >= cutoffMs);
      const buyVol = windowTrades.filter((t) => t.side === "BUY").reduce((s, t) => s + t.price * t.size, 0);
      const sellVol = windowTrades.filter((t) => t.side === "SELL").reduce((s, t) => s + t.price * t.size, 0);
      const traderVol = Math.max(buyVol, sellVol, 1);
      const capitalScale = (capital * wFrac) / traderVol;
      traderCurves.push({ address: addr, points: curve, weight: capitalScale });
    }

    return { combined: buildCombinedPnlCurve(traderCurves), perTrader: traderCurves };
  }, [watchlist, traderTrades, traderData, backtestDays, traderWeights, totalWeight, capital, loading, rebalancePeriod, rebalanceHour, samplePct, keptBuyIds, marketQuery]);

  const combinedPnlCurve = combinedCurveData.combined;

  // Chart-derived summary numbers. `weightedPnlAfterCosts` / `combinedRoi1k`
  // use cashflow-only `estimatedPnl = sellVol - buyVol`, which under-reports
  // performance whenever the window has more buys than sells (those buys are
  // open positions with MTM value that the chart picks up but the cashflow
  // proxy doesn't). Header + snapshot now match the curve.
  const chartGrossPnl = combinedPnlCurve.length > 0
    ? combinedPnlCurve[combinedPnlCurve.length - 1].pnl
    : 0;
  const chartNetPnl = chartGrossPnl - totalCosts;
  const chartRoi = capital > 0 ? (chartNetPnl / capital) * 100 : 0;

  // ── Persist backtest snapshot to SavedIndex for leaderboard ──
  // (Moved below combinedPnlCurve so it can reference chart-derived values.)
  useEffect(() => {
    if (!activeIndex || backtests.length === 0 || loading) return;
    const snap = {
      lastPnl: Math.round(chartGrossPnl * 100) / 100,
      lastPnlAfterCosts: Math.round(chartNetPnl * 100) / 100,
      lastRoi1k: Math.round(chartRoi * 100) / 100,
      lastTradeCount: totalTradeCount,
      lastBacktestAt: Date.now(),
    };
    updateIndex(activeIndex.id, snap);
  }, [activeIndex, chartGrossPnl, chartNetPnl, chartRoi, totalTradeCount, loading, backtests.length]);

  // Per-trader scaled MTM P&L from curves (consistent with chart)
  const traderCurvePnl = useMemo(() => {
    const map = new Map<string, number>();
    for (const tc of combinedCurveData.perTrader) {
      if (tc.points.length === 0) continue;
      const lastPnl = tc.points[tc.points.length - 1].pnl;
      map.set(tc.address, Math.round(lastPnl * tc.weight * 100) / 100);
    }
    return map;
  }, [combinedCurveData]);

  // ── Linked trades: every trade with its running P&L impact ──
  interface LinkedTrade {
    ts: number;
    market: string;
    trader: string;
    side: "BUY" | "SELL";
    amount: number;       // scaled to user capital ($)
    price: number;        // trade price (0-1)
    fee: number;          // trading fee ($)
    realized: number;     // realized PnL on SELL (scaled)
    runningPnl: number;   // combined running P&L after this trade
    pnlDelta: number;     // change in P&L from previous trade
    /** Sharpe-weighted EV score the live engine would have assigned this
        candidate when it was eligible. Undefined for SELLs (always honored)
        and for BUYs from traders without enough 30d closed trades. */
    score?: number;
    sharpe?: number;
  }

  const linkedTrades = useMemo((): LinkedTrade[] => {
    if (watchlist.length === 0 || loading) return [];
    const cutoffMs = Date.now() - backtestDays * 86400_000;

    // Build from raw trades directly (not curve points) to use conditionId for filtering
    type RawEntry = {
      ts: number; market: string; conditionId: string; trader: string;
      side: "BUY" | "SELL"; size: number; price: number; realized: number; scale: number;
      score?: number; sharpe?: number;
    };
    const allEntries: RawEntry[] = [];

    for (const addr of watchlist) {
      // Same market-topic gate as `backtests` / `combinedCurveData` — the
      // feed must only ever show trades the strat would actually mirror.
      const rawTrades = (traderTrades.get(addr) || [])
        .filter((t) => marketMatchesQuery(t.market, marketQuery));
      const positions = traderData.get(addr) || [];
      if (rawTrades.length === 0) continue;

      const replayTrades = rebalancePeriod > 0
        ? aggregateToRebalanceWindows(rawTrades, rebalancePeriod, rebalanceHour)
        : rawTrades;
      // Strict-in-window FIFO: a copy-trader starting at cutoffMs has no
      // pre-window inventory, so SELLs that would consume pre-window basis
      // shouldn't appear in the feed (they're not actually replicable).
      const inWindowAll = replayTrades.filter((t) => t.timestamp >= cutoffMs);
      // Apply SAMPLE % so the feed matches the chart's sampled trade set.
      const inWindowSampled = samplePct >= 100
        ? inWindowAll
        : inWindowAll.filter((t, i) =>
            keepInSample(samplePct, `${addr}:${t.timestamp}:${i}`),
          );
      // Same top-N Sharpe filter the curve uses — keeps the feed in sync.
      const inWindowTrades = rebalancePeriod > 0
        ? inWindowSampled
        : inWindowSampled.filter((t) =>
            t.side === "SELL" || keptBuyIds.has(t.id),
          );
      if (inWindowTrades.length === 0) continue;
      const annotated = computeFifoTrades(inWindowTrades, positions, cutoffMs);
      const windowAnnotated = annotated.filter((t) => t.side === "BUY" || t.hasBasis);
      if (windowAnnotated.length === 0) continue;

      const wFrac = totalWeight > 0 ? (traderWeights[addr] || 0) / totalWeight : 1 / watchlist.length;
      const buyVol = windowAnnotated.filter((t) => t.side === "BUY").reduce((s, t) => s + t.price * t.size, 0);
      const sellVol = windowAnnotated.filter((t) => t.side === "SELL").reduce((s, t) => s + t.price * t.size, 0);
      const traderVol = Math.max(buyVol, sellVol, 1);
      const scale = (capital * wFrac) / traderVol;

      // Score via the SAME strat instance used live + by keptBuyIds — so
      // dropping in a new strat changes BUY scores in the chart tooltip
      // and feed without any extra wiring.
      const stats = traderStatsMap.get(addr) ?? null;
      const sharpe = stats?.sharpe ?? 0;
      const totalW = watchlist.reduce((s, a) => s + (traderWeights[a] || 0), 0) || 1;
      const weight = traderWeights[addr] || 0;
      const weightFraction = weight / totalW;
      for (const t of windowAnnotated) {
        let score: number | undefined;
        if (t.side === "BUY") {
          const stratTrade: StratTraderTrade = {
            ...t, trader: addr, weight, weightFraction,
            copyRatio: 0, notional: t.price * t.size,
          };
          const s = backtestStrat.scoreCandidate(stratTrade, stats);
          if (s > 0) score = s;
        }
        allEntries.push({
          ts: t.timestamp, market: t.market, conditionId: t.conditionId || t.market,
          trader: addr, side: t.side, size: t.size, price: t.price,
          realized: t.realized, scale,
          score,
          sharpe: sharpe > 0 ? sharpe : undefined,
        });
      }
    }

    allEntries.sort((a, b) => a.ts - b.ts);

    // Compute derived fields per trade. Apply TRADE SIZE constraints so the
    // displayed feed matches what the LIVE engine would actually do:
    //   • amount < minTrade → skip entirely (dust order, no place)
    //   • amount > maxTrade → clamp amount to maxTrade; SELL's realized P&L
    //     scales down proportionally (capped trade ≠ free capped P&L)
    // Then accumulate running P&L only over surviving trades, so the curve
    // value at any row matches the column shown.
    const derived: {
      ts: number; market: string; trader: string; side: "BUY" | "SELL";
      amount: number; price: number; fee: number; realized: number; pnlDelta: number;
      score?: number; sharpe?: number;
    }[] = [];
    for (const t of allEntries) {
      const rawAmount = t.price * t.size * t.scale;
      // Skip dust trades — below the user-configured min size they wouldn't
      // be placed by the live engine; showing them in the feed misleads.
      if (!showAllTrades && rawAmount < minTrade) continue;
      // Cap at max size; SELL realized P&L scales down because the smaller
      // clamped position can only return a smaller absolute gain/loss.
      // SHOW ALL skips both gates so the displayed amount is whatever the
      // raw scaled mirror would be (no floor, no ceiling).
      const clampRatio = showAllTrades || rawAmount <= maxTrade ? 1 : maxTrade / rawAmount;
      // Keep full precision so sub-cent mirrors don't collapse to $0.00 in
      // the table. The renderer formats with the right precision for tiny
      // values (e.g. "0.42¢" instead of "0.00¢"); rounding here was the
      // source of the "every row is $0.00" bug.
      const amount = showAllTrades ? rawAmount : Math.min(rawAmount, maxTrade);
      const realizedScaled = t.side === "SELL"
        ? Math.round(t.realized * t.scale * clampRatio * 100) / 100
        : 0;
      const scaledShares = t.price > 0 ? amount / t.price : 0;
      const fee = Math.round(scaledShares * Math.min(t.price, 1 - t.price) * (TAKER_FEE_BPS / 10_000) * 100) / 100;
      derived.push({
        ts: t.ts,
        market: t.market,
        trader: t.trader,
        side: t.side,
        amount,
        price: t.price,
        fee,
        realized: realizedScaled,
        pnlDelta: realizedScaled,
        score: t.score,
        sharpe: t.sharpe,
      });
    }

    let runningPnl = 0;
    return derived.map((t) => {
      runningPnl = Math.round((runningPnl + t.realized) * 100) / 100;
      return { ...t, runningPnl };
    });
  }, [watchlist, traderTrades, traderData, backtestDays, traderWeights, totalWeight, capital, loading, rebalancePeriod, rebalanceHour, samplePct, minTrade, maxTrade, showAllTrades, keptBuyIds, traderStatsMap, backtestStrat, marketQuery]);

  // liveCurve — built from the engine's actual order log. Each successful
  // COPY_BUY / COPY_SELL becomes a point. P&L is a FIFO-realized running
  // total: BUYs add to per-token basis queues, SELLs pop the oldest BUY at
  // its filled price and compute (sell - buy) × shares. Only what actually
  // executed on Polymarket is plotted — no historical replay.
  const liveCurve = useMemo((): CurvePoint[] => {
    if (!engineState) return [];
    const trades = engineState.log
      .filter((e) => (e.type === "COPY_BUY" || e.type === "COPY_SELL") && e.orderResult?.success)
      .sort((a, b) => a.timestamp - b.timestamp);
    if (trades.length === 0) return [];

    // Per-token FIFO basis. Each entry is (price, shares).
    const basis: Map<string, Array<[number, number]>> = new Map();
    let running = 0;
    const out: CurvePoint[] = [];
    for (let i = 0; i < trades.length; i++) {
      const t = trades[i];
      const side = t.side ?? "BUY";
      const price = t.price ?? 0;
      const notional = t.mirrorNotional ?? 0;
      const shares = price > 0 ? notional / price : 0;
      const tokenId = t.tokenId ?? t.conditionId ?? t.market ?? "";
      let realized = 0;
      if (side === "BUY") {
        const q = basis.get(tokenId) ?? [];
        q.push([price, shares]);
        basis.set(tokenId, q);
      } else {
        // Pop earliest BUYs to realize PnL.
        let remaining = shares;
        const q = basis.get(tokenId) ?? [];
        while (remaining > 0 && q.length > 0) {
          const [bp, bs] = q[0];
          const consumed = Math.min(bs, remaining);
          realized += (price - bp) * consumed;
          remaining -= consumed;
          if (consumed >= bs) q.shift();
          else q[0] = [bp, bs - consumed];
        }
        basis.set(tokenId, q);
      }
      running = Math.round((running + realized) * 100) / 100;
      const d = new Date(t.timestamp);
      out.push({
        i,
        ts: t.timestamp,
        date: `${d.getUTCMonth() + 1}/${d.getUTCDate()}`,
        time: `${d.getUTCHours().toString().padStart(2, "0")}:${d.getUTCMinutes().toString().padStart(2, "0")}`,
        pnl: running,
        side,
        realized,
        market: t.market ?? "",
        size: notional,
        price,
      });
    }
    return out;
  }, [engineState]);

  // chartCurve — what PnlChart actually plots.
  //   LIVE mode  → liveCurve (engine's real executed orders)
  //   BACKTEST   → linkedTrades-derived curve (constrained replay) or
  //                combinedPnlCurve when SHOW ALL is on
  // The two modes never share a series — historical replay is meaningless
  // when monitoring live, and the live order log doesn't exist on BACKTEST.
  const chartCurve = useMemo((): CurvePoint[] => {
    if (mode === "LIVE") return liveCurve;
    if (showAllTrades) return combinedPnlCurve;
    if (linkedTrades.length === 0) return combinedPnlCurve;
    return linkedTrades.map((t, i) => {
      const d = new Date(t.ts);
      return {
        i,
        ts: t.ts,
        date: `${d.getUTCMonth() + 1}/${d.getUTCDate()}`,
        time: `${d.getUTCHours().toString().padStart(2, "0")}:${d.getUTCMinutes().toString().padStart(2, "0")}`,
        pnl: t.runningPnl,
        side: t.side,
        realized: t.realized,
        market: t.market,
        size: t.amount,
        price: t.price,
      };
    });
  }, [mode, liveCurve, linkedTrades, combinedPnlCurve, showAllTrades]);

  // ── Chart ↔ Trade feed hover linking ──
  const [chartHighlight, setChartHighlight] = useState<number | null>(null);
  const [tradeHighlight, setTradeHighlight] = useState<number | null>(null);
  // Pinned trade selection — survives mouse-leave (unlike the hover-only
  // chartHighlight/tradeHighlight pair above, which reset on every
  // mouseleave). Click a row to pin it; click again to unpin. Indexes into
  // `linkedTrades` (the origIdx used by the feed table below).
  const [selectedTradeIdx, setSelectedTradeIdx] = useState<number | null>(null);
  // Lowercase trader address → isolate the feed to just this trader's trades.
  // `null` shows everyone (the default). Click a trader chip or a trader cell
  // in the feed to set; click again or the ALL chip to clear.
  const [feedTraderFilter, setFeedTraderFilter] = useState<string | null>(null);
  const [indexTradeLimit, setIndexTradeLimit] = useState(100);
  const [feedOrder, setFeedOrder] = useState<"newest" | "oldest">("newest");

  // Both hover-linking helpers index into the SAME series PnlChart plots
  // (chartCurve), not the unfiltered combinedPnlCurve. Mixing the two
  // crashes when the chart plots N points but combinedPnlCurve has fewer
  // — clicking a chart dot tries combinedPnlCurve[idx] which is undefined.
  const findCurveIdx = useCallback((ts: number): number | null => {
    if (chartCurve.length === 0) return null;
    let best = 0, bestDist = Infinity;
    for (let i = 0; i < chartCurve.length; i++) {
      const d = Math.abs(chartCurve[i].ts - ts);
      if (d < bestDist) { bestDist = d; best = i; }
    }
    return best;
  }, [chartCurve]);

  const handleChartHover = useCallback((idx: number | null) => {
    if (idx === null || chartCurve.length === 0 || linkedTrades.length === 0) {
      setTradeHighlight(null);
      return;
    }
    const point = chartCurve[idx];
    if (!point) { setTradeHighlight(null); return; }
    const ts = point.ts;
    let best = 0, bestDist = Infinity;
    for (let i = 0; i < linkedTrades.length; i++) {
      const d = Math.abs(linkedTrades[i].ts - ts);
      if (d < bestDist) { bestDist = d; best = i; }
    }
    setTradeHighlight(best);
  }, [chartCurve, linkedTrades]);

  // Drop a pinned selection once the feed it pointed into is rebuilt out
  // from under it (strat switch, market filter edit, new data fetched) —
  // otherwise selectedTradeIdx can point at a now-unrelated row.
  useEffect(() => {
    if (selectedTradeIdx !== null && selectedTradeIdx >= linkedTrades.length) {
      setSelectedTradeIdx(null);
    }
  }, [linkedTrades, selectedTradeIdx]);

  // ── Backtest date range ──
  const backtestDateRange = useMemo(() => {
    const now = new Date();
    const from = new Date(Date.now() - backtestDays * 24 * 60 * 60 * 1000);
    const fmt = (d: Date) => d.toLocaleDateString([], { month: "short", day: "numeric", timeZone: "UTC" });
    return { from: fmt(from), to: fmt(now) };
  }, [backtestDays]);

  // Lag derivations — surfaces the freshness of the data the engine is
  // basing decisions on. fetchAgeMs is how long since we last polled
  // Polymarket; lastTradeAgeMs is the age of the most recent observed
  // trade across the watchlist. A growing lastTradeAgeMs while fetchAgeMs
  // stays small means the watched traders are quiet (no live signal);
  // a growing fetchAgeMs means OUR poll loop is stalled.
  const fetchAgeMs = lastUpdated ? utcNow - lastUpdated : null;
  const lastTradeTs = useMemo(() => {
    let max = 0;
    for (const trades of traderTrades.values()) {
      for (const t of trades) if (t.timestamp > max) max = t.timestamp;
    }
    return max;
  }, [traderTrades]);
  const lastTradeAgeMs = lastTradeTs > 0 ? utcNow - lastTradeTs : null;

  // ── Trader summaries (all traders including hidden, for editor panel) ──
  const traderSummaries: (TraderSummary & { enabled: boolean })[] = useMemo(() => {
    return allTraderAddrs.map((addr) => {
      const positions = traderData.get(addr) || [];
      const q = searchFilter.trim().toLowerCase();
      const filtered = q ? positions.filter((p) => p.market.toLowerCase().includes(q)) : positions;
      const isEnabled = activeIndex?.traders.find((t) => t.address === addr)?.enabled !== false;
      return {
        address: addr,
        positions: positions.length,
        filteredPositions: filtered.length,
        totalPnl: filtered.reduce((s, p) => s + p.pnlUsd, 0),
        loaded: traderData.has(addr),
        enabled: isEnabled,
      };
    });
  }, [allTraderAddrs, traderData, searchFilter, activeIndex]);

  // Per-trader 24h activity count — surfaces dormant traders so the user
  // knows which ones in the watchlist actually contribute trades to copy.
  // Derived live from the already-fetched traderTrades map (no extra fetch);
  // re-runs whenever the 60s background refresh swaps in new traderTrades.
  const trades24hByAddr = useMemo(() => {
    const cutoffMs = Date.now() - 86_400_000;
    const m = new Map<string, number>();
    for (const addr of allTraderAddrs) {
      const trades = traderTrades.get(addr);
      if (!trades) continue;
      let c = 0;
      for (const t of trades) if (t.timestamp >= cutoffMs) c++;
      m.set(addr, c);
    }
    return m;
  }, [allTraderAddrs, traderTrades]);

  // Per-trader total trade count (across the loaded history window) +
  // timestamp of their most recent trade. Surfaces "this trader is very
  // active but went dormant 3h ago" patterns the 24h count alone can't show.
  const traderTradeStatsByAddr = useMemo(() => {
    const m = new Map<string, { total: number; lastTs: number }>();
    for (const addr of allTraderAddrs) {
      const trades = traderTrades.get(addr);
      if (!trades || trades.length === 0) continue;
      let lastTs = 0;
      for (const t of trades) if (t.timestamp > lastTs) lastTs = t.timestamp;
      m.set(addr, { total: trades.length, lastTs });
    }
    return m;
  }, [allTraderAddrs, traderTrades]);


  // ══════════════════════════════════════════
  // ── RENDER ──
  // ══════════════════════════════════════════

  return (
    <div className="min-w-0 space-y-2">
      {/* ── Header: tabs ──
          Wallet/token/QR + go-live checklist + the strat list all live in
          the permanent left SidebarShell (ACCOUNT / STRATS tabs) — this
          column stays to the strat tabs and their content, nothing
          duplicated here. */}
      <div className="pixel-panel px-3 py-2 space-y-2">
        {/* Tabs */}
        <div className="flex items-center gap-2">
          {(
            [
              { id: "STRATS", label: "STRAT", disabled: false },
              { id: "HUB", label: "HUB", disabled: false },
              { id: "BACKTEST", label: "BACKTEST", disabled: watchlist.length === 0 },
              { id: "LIVE", label: "LIVE", disabled: watchlist.length === 0 },
            ] as { id: typeof mode; label: string; disabled: boolean }[]
          ).map((t) => {
            const active = mode === t.id;
            // Show a RUNNING chip on the LIVE tab while the engine is
            // actively trading — so the user can tell at a glance there's
            // a "task" firing in the background even from STRATS/BACKTEST.
            const showRunning = t.id === "LIVE" && isLive;
            const runningTone = engineState?.status === "paused"
              ? "border-amber-400/60 text-amber-400 bg-amber-400/10"
              : engineState?.status === "error"
                ? "border-red-400/60 text-red-400 bg-red-400/10"
                : "border-green-400/60 text-green-400 bg-green-400/10";
            return (
              <button
                key={t.id}
                onClick={() => setMode(t.id)}
                disabled={t.disabled}
                style={{ fontFamily: '"Space Grotesk", system-ui, sans-serif', letterSpacing: "0.16em" }}
                className={`relative text-[12.5px] font-bold px-4 py-2 rounded-[var(--radius-sm)] transition-all duration-150 uppercase flex items-center gap-2 ${
                  active
                    ? "text-green-400 bg-green-400/[0.08]"
                    : "text-pixel-gray hover:text-pixel-white hover:bg-pixel-white/[0.04]"
                } disabled:opacity-30 disabled:cursor-not-allowed`}
              >
                {t.label}
                {showRunning && (
                  <span
                    className={`text-[9px] px-1.5 py-0.5 border tracking-[0.1em] rounded-full font-semibold ${runningTone}`}
                    title={`Live engine ${engineState?.status ?? "running"}`}
                  >
                    {engineState?.status === "paused" ? "PAUSED" :
                     engineState?.status === "error" ? "ERROR" : "RUNNING"}
                  </span>
                )}
                <span
                  className={`absolute left-3 right-3 -bottom-px h-[2px] rounded-full transition-all duration-200 ${
                    active ? "bg-green-400 opacity-100 shadow-[0_0_10px_rgba(74,222,128,0.7)]" : "opacity-0"
                  }`}
                />
              </button>
            );
          })}
        </div>

        {/* ── STRAT subtabs ──
            SOURCE = the strat's code, TRADERS = leaderboard + watchlist
            editor, PARAMS = every tuning knob. The old always-docked
            params panel is gone — params render here and nowhere else. */}
        {mode === "STRATS" && (
          <div className="flex items-center gap-1.5 border-t border-pixel-border/40 pt-2">
            {(
              [
                { id: "SOURCE", label: "SOURCE", hint: "Strat code — read built-ins, edit your uploads" },
                { id: "TRADERS", label: "TRADERS", hint: "Browse the leaderboard + manage who you copy" },
                { id: "PARAMS", label: "PARAMS", hint: "Window, capital, trade band, throttle, market focus, trade filters" },
              ] as { id: typeof stratTab; label: string; hint: string }[]
            ).map((t) => {
              const active = stratTab === t.id;
              return (
                <button
                  key={t.id}
                  onClick={() => setStratTab(t.id)}
                  title={t.hint}
                  className={`text-[11px] font-mono tracking-[0.15em] px-3 py-1 rounded border transition-colors ${
                    active
                      ? "border-green-400/60 text-green-400 bg-green-400/[0.08]"
                      : "border-pixel-border text-pixel-gray hover:text-pixel-white hover:border-pixel-white/40"
                  }`}
                >
                  {t.label}
                </button>
              );
            })}
          </div>
        )}
      </div>

      {/* ── PARAMS subtab (STRAT tab only) ──
          All strat tuning in one place: window / capital / trade band /
          throttle / top-N / sample / poll cadence / market focus / per-trade
          filters. The status row keeps the strat picker + data-freshness
          chips so a stalled poll loop is still diagnosable from here. */}
      {mode === "STRATS" && stratTab === "PARAMS" && activeIndex && (
        <div className="pixel-panel px-3 py-2.5 space-y-2.5">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-[14px] text-pixel-white tracking-[0.2em] shrink-0">PARAMS</span>
            <div className="w-2 h-2 bg-green-400 shrink-0" />
            <select
              value={activeIndex.id}
              onChange={(e) => selectStrategy(e.target.value)}
              className="bg-transparent text-[14px] font-mono text-green-400 font-bold truncate outline-none border-none cursor-pointer hover:underline pr-1"
              title="Switch active strat"
            >
              {savedIndexes.map((s) => (
                <option key={s.id} value={s.id} className="bg-pixel-bg text-pixel-white">
                  {s.name}
                </option>
              ))}
            </select>
            <span className="text-[13px] text-pixel-gray shrink-0">{activeIndex.traders.length}T</span>
            {activeIndex.lastPnlAfterCosts !== undefined && (
              <span className={`text-[13px] font-mono shrink-0 ${activeIndex.lastPnlAfterCosts >= 0 ? "text-green-400" : "text-red-400"}`}>
                {activeIndex.lastPnlAfterCosts >= 0 ? "+" : ""}${activeIndex.lastPnlAfterCosts.toFixed(0)}
              </span>
            )}
            <span className="text-[12px] text-pixel-gray/70 font-mono tracking-wider truncate">
              {backtestDateRange.from} → {backtestDateRange.to}
            </span>
            <span className="text-pixel-border/40 shrink-0">·</span>
            <span className="text-[11px] text-pixel-gray font-mono shrink-0" title="Current time (UTC)">
              UTC {new Date(utcNow).toISOString().slice(11, 19)}
            </span>
            {lastUpdated && fetchAgeMs != null && (
              <>
                <span className="text-pixel-border/40 shrink-0">·</span>
                <span
                  className={`text-[11px] font-mono shrink-0 ${
                    fetchAgeMs < 90_000 ? "text-pixel-gray/70"
                      : fetchAgeMs < 5 * 60_000 ? "text-amber-400"
                      : "text-red-400"
                  }`}
                  title={`Watchlist trades last fetched at ${new Date(lastUpdated).toISOString()} — polling interval is 60s`}
                >
                  UPD {formatAgoShort(fetchAgeMs)} ago
                </span>
              </>
            )}
            {lastTradeAgeMs != null && (
              <>
                <span className="text-pixel-border/40 shrink-0">·</span>
                <span
                  className={`text-[11px] font-mono shrink-0 ${
                    lastTradeAgeMs < 5 * 60_000 ? "text-green-400"
                      : lastTradeAgeMs < 30 * 60_000 ? "text-pixel-gray-light"
                      : "text-pixel-gray"
                  }`}
                  title={`Most recent observed trade across watched traders: ${new Date(lastTradeTs).toISOString()}`}
                >
                  LAST TRADE {formatAgoShort(lastTradeAgeMs)} ago
                </span>
              </>
            )}
          </div>
          <div className="border-t border-pixel-border/40 pt-2.5">
            <div className="flex items-end gap-2 flex-wrap">
              <Field label="WINDOW" suffix="DAYS">
                <input
                  type="text"
                  inputMode="numeric"
                  value={backtestDaysInput}
                  onChange={(e) => setBacktestDaysInput(e.target.value)}
                  onBlur={() => {
                    const v = parseInt(backtestDaysInput, 10);
                    if (!isNaN(v) && v > 0 && v <= 365) {
                      updateBacktestDays(v);
                      setBacktestDaysInput(String(v));
                    } else {
                      setBacktestDaysInput(String(backtestDays));
                    }
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      const v = parseInt(backtestDaysInput, 10);
                      if (!isNaN(v) && v > 0 && v <= 365) {
                        updateBacktestDays(v);
                        setRefreshKey((k) => k + 1);
                      }
                    }
                  }}
                  onFocus={(e) => e.target.select()}
                  className="bg-transparent w-10 text-right font-mono text-[14px] text-pixel-white outline-none"
                />
              </Field>

              <Field label="CAPITAL" prefix="$">
                <input
                  type="text"
                  inputMode="numeric"
                  value={capital}
                  onChange={(e) => {
                    const v = parseInt(e.target.value.replace(/[^0-9]/g, ""), 10);
                    if (!isNaN(v) && v > 0) updateCapital(v);
                  }}
                  onFocus={(e) => e.target.select()}
                  className="bg-transparent w-16 text-right font-mono text-[14px] text-pixel-white outline-none"
                />
              </Field>

              <Field label="TRADE SIZE" prefix="$">
                <input
                  type="text"
                  inputMode="numeric"
                  value={minTrade}
                  onChange={(e) => {
                    const v = parseInt(e.target.value.replace(/[^0-9]/g, ""), 10);
                    if (!isNaN(v) && v >= 0) updateMinTrade(v);
                  }}
                  onFocus={(e) => e.target.select()}
                  className="bg-transparent w-10 text-right font-mono text-[14px] text-pixel-white outline-none"
                />
                <span className="text-pixel-gray/60 mx-0.5">–</span>
                <input
                  type="text"
                  inputMode="numeric"
                  value={maxTrade}
                  onChange={(e) => {
                    const v = parseInt(e.target.value.replace(/[^0-9]/g, ""), 10);
                    if (!isNaN(v) && v > 0) updateMaxTrade(v);
                  }}
                  onFocus={(e) => e.target.select()}
                  className="bg-transparent w-10 text-right font-mono text-[14px] text-pixel-white outline-none"
                />
              </Field>

              <Field label="THROTTLE" suffix="/HR">
                <input
                  type="text"
                  inputMode="numeric"
                  value={maxTradesPerHour}
                  onChange={(e) => {
                    const v = parseInt(e.target.value.replace(/[^0-9]/g, ""), 10);
                    if (!isNaN(v) && v > 0) updateMaxTradesPerHour(v);
                  }}
                  onFocus={(e) => e.target.select()}
                  className="bg-transparent w-8 text-right font-mono text-[14px] text-pixel-white outline-none"
                />
              </Field>

              <Field label="MAX/CYCLE">
                <input
                  type="text"
                  inputMode="numeric"
                  value={maxPerCycle}
                  onChange={(e) => {
                    const v = parseInt(e.target.value.replace(/[^0-9]/g, ""), 10);
                    if (!isNaN(v) && v > 0) updateMaxPerCycle(v);
                  }}
                  onFocus={(e) => e.target.select()}
                  title="Top-N candidates copied per scan (by score)"
                  className="bg-transparent w-8 text-right font-mono text-[14px] text-pixel-white outline-none"
                />
              </Field>

              <Field label="SAMPLE" suffix="%">
                <input
                  type="text"
                  inputMode="numeric"
                  value={samplePct}
                  onChange={(e) => {
                    const v = parseInt(e.target.value.replace(/[^0-9]/g, ""), 10);
                    if (!isNaN(v) && v >= 1 && v <= 100) setSamplePct(v);
                    else if (e.target.value === "") setSamplePct(1);
                  }}
                  onFocus={(e) => e.target.select()}
                  title="Deterministically keep this % of in-window trades — the curve, feed, and stats all update. Quick chips below set 10/25/50/75/100%."
                  className="bg-transparent w-8 text-right font-mono text-[14px] text-pixel-white outline-none"
                />
              </Field>

              <Field label="POLL EVERY">
                <select
                  value={rebalanceMinutes}
                  onChange={(e) => updateRebalanceMinutes(Number(e.target.value))}
                  className="bg-transparent font-mono text-[13px] text-pixel-white outline-none cursor-pointer pr-1"
                >
                  <option value={5 / 60}>5s</option>
                  <option value={10 / 60}>10s</option>
                  <option value={15 / 60}>15s</option>
                  <option value={30 / 60}>30s</option>
                  <option value={1}>1m</option>
                  <option value={2}>2m</option>
                  <option value={5}>5m</option>
                  <option value={10}>10m</option>
                  <option value={15}>15m</option>
                  <option value={30}>30m</option>
                  <option value={60}>1h</option>
                  <option value={240}>4h</option>
                  <option value={1440}>24h</option>
                </select>
              </Field>

              {/* Market-topic filter — keeps a strat focused (e.g. "price of
                  bitcoin") instead of copying every market a trader touches.
                  Commits on blur / Enter so the backtest doesn't re-run per
                  keystroke. The local `marketQueryInput` mirror lets the user
                  type freely before committing. */}
              <Field label="MARKET">
                <input
                  type="text"
                  value={marketQueryInput}
                  onChange={(e) => setMarketQueryInput(e.target.value)}
                  onBlur={() => updateMarketQuery(marketQueryInput)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      updateMarketQuery(marketQueryInput);
                      (e.target as HTMLInputElement).blur();
                    }
                  }}
                  placeholder="all markets"
                  title="Only act on markets whose title matches this query. Comma = OR (e.g. 'bitcoin, btc'), space = AND ('price bitcoin'). Blank = every market. Applies to backtest + live."
                  className="bg-transparent w-44 font-mono text-[14px] text-pixel-white outline-none placeholder:text-pixel-gray/40"
                />
              </Field>

              {/* Quick crypto presets — one click focuses the strat on a coin,
                  OR-matching both the full name and ticker so e.g. "Bitcoin"
                  catches "BTC above $100k" too. Bitcoin-first by design. */}
              <div className="w-full flex items-center gap-1.5 flex-wrap pt-0.5">
                <span className="text-[10px] text-pixel-gray tracking-[0.18em] leading-none">FOCUS</span>
                {([
                  ["Bitcoin", "bitcoin, btc"],
                  ["Ethereum", "ethereum, eth"],
                  ["Solana", "solana, sol"],
                  ["Crypto", "bitcoin, btc, ethereum, eth, solana, sol, crypto, xrp, dogecoin"],
                ] as const).map(([label, q]) => {
                  const active = marketQueryInput.trim().toLowerCase() === q;
                  return (
                    <button
                      key={label}
                      onClick={() => { setMarketQueryInput(q); updateMarketQuery(q); }}
                      title={`Only copy markets matching: ${q}`}
                      className={`text-[10px] px-2 py-0.5 rounded border font-bold transition-colors ${
                        active
                          ? "border-amber-400/70 bg-amber-500/20 text-amber-300"
                          : "border-pixel-border bg-pixel-black/60 text-pixel-gray-light hover:border-pixel-white/40"
                      }`}
                    >
                      {label}
                    </button>
                  );
                })}
                {marketQueryInput.trim() && (
                  <button
                    onClick={() => { setMarketQueryInput(""); updateMarketQuery(""); }}
                    title="Clear the market focus — copy every market again."
                    className="text-[10px] px-2 py-0.5 rounded border border-pixel-border bg-pixel-black/60 text-pixel-gray hover:text-pixel-white hover:border-pixel-white/40"
                  >
                    ✕ all
                  </button>
                )}
              </div>

              {/* Per-trade filter — orthogonal to MARKET: gates on the
                  leader's own trade attributes (side / entry price / size /
                  category) rather than the market title. AND-ed together.
                  Backtest + live engine + catch-up all honor these. */}
              <div className="w-full flex items-center gap-2 flex-wrap pt-0.5">
                <span className="text-[10px] text-pixel-gray tracking-[0.18em] leading-none">TRADE FILTER</span>
                <span className="flex items-center gap-1">
                  {(["both", "buy", "sell"] as const).map((s) => (
                    <button
                      key={s}
                      onClick={() => patchTradeFilters({ sides: s })}
                      title="Only copy trades on this side"
                      className={`text-[10px] px-2 py-0.5 rounded border font-mono uppercase transition-colors ${
                        (tradeFilters.sides ?? "both") === s
                          ? "border-green-400 text-green-400 bg-green-400/10"
                          : "border-pixel-border text-pixel-gray hover:text-pixel-white"
                      }`}
                    >
                      {s}
                    </button>
                  ))}
                </span>
                <span className="flex items-center font-mono text-[12px] text-pixel-white" title="Only copy trades entered inside this price band (¢)">
                  <span className="text-[10px] text-pixel-gray mr-1">PRICE¢</span>
                  <input
                    type="text" inputMode="numeric" placeholder="0"
                    value={priceMinInput}
                    onChange={(e) => setPriceMinInput(e.target.value)}
                    onBlur={() => {
                      const v = priceMinInput.trim() === "" ? undefined : Math.min(100, Math.max(0, parseInt(priceMinInput, 10)));
                      patchTradeFilters({ minPrice: v !== undefined && !isNaN(v) ? v / 100 : undefined });
                    }}
                    onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }}
                    className="bg-transparent w-8 text-right outline-none border-b border-pixel-border/40 focus:border-green-400 placeholder:text-pixel-gray/40" />
                  <span className="text-pixel-gray/60 mx-0.5">–</span>
                  <input
                    type="text" inputMode="numeric" placeholder="100"
                    value={priceMaxInput}
                    onChange={(e) => setPriceMaxInput(e.target.value)}
                    onBlur={() => {
                      const v = priceMaxInput.trim() === "" ? undefined : Math.min(100, Math.max(0, parseInt(priceMaxInput, 10)));
                      patchTradeFilters({ maxPrice: v !== undefined && !isNaN(v) ? v / 100 : undefined });
                    }}
                    onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }}
                    className="bg-transparent w-8 text-right outline-none border-b border-pixel-border/40 focus:border-green-400 placeholder:text-pixel-gray/40" />
                </span>
                <span className="flex items-center font-mono text-[12px] text-pixel-white" title="Only copy trades whose notional ($) falls inside this band">
                  <span className="text-[10px] text-pixel-gray mr-1">SIZE $</span>
                  <input
                    type="text" inputMode="numeric" placeholder="0"
                    value={sizeMinInput}
                    onChange={(e) => setSizeMinInput(e.target.value)}
                    onBlur={() => {
                      const v = sizeMinInput.trim() === "" ? undefined : Math.max(0, parseInt(sizeMinInput, 10));
                      patchTradeFilters({ minNotional: v !== undefined && !isNaN(v) ? v : undefined });
                    }}
                    onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }}
                    className="bg-transparent w-10 text-right outline-none border-b border-pixel-border/40 focus:border-green-400 placeholder:text-pixel-gray/40" />
                  <span className="text-pixel-gray/60 mx-0.5">–</span>
                  <input
                    type="text" inputMode="numeric" placeholder="∞"
                    value={sizeMaxInput}
                    onChange={(e) => setSizeMaxInput(e.target.value)}
                    onBlur={() => {
                      const v = sizeMaxInput.trim() === "" ? undefined : Math.max(0, parseInt(sizeMaxInput, 10));
                      patchTradeFilters({ maxNotional: v !== undefined && !isNaN(v) ? v : undefined });
                    }}
                    onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }}
                    className="bg-transparent w-10 text-right outline-none border-b border-pixel-border/40 focus:border-green-400 placeholder:text-pixel-gray/40" />
                </span>
                <span className="flex items-center gap-1 flex-wrap">
                  {CATEGORIES.filter((c) => c.slug).map((c) => {
                    const active = (tradeFilters.categories ?? []).includes(c.slug);
                    return (
                      <button
                        key={c.slug}
                        onClick={() => toggleTradeCategory(c.slug)}
                        title="Only copy trades in the selected categories (none = all)"
                        className={`text-[9px] px-1.5 py-0.5 rounded border font-mono transition-colors ${
                          active
                            ? "border-green-400 text-green-400 bg-green-400/10"
                            : "border-pixel-border text-pixel-gray hover:text-pixel-white"
                        }`}
                      >
                        {c.label}
                      </button>
                    );
                  })}
                </span>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ── Strategy Hub (HUB tab) ──
          Your own dedicated tab: publish / community / fork (UserStratsPanel).
          FUND lives permanently in the account sidebar now — this tab stays to
          its actual job (publish/fork) instead of duplicating wallet chrome. */}
      {mode === "HUB" && (
        <div className="pixel-panel border-2 border-pixel-border">
          <div className="flex items-center justify-between px-4 py-3 border-b border-pixel-border/40">
            <span className="flex items-center gap-2.5">
              <span className="w-[3px] h-4 rounded-full bg-green-400 shadow-[0_0_8px_rgba(74,222,128,0.6)]" />
              <span
                className="text-[14px] font-bold text-pixel-white uppercase tracking-[0.18em]"
                style={{ fontFamily: '"Space Grotesk", system-ui, sans-serif' }}
              >
                Strategy Hub
              </span>
              <span className="text-[11px] text-pixel-gray hidden sm:inline tracking-wide">
                publish · fork
              </span>
            </span>
          </div>
          <div className="px-4 pb-4 space-y-3 pt-4">
            <UserStratsPanel eoa={auth.address ?? undefined} />
          </div>
        </div>
      )}

      {/* Strat list + "+ New" moved to the left StratSidebar (managed there). */}

      {/* ── SOURCE subtab — the strat's code ──
          Read-only for built-in TS strats; editable for user-uploaded
          mod.py / mod.rs files (persisted via /api/polymarket/user-strats). */}
      {mode === "STRATS" && stratTab === "SOURCE" && <StratSourceViewer />}

      {/* ── Add trader bar (STRAT → TRADERS subtab + BACKTEST) ── */}
      {(mode === "BACKTEST" || (mode === "STRATS" && stratTab === "TRADERS")) && (
        <div className="pixel-panel px-3 py-1.5">
          <div className="flex items-center gap-2">
            <div className="flex-1">
              <AddTraderBar watchlist={watchlist} onAdd={addTrader} />
            </div>
            <div className="flex items-center gap-2 shrink-0 text-[13px] font-mono">
              {loading && (
                <span className="text-[12px] text-green-400 animate-pulse">
                  {loadedCount}/{watchlist.length}
                </span>
              )}
            </div>
          </div>
        </div>
      )}

      {/* ── TRADERS subtab — browse top traders ──
          The old standalone /traders page, folded in: the full leaderboard for
          discovering who to copy, collapsible so it doesn't crowd the strat
          editor. Clicking a trader toggles them in/out of the active strat. */}
      {mode === "STRATS" && stratTab === "TRADERS" && (
        <div className="pixel-panel border-2 border-pixel-border">
          <button
            onClick={() => setBrowseOpen((v) => !v)}
            className="w-full flex items-center justify-between px-4 py-3 text-left group/bt"
          >
            <span className="flex items-center gap-2.5">
              <span className={`text-pixel-gray text-[12px] transition-transform duration-200 ${browseOpen ? "rotate-90" : ""}`}>▸</span>
              <span
                className="text-[14px] font-bold text-pixel-white uppercase tracking-[0.18em] group-hover/bt:text-green-400 transition-colors"
                style={{ fontFamily: '"Space Grotesk", system-ui, sans-serif' }}
              >
                Browse Traders
              </span>
              <span className="text-[11px] text-pixel-gray hidden sm:inline tracking-wide">
                leaderboard · click a trader to add
              </span>
            </span>
            {allTraderAddrs.length > 0 && (
              <span className="text-[11px] text-green-400 font-mono px-2 py-0.5 rounded-full bg-green-400/[0.08] border border-green-400/30">
                {allTraderAddrs.length} in strat
              </span>
            )}
          </button>
          {browseOpen && (
            <div className="px-3 pb-3 border-t border-pixel-border/40 pt-3">
              {/* Pinned leaderboards — named filter combos (e.g. "BTC ≥3/day")
                  that auto-refresh hourly (FiltersContext's reloadKey timer).
                  Click to apply, × to unpin. */}
              <div className="flex items-center gap-1.5 flex-wrap pb-3 mb-3 border-b border-pixel-border/40">
                <span className="text-[10px] text-pixel-gray tracking-[0.15em] mr-1">PINNED</span>
                {presets.map((p) => (
                  <span
                    key={p.id}
                    className="pixel-btn text-[11px] pl-2 pr-1 py-0.5 flex items-center gap-1 border-pixel-border text-pixel-gray hover:text-green-400 hover:border-green-400 transition-colors"
                  >
                    <button onClick={() => applyPreset(p)} title="Apply this leaderboard's filters">
                      {p.name}
                    </button>
                    <button
                      onClick={() => deletePreset(p.id)}
                      className="text-pixel-gray/60 hover:text-red-400"
                      title="Unpin"
                    >
                      ×
                    </button>
                  </span>
                ))}
                <button
                  onClick={saveCurrentAsPreset}
                  className="pixel-btn text-[11px] px-2 py-0.5 border-pixel-border text-pixel-gray hover:text-green-400 hover:border-green-400 transition-colors"
                  title="Pin the current filters (category / market / min trades-per-day / window) as a named leaderboard — refreshes hourly"
                >
                  + PIN CURRENT
                </button>
              </div>
              <CopyTrading
                days={browseDays}
                minTradesPerDay={browseMinTradesPerDay}
                reloadKey={browseReloadKey}
                search={searchFilter}
                category={browseCategory}
                marketQuery={browseMarketQuery}
                selectedAddresses={allTraderAddrs}
                onSelect={(addr) => {
                  const a = addr.toLowerCase();
                  // Remove using the exact stored casing (removeTrader matches
                  // case-sensitively); add new entries lowercased to match the
                  // app's storage convention.
                  const stored = allTraderAddrs.find((x) => x.toLowerCase() === a);
                  if (stored) removeTrader(stored);
                  else addTrader(a);
                }}
              />
            </div>
          )}
        </div>
      )}

      {/* ── Trader editor panel (STRAT → TRADERS subtab) ── */}
      {mode === "STRATS" && stratTab === "TRADERS" && allTraderAddrs.length > 0 && (
        <div className="pixel-panel px-3 py-2 space-y-1">
          {/* Toolbar: count + actions + weight sum */}
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span className="text-[14px] text-pixel-gray tracking-wider">{watchlist.length}/{allTraderAddrs.length} ACTIVE</span>
              <button
                onClick={equalizeWeights}
                className="pixel-btn text-[13px] px-2 py-1 border-pixel-border text-pixel-gray hover:text-green-400 hover:border-green-400 transition-colors"
                title="Equalize all weights"
              >
                EQ
              </button>
              {totalWeight !== 100 && totalWeight > 0 && (
                <button
                  onClick={normalizeWeights}
                  className="pixel-btn text-[13px] px-2 py-1 border-amber-400/60 text-amber-400 hover:bg-amber-400/10 transition-colors"
                  title="Normalize weights to 100%"
                >
                  NORM
                </button>
              )}
              <button
                onClick={resetStrat}
                className="pixel-btn text-[13px] px-2 py-1 border-red-400/60 text-red-400 hover:bg-red-400/10 transition-colors"
                title="Reset strat — clears traders, weights, and tuning back to defaults"
              >
                RESET
              </button>
            </div>
            <span className={`text-[15px] font-mono ${
              totalWeight === 100 ? "text-pixel-gray" : totalWeight > 0 ? "text-amber-400" : "text-pixel-gray"
            }`}>
              {totalWeight}%
            </span>
          </div>

          {/* Column headers */}
          <div className="flex items-center text-[12px] text-pixel-gray tracking-wider px-0.5 border-t border-pixel-border pt-1">
            <span className="w-5 shrink-0" />
            <span className="w-5 shrink-0" />
            <span className="flex-1">ADDRESS</span>
            <span className="w-12 text-right" title="Total trades observed in the loaded history window">TXS</span>
            <span className="w-14 text-right" title="Time since this trader's most recent trade">LAST</span>
            <span className="w-10 text-right" title="Trades by this trader in the last 24h — 0 means dormant">24H</span>
            <span className="w-16 text-right">P&L</span>
            <span className="flex-1 text-center">WEIGHT</span>
            <span className="w-5" />
          </div>

          {/* Trader rows */}
          <div className="space-y-0">
            {traderSummaries.map((t, i) => {
              const curvePnl = traderCurvePnl.get(t.address);
              const displayPnl = curvePnl ?? t.totalPnl;
              const pnlColor = displayPnl > 0 ? "text-green-400"
                : displayPnl < 0 ? "text-red-400" : "text-pixel-gray-light";
              const w = traderWeights[t.address] || 0;
              return (
                <div key={t.address} className={`flex items-center gap-0.5 px-0.5 py-1 hover:bg-pixel-white/5 transition-colors group border-b border-pixel-border/20 last:border-b-0 ${t.enabled ? "" : "opacity-40"}`}>
                  {/* Toggle enabled/disabled */}
                  <button
                    onClick={() => toggleTrader(t.address)}
                    className="w-4 shrink-0 flex items-center justify-center"
                    title={t.enabled ? "Hide trader" : "Show trader"}
                  >
                    <div className={`w-1.5 h-1.5 rounded-full transition-colors ${t.enabled ? "bg-green-400" : "bg-pixel-gray"}`} />
                  </button>
                  <span className="text-[12px] text-pixel-gray font-mono w-5 shrink-0 text-center">{i + 1}</span>
                  <button
                    onClick={() => goToTrader(t.address)}
                    className="flex-1 text-left text-[15px] font-mono text-pixel-white hover:text-green-400 transition-colors truncate"
                  >
                    {shortAddress(t.address)}
                  </button>
                  {/* TXS — total trade count in the loaded history window.
                      Tooltips show the absolute number; cell rendered with
                      a "K" suffix for large counts to keep the column tight. */}
                  {(() => {
                    const stats = traderTradeStatsByAddr.get(t.address);
                    const total = stats?.total ?? null;
                    return (
                      <span
                        className="w-12 text-right text-[13px] font-mono text-pixel-gray-light"
                        title={total === null ? "Loading…" : `${total.toLocaleString()} total trades observed`}
                      >
                        {total === null ? "…" : total >= 1000 ? `${(total / 1000).toFixed(1)}k` : String(total)}
                      </span>
                    );
                  })()}
                  {/* LAST — short-form "5m" / "3h" / "2d" since the most
                      recent trade. Red when stale (>24h), amber when warming
                      (>1h), green when fresh. */}
                  {(() => {
                    const stats = traderTradeStatsByAddr.get(t.address);
                    if (!stats) {
                      return (
                        <span className="w-14 text-right text-[13px] font-mono text-pixel-gray">…</span>
                      );
                    }
                    const ageMs = Date.now() - stats.lastTs;
                    const cls =
                      ageMs < 3_600_000 ? "text-green-400" :
                      ageMs < 86_400_000 ? "text-amber-400" :
                      "text-red-400";
                    const short = (() => {
                      const sec = Math.floor(ageMs / 1000);
                      if (sec < 60) return `${sec}s`;
                      const m = Math.floor(sec / 60);
                      if (m < 60) return `${m}m`;
                      const h = Math.floor(m / 60);
                      if (h < 24) return `${h}h`;
                      return `${Math.floor(h / 24)}d`;
                    })();
                    return (
                      <span
                        className={`w-14 text-right text-[13px] font-mono ${cls}`}
                        title={`Last trade ${new Date(stats.lastTs).toLocaleString()}`}
                      >
                        {short}
                      </span>
                    );
                  })()}
                  {/* 24H activity — red when dormant, amber when thin,
                      green when active. Pure visual cue, no filtering. */}
                  {(() => {
                    const c24 = trades24hByAddr.get(t.address);
                    const activityCls = c24 === undefined
                      ? "text-pixel-gray"
                      : c24 === 0
                        ? "text-red-400"
                        : c24 < 3
                          ? "text-amber-400"
                          : "text-green-400";
                    const title = c24 === undefined
                      ? "Loading trade history…"
                      : c24 === 0
                        ? "No trades in last 24h — trader may be dormant"
                        : `${c24} trade${c24 === 1 ? "" : "s"} in last 24h`;
                    return (
                      <span
                        className={`w-10 text-right text-[13px] font-mono ${activityCls}`}
                        title={title}
                      >
                        {c24 === undefined ? "…" : c24}
                      </span>
                    );
                  })()}
                  <span className={`w-16 text-right text-[15px] font-mono ${pnlColor}`}>
                    {curvePnl !== undefined ? formatPnl(curvePnl) : t.loaded ? formatPnl(t.totalPnl) : "..."}
                  </span>
                  {/* Weight slider + value */}
                  <div className="flex-1 flex items-center gap-1 px-1">
                    <input
                      type="range"
                      min={0}
                      max={100}
                      value={w}
                      onChange={(e) => updateWeight(t.address, parseInt(e.target.value, 10))}
                      className="flex-1 h-[4px] appearance-none bg-pixel-border rounded cursor-grab accent-green-400 [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:w-3 [&::-webkit-slider-thumb]:h-3 [&::-webkit-slider-thumb]:rounded-sm [&::-webkit-slider-thumb]:bg-green-400 [&::-webkit-slider-thumb]:cursor-grab"
                    />
                    <span className="text-[14px] font-mono text-pixel-gray w-9 text-right shrink-0">{w}%</span>
                  </div>
                  <button
                    onClick={() => removeTrader(t.address)}
                    className="w-5 text-center text-[13px] text-pixel-gray hover:text-red-400 opacity-0 group-hover:opacity-100 transition-all"
                    title="Remove trader"
                  >
                    x
                  </button>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* ── Active view ── */}
      {activeIndex && (
        <>
          {/* Empty trader state */}
          {watchlist.length === 0 && mode !== "HUB" && (
            <div className="pixel-panel p-4 text-center space-y-2">
              <div className="text-[14px] text-pixel-gray">NO TRADERS YET</div>
              <div className="text-[12px] text-pixel-gray-light">
                ADD TRADERS FROM{" "}
                <button onClick={() => { setMode("STRATS"); setStratTab("TRADERS"); }} className="text-pixel-white hover:text-green-400 transition-colors">
                  STRAT → TRADERS
                </button>.
              </div>
            </div>
          )}

          {/* ── BACKTEST panel ───────────────────────────────────────
              RUN + P&L summary + chart + fee row. Always visible on
              BACKTEST tab; LIVE has its own <LivePanel /> rendered
              elsewhere with real-time engine state. Splitting this out
              of the STRAT panel above keeps the param row scannable. */}
          {watchlist.length > 0 && mode === "BACKTEST" && (
            <div className="pixel-panel px-3 py-2.5 space-y-3">
              <div className="flex items-center justify-between gap-3 flex-wrap">
                <div className="flex items-center gap-3">
                  <span className="text-[14px] text-pixel-white tracking-[0.2em]">
                    BACKTEST
                  </span>
                  <button
                    onClick={() => {
                      const v = parseInt(backtestDaysInput, 10);
                      if (!isNaN(v) && v > 0 && v <= 365) updateBacktestDays(v);
                      setRefreshKey((k) => k + 1);
                    }}
                    className="inline-flex items-center gap-1.5 text-[12px] font-mono tracking-[0.15em] px-3 h-[24px] border border-green-400 text-green-400 hover:bg-green-400/10 active:bg-green-400/20 transition-colors"
                    title="Run backtest with current settings"
                  >
                    ▶ RUN
                  </button>
                  <div
                    className="inline-flex items-center gap-1 text-[12px] font-mono h-[24px] px-2 border border-pixel-border/60 text-pixel-gray"
                    title="Backtest window (days back from now)"
                  >
                    <span className="tracking-[0.15em]">DAYS</span>
                    <input
                      type="text"
                      inputMode="numeric"
                      value={backtestDaysInput}
                      onChange={(e) => setBacktestDaysInput(e.target.value)}
                      onBlur={() => {
                        const v = parseInt(backtestDaysInput, 10);
                        if (!isNaN(v) && v > 0 && v <= 365) {
                          updateBacktestDays(v);
                          setBacktestDaysInput(String(v));
                        } else {
                          setBacktestDaysInput(String(backtestDays));
                        }
                      }}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") {
                          const v = parseInt(backtestDaysInput, 10);
                          if (!isNaN(v) && v > 0 && v <= 365) {
                            updateBacktestDays(v);
                            setRefreshKey((k) => k + 1);
                          }
                          (e.target as HTMLInputElement).blur();
                        }
                      }}
                      onFocus={(e) => e.target.select()}
                      className="bg-transparent w-8 text-right font-mono text-[13px] text-pixel-white outline-none"
                    />
                    <div className="flex flex-col leading-none">
                      <button
                        type="button"
                        onClick={() => {
                          const next = Math.min(365, backtestDays + 1);
                          updateBacktestDays(next);
                          setBacktestDaysInput(String(next));
                        }}
                        className="text-[8px] text-pixel-gray hover:text-green-400 px-0.5"
                        title="+1 day"
                      >▲</button>
                      <button
                        type="button"
                        onClick={() => {
                          const next = Math.max(1, backtestDays - 1);
                          updateBacktestDays(next);
                          setBacktestDaysInput(String(next));
                        }}
                        className="text-[8px] text-pixel-gray hover:text-green-400 px-0.5"
                        title="-1 day"
                      >▼</button>
                    </div>
                    <div className="flex items-center gap-0.5 ml-1 border-l border-pixel-border/40 pl-1">
                      {[3, 7, 14, 30].map((d) => (
                        <button
                          key={d}
                          type="button"
                          onClick={() => {
                            updateBacktestDays(d);
                            setBacktestDaysInput(String(d));
                          }}
                          className={`text-[10px] px-1 tracking-wider ${
                            backtestDays === d
                              ? "text-green-400"
                              : "text-pixel-gray hover:text-pixel-white"
                          }`}
                          title={`${d}-day backtest window`}
                        >
                          {d}d
                        </button>
                      ))}
                    </div>
                  </div>
                </div>
                <div className="flex items-center gap-4 text-[13px] font-mono">
                  <div className="flex items-baseline gap-1.5">
                    <span className="text-[12px] text-pixel-gray tracking-[0.15em]">P&L</span>
                    <span className={chartNetPnl >= 0 ? "text-green-400" : "text-red-400"}>
                      {formatPnl(chartNetPnl)}
                    </span>
                  </div>
                  <div className="flex items-baseline gap-1.5">
                    <span className="text-[12px] text-pixel-gray tracking-[0.15em]">ROI</span>
                    <span className={chartRoi >= 0 ? "text-green-400" : "text-red-400"}>
                      {chartRoi >= 0 ? "+" : ""}{chartRoi.toFixed(1)}%
                    </span>
                  </div>
                </div>
              </div>

              {/* Fee/gas cost summary derived from the historical replay. */}
              {(() => {
                const feedFees = linkedTrades.reduce((s, t) => s + t.fee, 0);
                const feedVolume = linkedTrades.reduce((s, t) => s + t.amount, 0);
                const feedGas = linkedTrades.length * GAS_PER_TRADE_USD;
                const feedCosts = feedFees + feedGas;
                const chartPnl = combinedPnlCurve.length > 0 ? combinedPnlCurve[combinedPnlCurve.length - 1].pnl : 0;
                const feedPnl = linkedTrades.length > 0 ? linkedTrades[linkedTrades.length - 1].runningPnl : 0;
                const grossPnl = chartPnl || feedPnl;
                const netPnl = grossPnl - feedCosts;
                const costWarning = feedCosts > 5 && (grossPnl <= 0 || feedCosts > grossPnl * 0.5);
                return (
                  <>
                    <div className="flex items-center justify-between flex-wrap gap-3 text-[13px] font-mono border-t border-pixel-border/40 pt-2">
                      <div className="flex items-center gap-4">
                        <div className="flex items-baseline gap-1.5" title="Total notional traded across the backtest window (sum of BUY/SELL amounts)">
                          <span className="text-[12px] text-pixel-gray tracking-[0.15em]">AMOUNT</span>
                          <span className="text-pixel-white">${feedVolume.toFixed(2)}</span>
                        </div>
                        <span className="text-pixel-border/60">·</span>
                        <div className="flex items-baseline gap-1.5">
                          <span className="text-[12px] text-pixel-gray tracking-[0.15em]">FEES</span>
                          <span className="text-amber-400">${feedFees.toFixed(2)}</span>
                        </div>
                        <span className="text-pixel-border/60">·</span>
                        <div className="flex items-baseline gap-1.5">
                          <span className="text-[12px] text-pixel-gray tracking-[0.15em]">GAS</span>
                          <span className="text-amber-400">${feedGas.toFixed(2)}</span>
                        </div>
                        <span className="text-pixel-border/60">·</span>
                        <div className="flex items-baseline gap-1.5">
                          <span className="text-[12px] text-pixel-gray tracking-[0.15em]">TOTAL</span>
                          <span className="text-amber-400">${feedCosts.toFixed(2)}</span>
                          <span className="text-[12px] text-pixel-gray/70">({linkedTrades.length} TXS)</span>
                        </div>
                      </div>
                      <div className="flex items-baseline gap-1.5">
                        <span className="text-[12px] text-pixel-gray tracking-[0.15em]">GROSS</span>
                        <span className={grossPnl >= 0 ? "text-green-400/70" : "text-red-400/70"}>
                          {formatPnl(grossPnl)}
                        </span>
                      </div>
                    </div>

                    {costWarning && !loading && (
                      <div className="px-3 py-2 border border-amber-400/40 bg-amber-400/5">
                        <div className="text-[13px] text-amber-400 font-mono">
                          {feedCosts > grossPnl && grossPnl > 0
                            ? `FEES ($${feedCosts.toFixed(0)}) EXCEED GROSS P&L (${formatPnl(grossPnl)}) — COPYING ${watchlist.length} TRADERS AT ${linkedTrades.length} TXS IS UNPROFITABLE AFTER COSTS`
                            : grossPnl <= 0
                              ? `STRAT IS NEGATIVE AND INCURS $${feedCosts.toFixed(0)} IN FEES/GAS ACROSS ${linkedTrades.length} TXS`
                              : `FEES CONSUME ${Math.round((feedCosts / grossPnl) * 100)}% OF GROSS PROFIT — CONSIDER FEWER TRADERS OR HIGHER-CONVICTION PICKS`
                          }
                        </div>
                      </div>
                    )}
                  </>
                );
              })()}

              {/* BACKTEST PnL chart — constrained replay curve from
                  linkedTrades. The LIVE branch lived here too before the
                  split; now LivePanel renders its own real-time chart and
                  this panel is BACKTEST-gated. */}
              <div ref={chartPanelRef}>
              {chartCurve.length >= 2 ? (
                <PnlChart
                  points={chartCurve}
                  dayLabel={`${backtestDays}D INDEX`}
                  tradesInWindow={chartCurve.filter((p) => p.side !== "MARK").map((p) => ({ timestamp: p.ts }))}
                  highlightIndex={chartHighlight}
                  onHoverChange={handleChartHover}
                  linkedTrades={linkedTrades}
                  shortAddress={shortAddress}
                />
              ) : !loading && watchlist.length > 0 ? (
                <div className="p-6 text-center">
                  <span className="text-[13px] text-pixel-gray">NOT ENOUGH TRADE DATA FOR PNL CURVE</span>
                </div>
              ) : loading ? (
                <div className="p-6 text-center">
                  <span className="text-[13px] text-pixel-gray animate-pulse">LOADING...</span>
                </div>
              ) : null}
              </div>
            </div>
          )}

          {/* ── Trade feed — every trade with its P&L impact, linked to chart ── */}
          {mode === "BACKTEST" && (() => {
            // Apply the trader filter BEFORE ordering so the running-P&L
            // values stay consistent with what's shown — feedTraderFilter
            // pre-trims linkedTrades to a single trader's contribution path.
            const filtered = feedTraderFilter
              ? linkedTrades.filter((t) => t.trader.toLowerCase() === feedTraderFilter)
              : linkedTrades;
            const ordered = feedOrder === "newest" ? [...filtered].reverse() : filtered;
            const finalPnl = filtered.length > 0 ? filtered[filtered.length - 1].runningPnl : 0;
            const n = filtered.length;

            // Build per-trader counts for the chip row labels.
            const tradesByTrader = new Map<string, number>();
            for (const t of linkedTrades) {
              const k = t.trader.toLowerCase();
              tradesByTrader.set(k, (tradesByTrader.get(k) || 0) + 1);
            }

            return (
              <div className="pixel-panel overflow-hidden">
                <div className="px-4 py-2.5 border-b-2 border-pixel-border flex items-center justify-between flex-wrap gap-2">
                  <span className="text-[14px] text-pixel-gray-light tracking-wider">TRADE FEED</span>
                  <div className="flex items-center gap-3 text-[13px] font-mono">
                    <span className="text-pixel-gray">
                      {filtered.length} {feedTraderFilter ? `of ${linkedTrades.length} ` : ""}TRADES
                    </span>
                    {filtered.length > 0 && (
                      <span className={finalPnl >= 0 ? "text-green-400" : "text-red-400"}>
                        {finalPnl >= 0 ? "+" : ""}${finalPnl.toFixed(2)}
                      </span>
                    )}
                    <button
                      onClick={() => setShowAllTrades((v) => !v)}
                      className={`pixel-btn text-[13px] px-2 py-0.5 transition-colors ${
                        showAllTrades
                          ? "border-amber-400 text-amber-400 bg-amber-400/10"
                          : "border-pixel-border text-pixel-gray hover:text-amber-400 hover:border-amber-400"
                      }`}
                      title={
                        showAllTrades
                          ? `Showing every upstream trade — ignoring MIN ${minTrade}/MAX ${maxTrade} constraints.`
                          : `Hiding trades outside MIN ${minTrade}/MAX ${maxTrade}. Click to show all upstream activity.`
                      }
                    >
                      {showAllTrades ? "SHOW ALL ✓" : "SHOW ALL"}
                    </button>
                    <button
                      onClick={() => setFeedOrder((o) => (o === "newest" ? "oldest" : "newest"))}
                      className="pixel-btn text-[13px] px-2 py-0.5 border-pixel-border text-pixel-gray hover:text-green-400 hover:border-green-400 transition-colors"
                      title="Toggle sort order"
                    >
                      {feedOrder === "newest" ? "NEW→OLD" : "OLD→NEW"}
                    </button>
                  </div>
                </div>

                {/* Per-trader filter chips — click any trader to isolate their
                    contribution to the feed. ALL clears the filter. Only shown
                    when there's more than one trader contributing trades. */}
                {tradesByTrader.size > 1 && (
                  <div className="px-3 py-1.5 border-b border-pixel-border/40 bg-pixel-black/30 flex items-center gap-1 flex-wrap">
                    <span className="text-[10px] text-pixel-gray tracking-[0.15em] mr-1">SHOW</span>
                    <button
                      onClick={() => setFeedTraderFilter(null)}
                      className={`pixel-btn text-[11px] px-2 py-0.5 ${
                        feedTraderFilter === null
                          ? "border-green-400 text-green-400 bg-green-400/10"
                          : "border-pixel-border text-pixel-gray hover:text-pixel-white"
                      }`}
                    >
                      ALL ({linkedTrades.length})
                    </button>
                    {Array.from(tradesByTrader.entries())
                      .sort((a, b) => b[1] - a[1])
                      .map(([addr, count]) => {
                        const active = feedTraderFilter === addr;
                        return (
                          <button
                            key={addr}
                            onClick={() => setFeedTraderFilter(active ? null : addr)}
                            className={`pixel-btn text-[11px] px-2 py-0.5 font-mono ${
                              active
                                ? "border-green-400 text-green-400 bg-green-400/10"
                                : "border-pixel-border text-pixel-gray hover:text-pixel-white"
                            }`}
                            title={`Isolate ${addr}'s trades (${count})`}
                          >
                            {shortAddress(addr)} <span className="text-pixel-gray-light">({count})</span>
                          </button>
                        );
                      })}
                  </div>
                )}

                {loading ? (
                  <div className="p-8 text-center">
                    <div className="text-[14px] text-pixel-white animate-pulse">LOADING...</div>
                  </div>
                ) : linkedTrades.length > 0 ? (
                  <>
                    <div className="overflow-auto max-h-[480px]">
                      <table className="pixel-table" style={{ minWidth: "100%", tableLayout: "auto" }}>
                        <thead className="sticky top-0 bg-pixel-black z-10">
                          <tr>
                            <th className="whitespace-nowrap">WHEN</th>
                            <th>MARKET</th>
                            <th className="whitespace-nowrap">TRADER</th>
                            <th className="whitespace-nowrap">SIDE</th>
                            <th className="text-right whitespace-nowrap">AMOUNT</th>
                            <th className="text-right whitespace-nowrap">PRICE</th>
                            <th className="text-right whitespace-nowrap">FEE</th>
                            <th className="text-right whitespace-nowrap">IMPACT</th>
                            <th className="text-right whitespace-nowrap">TOTAL P&L</th>
                          </tr>
                        </thead>
                        <tbody>
                          {ordered.map((t, di) => {
                            const origIdx = feedOrder === "newest" ? n - 1 - di : di;
                            const isHovered = tradeHighlight === origIdx;
                            const isSelected = selectedTradeIdx === origIdx;
                            const d = new Date(t.ts);
                            const when = `${d.getUTCMonth() + 1}/${d.getUTCDate()} ${d.getUTCHours().toString().padStart(2, "0")}:${d.getUTCMinutes().toString().padStart(2, "0")}`;
                            return (
                              <tr
                                key={`${t.trader}-${t.ts}-${di}`}
                                className={`transition-colors cursor-pointer ${
                                  isSelected
                                    ? "bg-green-400/10 outline outline-1 outline-green-400/60"
                                    : isHovered
                                      ? "bg-pixel-white/10"
                                      : "hover:bg-pixel-white/5"
                                }`}
                                title="Click to pin this trade against the chart — click again to unpin"
                                onClick={() => {
                                  // Pinned selection persists across mouse-leave (unlike
                                  // the hover preview below) so clicking actually "selects"
                                  // a trade instead of the highlight vanishing the instant
                                  // the cursor moves off the row.
                                  setSelectedTradeIdx((cur) => (cur === origIdx ? null : origIdx));
                                  setChartHighlight(findCurveIdx(t.ts));
                                  setTradeHighlight(origIdx);
                                  chartPanelRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
                                }}
                                onMouseEnter={() => {
                                  setChartHighlight(findCurveIdx(t.ts));
                                  setTradeHighlight(origIdx);
                                }}
                                onMouseLeave={() => {
                                  // Fall back to the pinned selection (if any) instead of
                                  // clearing outright, so a selected trade stays visible
                                  // against the chart once the mouse leaves the row.
                                  if (selectedTradeIdx !== null) {
                                    const sel = linkedTrades[selectedTradeIdx];
                                    setChartHighlight(sel ? findCurveIdx(sel.ts) : null);
                                    setTradeHighlight(selectedTradeIdx);
                                  } else {
                                    setChartHighlight(null);
                                    setTradeHighlight(null);
                                  }
                                }}
                              >
                                <td title={t.market} className="whitespace-nowrap">
                                  <div className="text-[13px] text-pixel-gray-light font-mono">{when}</div>
                                </td>
                                <td className="max-w-[220px]">
                                  <div className="text-[13px] text-pixel-white truncate" title={t.market}>
                                    {t.market || "—"}
                                  </div>
                                </td>
                                <td>
                                  {/* Click trader: isolate the feed to just
                                      their trades. Shift+click: jump to the
                                      trader's profile page (old behavior). */}
                                  <button
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      if (e.shiftKey) {
                                        goToTrader(t.trader);
                                        return;
                                      }
                                      const lc = t.trader.toLowerCase();
                                      setFeedTraderFilter((cur) => (cur === lc ? null : lc));
                                    }}
                                    className={`text-[13px] font-mono transition-colors ${
                                      feedTraderFilter === t.trader.toLowerCase()
                                        ? "text-green-400"
                                        : "text-pixel-gray-light hover:text-green-400"
                                    }`}
                                    title="Click: isolate this trader's trades. Shift+Click: open profile."
                                  >
                                    {shortAddress(t.trader)}
                                  </button>
                                </td>
                                <td>
                                  <span className={`pixel-badge ${
                                    t.side === "BUY"
                                      ? "border-green-400/60 text-green-400"
                                      : "border-red-400/60 text-red-400"
                                  }`}>
                                    {t.side}
                                  </span>
                                </td>
                                <td className="text-right text-pixel-white font-mono whitespace-nowrap">
                                  {t.amount === 0
                                    ? "—"
                                    : t.amount < 0.01
                                      ? `${(t.amount * 100).toPrecision(2)}¢`
                                      : t.amount < 1
                                        ? `${(t.amount * 100).toFixed(1)}¢`
                                        : t.amount < 100
                                          ? `$${t.amount.toFixed(2)}`
                                          : `$${t.amount.toFixed(0)}`
                                  }
                                </td>
                                <td className="text-right text-pixel-gray-light font-mono whitespace-nowrap">
                                  {Math.round(t.price * 100)}c
                                </td>
                                <td className="text-right text-amber-400/70 font-mono whitespace-nowrap">
                                  ${t.fee.toFixed(2)}
                                </td>
                                <td className={`text-right font-mono whitespace-nowrap ${
                                  t.pnlDelta > 0.005 ? "text-green-400"
                                    : t.pnlDelta < -0.005 ? "text-red-400"
                                    : "text-pixel-gray"
                                }`}>
                                  {t.pnlDelta > 0.005 ? "+" : t.pnlDelta < -0.005 ? "" : ""}
                                  {Math.abs(t.pnlDelta) >= 0.005 ? `$${t.pnlDelta.toFixed(2)}` : "—"}
                                </td>
                                <td className={`text-right font-mono whitespace-nowrap ${
                                  t.runningPnl > 0 ? "text-green-400"
                                    : t.runningPnl < 0 ? "text-red-400"
                                    : "text-pixel-gray"
                                }`}>
                                  {t.runningPnl >= 0 ? "+" : ""}${t.runningPnl.toFixed(2)}
                                </td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>

                  </>
                ) : (
                  <div className="p-8 text-center">
                    <div className="text-[14px] text-pixel-gray">NO TRADES IN THIS WINDOW</div>
                  </div>
                )}
              </div>
            );
          })()}

          {/* ── Live Panel ── */}
          {mode === "LIVE" && watchlist.length > 0 && <LivePanel />}
        </>
      )}

    </div>
  );
}
