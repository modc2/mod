"use client";

import { useState, useEffect, useMemo, useCallback, useRef, type ReactNode } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { fetchPositions, fetchWalletTradesUntil, fetchWalletTradesIncremental, formatVolume, formatPnl, fetchTradersPage, TopTrader, CATEGORIES, TRADER_SYNC_MIN_MS } from "../lib/polymarket";
import { PolymarketPosition, PolymarketTrade, SavedIndex, TradeFilters, TraderFilter, TraderMetric } from "../lib/types";
import { tradeMatchesFilters, tradeFiltersActive } from "../lib/tradeFilters";
import { useSentimentBook } from "../lib/useSentimentBook";
import type { SentimentFilter } from "../lib/marketSentiment";
import { Strat, rankTraders, formatAgeShort, DEFAULT_FILTER_TOP_N, DEFAULT_MAX_STALE_HOURS, DEFAULT_STOP_LOSS, DEFAULT_TAKE_PROFIT, MIN_POLL_MINUTES, DEFAULT_MIN_MINUTES_TO_CLOSE, DEFAULT_MAX_UPSCALE } from "../lib/strats/strat";
import type { TraderTrade as StratTraderTrade } from "../lib/strats/strat";
import { marketMatchesQuery } from "../lib/marketQuery";
import { setAllocationMarketQuery } from "../lib/copyBook";
import { getOwnerAddress } from "../lib/access";
import { shortAddress } from "@/lib/auth";
import { useFilterParams, useFilters } from "../context/FiltersContext";
import { useAuth } from "../context/AuthContext";
import { useCopyEngine } from "../context/CopyEngineContext";
import CopyTrading from "./CopyTrading";
import PerfPanel from "./PerfPanel";
import TraderFilterCard from "./TraderFilterCard";
import SentimentCard from "./SentimentCard";
import CapitalPlanCard from "./CapitalPlanCard";
import type { CapitalPlanInput } from "../lib/capitalPlan";
import type { CurvePoint } from "./PnlChart";
import { computeFifoTrades, buildPnlCurve, buildCombinedPnlCurve, aggregateToRebalanceWindows } from "../lib/pnlEngine";
import { legKey } from "../lib/leg";
import { fetchResolvedLegs } from "../lib/hubCache";
// The backtest engine itself lives in lib/backtest.ts — the STRAT HUB replays
// every saved strat through the exact same functions, so a card and this tab
// can never disagree about what a strat did.
import {
  runBacktest, keepInSample, DEFAULT_CAPITAL,
  type BacktestSim,
} from "../lib/backtest";
import {
  FeeBook, NEW_DEPLOYMENT_GAS_OPS, fetchGasQuote, fmtGasUsd, sessionGasUsd, takerFeeUsd,
  type GasQuote,
} from "../lib/fees";
import { tapeFor } from "../lib/momentumTape";
import type { PriceTape } from "../lib/originationBacktest";
import { loadIndexes, saveIndex, deleteIndex, updateIndex, getActiveIndexId, setActiveIndexId, equalWeightTraders } from "../lib/indexStore";
import { pushStrat } from "../lib/stratSync";
import { fetchTraderBankrolls } from "../lib/liveSessions";
import LivePanel, { type LiveTab, normalizeLiveTab } from "./LivePanel";
import { modeOf } from "../lib/tradingMode";
import { SessionChip } from "./ModeControl";
import { OPEN_MONEY_EVENT } from "./MoneyBlock";
import { templateIndex, templateRoster, traderIndexTemplate } from "../lib/defaultStrats";
import { isTraderIndex } from "../lib/traderIndex";
import IndexScaleCard from "./IndexScaleCard";

// ══════════════════════════════════════════
// ── Subtab rail — second-level nav under the main TEST / TRADE tabs.
//
//    There is NO WALLET stop on this rail any more. Topping up and taking
//    money out live in the SIDE PANEL (components/MoneyBlock.tsx), reachable
//    from every screen with the column already open — money is a drawer, not
//    a destination, and a funding form parked inside the engine's own tab rail
//    made "add money" something you navigated AWAY from your session to do.
//    LIVE's FUND NOW banner now dispatches OPEN_MONEY_EVENT, which opens that
//    drawer over whatever you were looking at.
//
//    Each main tab gets its own accent, but note what those accents are NOT:
//    they are wayfinding, not mode. "Is the money real?" is answered only by
//    the amber/green TEST|LIVE switch inside the TRADE tab (lib/tradingMode.ts
//    owns that palette). A nav colour that also encoded execution mode is how
//    a cyan LIVE tab ended up sitting over an amber dry run.
//
//    SETTINGS is NOT a tab — it's the collapsible panel above these, because
//    the gates the engine copies through apply to the backtest AND the live
//    session at once. It has no rail of its own: it used to carry BUILD /
//    SOURCE / MARKET, and the last two went with the strat layer.
// ══════════════════════════════════════════
type MainTab = "BACKTEST" | "LIVE";
type BacktestSub = "results" | "trades";
type LiveSub = LiveTab;

const SUBTABS: Record<MainTab, { id: string; glyph: string; label: string; title: string }[]> = {
  BACKTEST: [
    { id: "results", glyph: "◔", label: "RESULTS", title: "Run the sim — P&L, fees, simulated equity curve" },
    { id: "trades", glyph: "⇄", label: "TRADES", title: "Every simulated trade with its P&L impact" },
  ],
  LIVE: [
    // PORTFOLIO + STATS + TRADES folded into ONE desk — they were three views
    // of a single question ("is it working, and did anything reach my
    // wallet?") that could never be on screen together. See LivePanel.
    { id: "desk", glyph: "◔", label: "DESK", title: "Equity curve · engine vitals · every trade — mine vs the traders I copy, copied vs filtered out" },
    { id: "help", glyph: "?", label: "HELP", title: "Which wallet do I use?" },
  ],
};

// Active-pill accents (static strings — Tailwind can't see computed classes).
const SUB_ACCENT: Record<MainTab, string> = {
  BACKTEST: "border-amber-400/60 text-amber-400 bg-amber-400/[0.08] shadow-[0_0_16px_rgba(251,191,36,0.25)]",
  LIVE: "border-cyan-400/60 text-cyan-300 bg-cyan-400/[0.08] shadow-[0_0_16px_rgba(34,211,238,0.25)]",
};

// Main-tab accents mirror the subtab rail's per-screen tones so BACKTEST and
// TRADE read as two distinct places from the top row alone.
const MAIN_ACTIVE: Record<MainTab, string> = {
  BACKTEST: "text-amber-400 bg-amber-400/[0.08]",
  LIVE: "text-cyan-300 bg-cyan-400/[0.08]",
};
const MAIN_BAR: Record<MainTab, string> = {
  BACKTEST: "bg-amber-400 shadow-[0_0_10px_rgba(251,191,36,0.7)]",
  LIVE: "bg-cyan-400 shadow-[0_0_10px_rgba(34,211,238,0.7)]",
};

const STRAT_SUB_KEY = "polymarket.sub.strat";
const STRAT_OPEN_KEY = "polymarket.strat.open";
const SUB_KEYS: Record<MainTab, string> = {
  BACKTEST: "polymarket.sub.backtest",
  LIVE: "polyLiveTab", // shared with LivePanel's uncontrolled fallback
};

function formatCountdown(ms: number): string {
  if (ms <= 0) return "NOW";
  const sec = Math.floor(ms / 1000);
  if (sec < 60) return `${sec}s`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ${sec % 60}s`;
  return `${Math.floor(min / 60)}h ${min % 60}m`;
}

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

  // Filter out SELLs without a prior BUY in the window (copy-trader wouldn't
  // hold those). Per OUTCOME TOKEN, like every other book in the app — a Yes
  // exit is not covered by a No entry (lib/leg.ts).
  const windowInv = new Map<string, number>();
  const windowTrades = allWindowTrades.filter((t) => {
    const key = legKey(t.conditionId || t.market, t.outcome);
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
  // Real taker fees, at each market's OWN rate — measured off this trader's
  // own fills where their `usdcSize` reveals it, inferred from the market's
  // category where it doesn't (lib/fees.ts).
  const feeBook = new FeeBook().observeAll(windowTrades);
  const totalFees = windowTrades.reduce((sum, t) => {
    const rate = feeBook.rateFor(t.conditionId, t.market, t.slug);
    return sum + takerFeeUsd(t.size * copyRatio, t.price, rate);
  }, 0);
  // Gas is per DEPLOYMENT, not per trade: fills are relayer-matched. One
  // wallet deploy + approvals + funding, at the fallback Polygon quote (this
  // estimate is synchronous; the full sim uses a live quote).
  const totalGas = sessionGasUsd(NEW_DEPLOYMENT_GAS_OPS);
  // Scale PnL to simulated capital (matches the ROI metric)
  const scaledPnl = estimatedPnl * copyRatio;
  const pnlAfterCosts = scaledPnl - totalFees - totalGas;

  // Build individual simulated trades scaled to user's capital
  const simulatedTrades: SimulatedTrade[] = windowTrades.map((t) => {
    const traderNotional = t.price * t.size;
    const mirrorNotional = Math.round(traderNotional * copyRatio * 100) / 100;
    const mirrorSize = Math.round(t.size * copyRatio * 100) / 100;
    const fee = Math.round(
      takerFeeUsd(mirrorSize, t.price, feeBook.rateFor(t.conditionId, t.market, t.slug)) * 100,
    ) / 100;
    const gas = 0; // relayer-matched fill — see `totalGas` above
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
      <div className="relative">
        <span className="absolute left-3 top-1/2 -translate-y-1/2 text-[14px] text-pixel-gray pointer-events-none select-none">⌕</span>
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onFocus={() => setFocused(true)}
          onKeyDown={(e) => { if (e.key === "Enter") handleSubmit(); }}
          placeholder="Search traders by name — or paste a 0x address to add"
          className="pixel-input-sm w-full font-mono text-[14px] pl-8 pr-24"
          spellCheck={false}
        />
        {searching && (
          <span className="absolute right-3 top-1/2 -translate-y-1/2 text-[12px] text-green-400 animate-pulse">searching…</span>
        )}
        {isAddress(input.trim()) && !alreadyAdded && (
          <button
            onClick={handleSubmit}
            className="absolute right-1.5 top-1/2 -translate-y-1/2 pixel-btn text-[12px] px-2.5 py-0.5 border-green-400 text-green-400 hover:bg-green-400/10"
          >
            + ADD
          </button>
        )}
        {alreadyAdded && (
          <span className="absolute right-3 top-1/2 -translate-y-1/2 text-[12px] text-pixel-gray">ALREADY IN STRAT</span>
        )}
      </div>

      {focused && results.length > 0 && (
        <div className="absolute z-50 left-0 right-0 mt-1.5 border border-pixel-border bg-pixel-black rounded-[var(--radius-sm)] shadow-xl shadow-black/50 max-h-[240px] overflow-y-auto">
          {results.map((t) => (
            <button
              key={t.address}
              onClick={() => handlePick(t.address)}
              className="w-full flex items-center justify-between gap-3 px-3 py-2 hover:bg-green-400/[0.06] transition-colors text-left group/res"
            >
              <div className="flex items-center gap-2 min-w-0">
                <span className="text-[14px] font-mono text-pixel-white truncate">{shortAddress(t.address)}</span>
                <span className={`text-[13px] font-mono shrink-0 ${t.pnl >= 0 ? "text-green-400" : "text-red-400"}`}>
                  {formatPnl(t.pnl)}
                </span>
              </div>
              <div className="flex items-center gap-3 text-[12px] text-pixel-gray font-mono shrink-0">
                <span>VOL {formatVolume(t.volume)}</span>
                <span>{t.recentTrades || t.positions} trades</span>
                <span className="text-green-400 opacity-0 group-hover/res:opacity-100 transition-opacity">+ ADD</span>
              </div>
            </button>
          ))}
        </div>
      )}
      {focused && coldCache && input.trim() && !isAddress(input.trim()) && (
        <div className="absolute z-50 left-0 right-0 mt-1.5 border border-pixel-border bg-pixel-black rounded-[var(--radius-sm)] px-3 py-2">
          <span className="text-[13px] text-pixel-gray">Trader cache is still warming — paste a 0x address to add directly.</span>
        </div>
      )}
    </div>
  );
}

interface CopyIndexProps {
  searchFilter: string;
  compact?: boolean;
  /** Pin the workspace to one screen and hide the internal TEST|LIVE row.
      BACKTEST and LIVE are top-level tabs in the global nav now — a second
      switch inside the panel is the same choice offered twice, and the two
      drift apart the moment one of them remembers a position. Routes pass
      this; nothing else should. */
  forcedMode?: MainTab;
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

// Titled cluster of PARAMS knobs — breaks the flat knob soup into scannable
// SIZING / RISK / ENGINE / MARKETS cards. Wide rows (TRADE FILTER) span the
// grid via className.
function ParamGroup({
  title,
  hint,
  className,
  children,
}: {
  title: string;
  hint?: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <div className={`rounded-[var(--radius-sm)] border border-pixel-border/50 bg-pixel-black/30 px-3 pt-2 pb-2.5 ${className ?? ""}`}>
      <div className="flex items-baseline gap-2 mb-2 min-w-0">
        <span className="text-[10px] font-bold text-green-400/80 tracking-[0.24em] shrink-0">{title}</span>
        {hint && <span className="text-[10px] text-pixel-gray/80 truncate">{hint}</span>}
      </div>
      <div className="flex items-end gap-2.5 flex-wrap">{children}</div>
    </div>
  );
}

export default function CopyIndex({ searchFilter, compact, forcedMode }: CopyIndexProps) {
  const router = useRouter();
  const filterQs = useFilterParams({ excludeSearch: true });
  const { localToken, auth } = useAuth();
  // Pull the live engine state so the chart can switch its data source to
  // the engine's actual order log when mode === "LIVE" — historical replay
  // is meaningless when the user is monitoring real-time trading.
  const { isLive, engineState, backendRunning, autoExecute } = useCopyEngine();

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
  // Watched traders' bankrolls (positions + cash), keyed lowercase — the
  // denominator of proportional copy sizing. See `traderCopyRatio`.
  const [traderBankrolls, setTraderBankrolls] = useState<Map<string, number>>(new Map());
  const [loadedCount, setLoadedCount] = useState(0);
  const [loading, setLoading] = useState(true);
  const [lastUpdated, setLastUpdated] = useState<number | null>(null);
  const [utcNow, setUtcNow] = useState(Date.now());

  // ── Backtest ──
  const [backtestDays, setBacktestDays] = useState(3);
  const [backtestDaysInput, setBacktestDaysInput] = useState("3");
  const [capital, setCapital] = useState(DEFAULT_CAPITAL);
  // Funds source for the backtest sizing. SIM = paper capital (the CAPITAL
  // param, $1K default) — needs no wallet and no deposit; WALLET = the
  // deposit wallet's live USDC balance, so the preview sizes exactly like a
  // real deployment would. Persisted per-strat.
  const [fundsMode, setFundsMode] = useState<"SIM" | "WALLET">("SIM");
  // Deposit-wallet USDC balance for the WALLET side of the toggle.
  // null = unknown (no wallet connected, or the read failed).
  const [walletBalance, setWalletBalance] = useState<number | null>(null);
  const [minTrade, setMinTrade] = useState(5);
  const [maxTrade, setMaxTrade] = useState(100);
  // Proportional-copy fidelity: how far a mirror may be upsized past what
  // proportionality asked for when that amount lands under the order floor.
  // 2 = "never place more than 2× the proportional size", everything smaller
  // is skipped as SUB_SCALE. 0 = OFF (∞): every filtered trade is clamped up
  // to the floor and placed, which is what a small account needs to trade at
  // all — at the cost of sizing a conviction bet and a punt identically.
  const [maxUpscale, setMaxUpscale] = useState<number>(DEFAULT_MAX_UPSCALE);
  // Max concurrent open positions — the live engine skips a mirror BUY that
  // would open a NEW token while this many are already held.
  const [maxOpenPositions, setMaxOpenPositions] = useState(10);
  // Minutes a market must still have left to run before a leader's BUY in it
  // is copyable. 60 (the engine's own default) excludes the 5m/15m/hourly
  // Up-or-Down candles — copying those late is a measured, structural loss.
  // 0 = off, and copying them is then a deliberate choice.
  const [minMinutesToClose, setMinMinutesToClose] = useState(DEFAULT_MIN_MINUTES_TO_CLOSE);
  // Per-position stop-loss, held as the integer PERCENT LOSS from entry that
  // triggers the exit (10 = sell once the position is down 10%). Persisted on
  // the strat as the DEFENDED 0–1 fraction of entry (`stopLoss` = 1 − loss%),
  // which is what the live engine and the backtest sim both enforce — the UI
  // just speaks the standard "max loss %" convention. Defaults ON at 25%
  // (undefined ⇒ 0.75: exit once the price decays to 75% of entry); an
  // explicit 0 is the off switch and is persisted as 0, not dropped.
  const [stopLossPct, setStopLossPct] = useState(Math.round((1 - DEFAULT_STOP_LOSS) * 100));
  // stored defend-fraction → displayed loss-% (and back, in updateStopLossPct).
  const stopLossFracToLossPct = (frac: number | undefined): number => {
    const f = frac ?? DEFAULT_STOP_LOSS;
    return f > 0 && f < 1 ? Math.round((1 - f) * 100) : 0;
  };
  // Per-position take-profit as an absolute mark level (strat `takeProfit`,
  // default 0.99 = liquidate once the market runs to the top tick — a
  // decided market has ≤1¢ left to earn). No UI knob yet; the strat param
  // is the override, 0 = off.
  const takeProfitFrac = activeIndex?.takeProfit ?? DEFAULT_TAKE_PROFIT;
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
  // Trader FILTER — which of the watched traders are good enough to copy
  // right now. `null` = off (copy everyone); an object turns the gate on.
  const [traderFilter, setTraderFilter] = useState<TraderFilter | null>(null);
  const [priceMinInput, setPriceMinInput] = useState("");
  const [priceMaxInput, setPriceMaxInput] = useState("");
  const [sizeMinInput, setSizeMinInput] = useState("");
  const [sizeMaxInput, setSizeMaxInput] = useState("");
  // SHOW ALL TRADES — when true, the linkedTrades pipeline skips the TRADE
  // SIZE filter and surfaces every upstream trade regardless of mirror
  // amount. Declared here (not next to feedOrder) so it's available to the
  // linkedTrades useMemo that fires earlier in the render.
  const [showAllTrades, setShowAllTrades] = useState(false);
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
  const [rebalanceMinutes, setRebalanceMinutes] = useState<number>(MIN_POLL_MINUTES); // minutes between live polls (30s floor)
  const [customDaysInput, setCustomDaysInput] = useState("");
  const [expandedTrader, setExpandedTrader] = useState<string | null>(null);
  const [showSimTrades, setShowSimTrades] = useState<Record<string, boolean>>({});
  const [simTradeLimit, setSimTradeLimit] = useState<Record<string, number>>({});
  const [refreshKey, setRefreshKey] = useState(0);

  // ── PARAMS fold — the full knob wall, CLOSED by default. The defaults ARE
  //    the strat; opening this is opting into tuning it. Remembered per
  //    browser so a tuner isn't re-opening it on every visit. ──
  const [paramsOpen, setParamsOpen] = useState(false);
  useEffect(() => {
    try { setParamsOpen(localStorage.getItem("poly_index_params_open") === "1"); } catch {}
  }, []);
  const toggleParams = () =>
    setParamsOpen((v) => {
      const next = !v;
      try { localStorage.setItem("poly_index_params_open", next ? "1" : "0"); } catch {}
      return next;
    });

  // ── Weights (local state, persisted on change) ──
  const [traderWeights, setTraderWeights] = useState<Record<string, number>>({});

  // ── Which screen this is (BACKTEST = replay history, LIVE = run it) ──
  // Normally the ROUTE decides: /backtest and /live are two of the console's
  // three tabs and each pins this with `forcedMode`. The local state is the
  // fallback for anywhere the workspace is rendered without a route saying
  // which half it is; it lands on TEST because that always has content, while
  // LIVE is blank until a wallet is connected and an engine is running.
  const [localMode, setLocalMode] = useState<MainTab>("BACKTEST");
  const mode = forcedMode ?? localMode;
  const setMode = setLocalMode;

  // ── Subtabs — one remembered position per main tab, so flipping
  // TEST → LIVE → TEST lands back where you were. Read after mount
  // (not in the initializer) to dodge a hydration mismatch; writes are
  // quota-safe — modc2.com modules share one localStorage origin.
  // The strat panel sits above both tabs and starts COLLAPSED on every load.
  // Editing is bursty (open it, turn knobs, collapse it and watch the
  // result) but READING is the common case: the console should open on the
  // numbers, not on a full-height editor you have to scroll past. The
  // open/closed choice is intentionally session-only — a panel left open
  // once should not make every future load start expanded.
  const [stratOpen, setStratOpen] = useState(false);
  const [backtestSub, setBacktestSub] = useState<BacktestSub>("results");
  const [liveSub, setLiveSub] = useState<LiveSub>("desk");
  useEffect(() => {
    try {
      // STRAT_SUB_KEY selected BUILD / SOURCE / MARKET under the settings
      // panel. There is only one view now — clear the stale entry rather than
      // leave a key nothing reads in the shared-origin store.
      localStorage.removeItem(STRAT_SUB_KEY);
      // STRAT_OPEN_KEY is deliberately NOT restored — see `stratOpen`. The
      // key is still cleared so an old persisted "true" can't linger.
      localStorage.removeItem(STRAT_OPEN_KEY);
      const bs = localStorage.getItem(SUB_KEYS.BACKTEST);
      if (bs === "results" || bs === "trades") setBacktestSub(bs);
      const ls = localStorage.getItem(SUB_KEYS.LIVE);
      // portfolio / stats / trades / positions all folded into DESK — migrate
      // any persisted value so an old entry doesn't select a dead subtab.
      // WALLET and HELP are unaffected.
      // A persisted "wallet" is a pointer at a subtab that no longer exists
      // (it moved to the side panel) — land on the DESK rather than on a
      // blank screen.
      const lsMapped = ls === "help" ? ls : normalizeLiveTab(ls === "wallet" ? "desk" : ls);
      if (ls) setLiveSub(lsMapped as LiveSub);
    } catch { /* storage unavailable — keep defaults */ }
  }, []);
  const pickSub = useCallback((m: MainTab, id: string) => {
    if (m === "BACKTEST") setBacktestSub(id as BacktestSub);
    else setLiveSub(id as LiveSub);
    try { localStorage.setItem(SUB_KEYS[m], id); } catch { /* quota full — non-fatal */ }
  }, []);
  const activeSub = mode === "BACKTEST" ? backtestSub : liveSub;
  const toggleStrat = useCallback(() => setStratOpen((o) => !o), []);

  // 1s tick for the rail's next-cycle countdown — only while the LIVE tab is
  // showing a running engine (the echo replaces LivePanel's old strip, which
  // moved up into the rail; see LivePanel controlled mode).
  const [railNow, setRailNow] = useState(0);
  useEffect(() => {
    if (!(mode === "LIVE" && isLive)) return;
    setRailNow(Date.now());
    const iv = setInterval(() => setRailNow(Date.now()), 1000);
    return () => clearInterval(iv);
  }, [mode, isLive]);

  // ── Embedded top-traders leaderboard (STRAT panel) ──
  // The standalone /traders page was folded into strat management: discovering
  // who to copy and assembling a strat now live in one place. The leaderboard
  // reads the shared TopBar filters (category / window / market topic), and
  // selecting a trader toggles them in/out of the active strat.
  // The add-trader bar lives inside this collapsible too. It starts CLOSED on
  // every load and the choice is NOT persisted: mounting it mounts the
  // leaderboard, which fans out live trader queries nobody asked for, and
  // half the strats here (momentum/candle origination) copy no one at all.
  // Browsing traders is a thing you go and do, not the state you land in.
  const [browseOpen, setBrowseOpen] = useState(false);
  useEffect(() => {
    // Drop the old persisted flag so a previously-open browser doesn't keep
    // reopening itself for users who already have it set.
    try { localStorage.removeItem("polymarket.stratTradersOpen"); } catch { /* non-fatal */ }
  }, []);
  const {
    category: browseCategory,
    daysAgo: browseDaysAgo,
    minPerDay: browseMinPerDayRaw,
    marketQuery: browseMarketQuery,
    reloadKey: browseReloadKey,
  } = useFilters();
  const browseDays = Number(browseDaysAgo) > 0 ? Number(browseDaysAgo) : 7;
  const browseMinTradesPerDay = browseMinPerDayRaw !== "" && Number.isFinite(Number(browseMinPerDayRaw))
    ? Math.max(0, Number(browseMinPerDayRaw))
    : 0;

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
  // Origination strats (momentum, incl. candle mode) buy from the market's own
  // price tape and need no watchlist at all — the live engine says as much
  // (`LivePanel`: the TRADERS precondition only applies to copy strats). Every
  // "add traders first" gate below has to exempt them, or picking BTC 5-MIN
  // DELTA hides its own backtest and LIVE panel behind a browse-traders nag.
  const originates = !!activeIndex?.momentum;

  // ── THE LEADER ──
  // A workspace is one copy-book row, so the strat's watchlist is exactly one
  // address at weight 1 and `identity` names them (lib/identityStrat.ts). Read
  // `identity` FIRST rather than `traders[0]`: a strat left over from the
  // multi-trader era also has a traders[0], and treating it as "the leader"
  // would quietly present a basket as one person. Null there means the active
  // strat is not a single-trader copy, and the panel says so.
  const leaderAddress = useMemo(() => {
    const id = (activeIndex?.identity ?? "").trim().toLowerCase();
    if (id) return id;
    const traders = activeIndex?.traders ?? [];
    return traders.length === 1 ? traders[0].address.toLowerCase() : null;
  }, [activeIndex]);

  // Bankrolls for the current watchlist, refreshed when it changes. Cached
  // server-side (~10m), so this is one cheap call per roster edit.
  useEffect(() => {
    if (!watchlistKey) { setTraderBankrolls(new Map()); return; }
    let alive = true;
    fetchTraderBankrolls(watchlistKey.split(",")).then((m) => { if (alive) setTraderBankrolls(m); });
    return () => { alive = false; };
  }, [watchlistKey]);

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

    // Always ensure at least one strat exists — and make it the console's
    // DEFAULT one. This used to mint a nameless `{name: "Default", traders:
    // []}` shell whose every param was an engine fallback, so a first-time
    // user's first backtest described a strategy nobody had chosen. Now it is
    // a TRADER INDEX: the shelf's default recipe, sized off the ratio between
    // your capital and each leader's (lib/traderIndex.ts). `templateIndex`
    // materializes exactly what forking that card on the STRATS board would
    // give you, so the two entry points cannot produce different strategies.
    let seeded: SavedIndex | null = null;
    if (indexes.length === 0) {
      const def = templateIndex(traderIndexTemplate());
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

    // Brand-new strat with nobody on it — seed it from the TEMPLATE's own
    // roster rule (top traders of the last week), not from whatever the
    // leaderboard browser happens to be filtered to. A default strat whose
    // bench depends on a filter the user hasn't touched yet is not a default.
    if (seeded) {
      templateRoster(traderIndexTemplate()).then((addrs) => {
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
    // Backtests are always paper — a strat carrying capital 0 (persisted
    // from an unfunded wallet or imported that way) would zero every
    // simulated trade, so fall back to the $1K default.
    setCapital(activeIndex.capital && activeIndex.capital > 0 ? activeIndex.capital : DEFAULT_CAPITAL);
    // Funds source (SIM vs WALLET) — the wallet-balance sync effect below
    // overwrites `capital` with the live balance when this lands on WALLET.
    setFundsMode(activeIndex.fundsMode === "WALLET" ? "WALLET" : "SIM");
    setMinTrade(activeIndex.minTrade ?? 5);
    setMaxTrade(activeIndex.maxTrade ?? 100);
    // undefined ⇒ the 2× default; null (legacy unbounded) and 0 both mean
    // OFF, and the field shows ∞ for either.
    setMaxUpscale(
      activeIndex.maxUpscale === undefined ? DEFAULT_MAX_UPSCALE : activeIndex.maxUpscale ?? 0,
    );
    setMaxPerCycle(activeIndex.maxPerCycle ?? 3);
    setMaxOpenPositions(activeIndex.maxOpenPositions ?? 10);
    setMinMinutesToClose(activeIndex.minMinutesToClose ?? DEFAULT_MIN_MINUTES_TO_CLOSE);
    setStopLossPct(stopLossFracToLossPct(activeIndex.stopLoss));
    setMarketQuery(activeIndex.marketQuery ?? "");
    setMarketQueryInput(activeIndex.marketQuery ?? "");
    const tf = activeIndex.tradeFilters ?? {};
    setTradeFilters(tf);
    setTraderFilter(activeIndex.filter ?? null);
    setPriceMinInput(tf.minPrice != null ? String(Math.round(tf.minPrice * 100)) : "");
    setPriceMaxInput(tf.maxPrice != null ? String(Math.round(tf.maxPrice * 100)) : "");
    setSizeMinInput(tf.minNotional != null ? String(tf.minNotional) : "");
    setSizeMaxInput(tf.maxNotional != null ? String(tf.maxNotional) : "");
    // Force per-trade replay on load — REBALANCE/AT selects were removed,
    // and any non-zero persisted value would silently aggregate trades into
    // windows with no way to undo from the UI.
    setRebalancePeriod(0);
    setRebalanceHour(0);
    // Treat the legacy 1-minute default as "unset" and surface the 30s
    // default. Sub-floor values from the old 5s era are clamped up: both the
    // console and the engine already run them at 30s, so showing "5s" in the
    // picker would just misreport the cadence (and match no option).
    setRebalanceMinutes(
      activeIndex.rebalanceMinutes && activeIndex.rebalanceMinutes !== 1
        ? Math.max(MIN_POLL_MINUTES, activeIndex.rebalanceMinutes)
        : MIN_POLL_MINUTES,
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeIndex?.id]);

  // ── Backtest funds source (SIM paper capital vs live WALLET balance) ──
  // Poll the deposit wallet's USDC balance (60s) while signed in so the
  // WALLET pill can show it. A missing wallet or failed read leaves it null —
  // the toggle then pins to SIM and the backtest keeps running on paper.
  useEffect(() => {
    if (!auth.address) {
      setWalletBalance(null);
      return;
    }
    let cancelled = false;
    const fetchBalance = async () => {
      try {
        const res = await fetch(`/api/polymarket/deposit-wallet/info?eoa=${auth.address}`);
        if (!res.ok) return;
        const info = await res.json() as { usdcBalance?: string | null };
        // usdcBalance is RAW 6-decimal token units (same as WalletChip /
        // WalletPanel, which both divide by 1e6). Without the division the
        // WALLET pill showed "$153867862" and — far worse — WALLET funds
        // mode sized the whole backtest with ~$153M of paper capital.
        const bal = info.usdcBalance != null ? Number(info.usdcBalance) / 1_000_000 : NaN;
        if (!cancelled && Number.isFinite(bal)) setWalletBalance(bal);
      } catch { /* keep last known value — never block the backtest on RPC */ }
    };
    void fetchBalance();
    const t = setInterval(fetchBalance, 60_000);
    return () => { cancelled = true; clearInterval(t); };
  }, [auth.address]);

  // Overwrite `capital` (the single input every backtest memo reads) with
  // the live balance while the WALLET source is selected. Only the override
  // lives here — restoring the SIM amount happens in updateFundsMode, so a
  // 60s balance poll can never stomp a freshly-typed CAPITAL in SIM mode.
  // Declared after the strat-load effect so it wins on strat switch.
  useEffect(() => {
    if (fundsMode === "WALLET" && walletBalance != null && walletBalance > 0) {
      setCapital(Math.max(1, Math.floor(walletBalance)));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fundsMode, walletBalance, activeIndex?.id]);

  const updateFundsMode = (mode: "SIM" | "WALLET") => {
    setFundsMode(mode);
    if (mode === "SIM") {
      // Re-read from storage — `activeIndex` can hold a stale capital after
      // param edits (updateIndex persists without setActiveIndex).
      const fresh = activeIndex ? loadIndexes().find((s) => s.id === activeIndex.id) : null;
      setCapital(fresh?.capital && fresh.capital > 0 ? fresh.capital : DEFAULT_CAPITAL);
    }
    if (activeIndex) {
      updateIndex(activeIndex.id, { fundsMode: mode, updatedAt: Date.now() });
    }
  };

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
    if (idx) {
      const renamed = { ...idx, name: name.trim(), updatedAt: Date.now() };
      persistIndex(renamed);
      // Mirror the drawer picker: keep the encrypted server copy in step so
      // the new name survives a fresh browser's local↔server merge.
      if (localToken) pushStrat(renamed, localToken.token);
    }
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
    // Typing a CAPITAL amount is an explicit "size with this simulated
    // figure" — flip a WALLET-pinned strat back to SIM so the balance sync
    // doesn't silently overwrite what the user just entered.
    setFundsMode("SIM");
    if (activeIndex) {
      updateIndex(activeIndex.id, { capital: clamped, fundsMode: "SIM", updatedAt: Date.now() });
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

  // Proportional-fidelity limit. 0 is a real value (place EVERY filtered
  // trade, clamped up to the order floor) so it persists as an explicit 0 —
  // dropping the field would silently re-arm the engine's 2× default.
  const updateMaxUpscale = (n: number) => {
    const clamped = n <= 0 ? 0 : Math.min(1000, Math.round(n));
    setMaxUpscale(clamped);
    if (activeIndex) {
      updateIndex(activeIndex.id, { maxUpscale: clamped, updatedAt: Date.now() });
    }
  };

  const updateMaxOpenPositions = (n: number) => {
    const clamped = Math.max(1, n);
    setMaxOpenPositions(clamped);
    if (activeIndex) {
      updateIndex(activeIndex.id, { maxOpenPositions: clamped, updatedAt: Date.now() });
    }
  };

  // Time-to-resolution gate. 0 is a real value (copy short-dated flow too), so
  // it persists as an explicit 0 rather than being dropped — dropping it would
  // silently re-arm the engine's 60m default on the next load.
  const updateMinMinutesToClose = (mins: number) => {
    const clamped = Math.max(0, Math.min(1440, Math.round(mins)));
    setMinMinutesToClose(clamped);
    if (activeIndex) {
      updateIndex(activeIndex.id, { minMinutesToClose: clamped, updatedAt: Date.now() });
    }
  };

  // Stop-loss, entered as the percent LOSS from entry that triggers the
  // exit. Clamped to 5–95: below 5% the defended fraction (1 − loss) sits
  // within the bid/ask spread and would fire on noise alone; the engine
  // treats defend fractions ≥1.0 as off for the same reason. 0 = off.
  const updateStopLossPct = (pct: number) => {
    const clamped = pct <= 0 ? 0 : Math.max(5, Math.min(95, pct));
    setStopLossPct(clamped);
    if (activeIndex) {
      // 0 persists as an explicit 0 (off) — dropping the field would just
      // re-arm the 0.75 default on the next load.
      updateIndex(activeIndex.id, {
        stopLoss: clamped === 0 ? 0 : (100 - clamped) / 100,
        updatedAt: Date.now(),
      });
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
  //
  // On a COPY row the local store is only a cache: `/copy/<address>` re-reads
  // the server book every 15s and `materialize()` lets server fields win, so a
  // query written here alone was overwritten within one poll — the filter
  // looked broken because the strat it edited was replaced. The server copy of
  // the gate is the one the live engine and the worker's backtest read, so
  // write it there and let the cache be refreshed from it.
  const updateMarketQuery = (q: string) => {
    const trimmed = q.trim();
    setMarketQuery(trimmed);
    if (activeIndex) {
      updateIndex(activeIndex.id, { marketQuery: trimmed, updatedAt: Date.now() });
    }
    const identity = (activeIndex?.identity ?? "").trim().toLowerCase();
    if (identity) {
      // Same owner rule as the desk — `auth.address` lags a wallet switch, and
      // without an eoa the route can't reconfigure a RUNNING session, so the
      // engine would keep copying under the old gate until the next restart.
      const eoa = getOwnerAddress() ?? auth.address ?? null;
      setAllocationMarketQuery(identity, trimmed, eoa).catch((e) => {
        console.error("failed to persist the market gate to the copy book", e);
      });
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

  // ── Trader FILTER (persist to strategy) ──
  // `null` clears the gate entirely (copy every watched trader); a patch
  // merges into the existing filter, turning it on with sane defaults if it
  // was off. The backtest re-runs off `backtestStrat`, which reads this.
  const patchTraderFilter = (changes: Partial<TraderFilter> | null) => {
    const next = changes === null ? null : { ...(traderFilter ?? { metric: "score" as const, topN: DEFAULT_FILTER_TOP_N }), ...changes };
    setTraderFilter(next);
    if (activeIndex) {
      updateIndex(activeIndex.id, { filter: next ?? undefined, updatedAt: Date.now() });
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

    const cutoffSec = Math.floor((Date.now() - 30 * 86400_000) / 1000);
    let done = 0;
    const promises = addresses.map(async (addr) => {
      try {
        // Silent refreshes ride the incremental path — fetches /activity
        // backwards from "now" until it hits a trade we've already seen,
        // then merges. Avoids re-pulling the same 30-day history every 60s.
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

  // Background refresh — re-poll every TRADER_SYNC_MIN_MS so the curve's
  // right edge keeps catching new trades. Without this the chart looks
  // sparse near "now" because data was only pulled once on mount. Guards:
  //   • only runs when there's a watchlist (no point polling 0 addrs)
  // The tick rides the SAME 30s gate as the live engine (fetchWalletTrades-
  // Incremental throttles per trader), so this loop and a running engine
  // together still cost one /activity sync per selected trader per 30s.
  useEffect(() => {
    if (watchlist.length === 0) return;
    const t = setInterval(() => {
      // Re-fetch the whole watchlist silently. Reuses the same setTraderTrades
      // path so the curve + linked feed rebuild via their existing useMemos.
      fetchAll(watchlist, true);
    }, TRADER_SYNC_MIN_MS);
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
        setCapital(found.capital && found.capital > 0 ? found.capital : DEFAULT_CAPITAL);
        setMinTrade(found.minTrade ?? 5);
        setMaxTrade(found.maxTrade ?? 100);
        setMaxUpscale(found.maxUpscale === undefined ? DEFAULT_MAX_UPSCALE : found.maxUpscale ?? 0);
        setMaxPerCycle(found.maxPerCycle ?? 3);
        setMaxOpenPositions(found.maxOpenPositions ?? 10);
        setStopLossPct(stopLossFracToLossPct(found.stopLoss));
        setMarketQuery(found.marketQuery ?? "");
        setMarketQueryInput(found.marketQuery ?? "");
        setTradeFilters(found.tradeFilters ?? {});
        setTraderFilter(found.filter ?? null);
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
    setMinTrade(5);
    setMaxTrade(100);
    setMaxOpenPositions(10);
    setStopLossPct(50);
    setSamplePct(100);
    setRebalancePeriod(0);
    setRebalanceHour(0);
    setRebalanceMinutes(5 / 60);
    persistIndex({
      ...activeIndex,
      traders: [],
      capital: DEFAULT_CAPITAL,
      minTrade: 5,
      maxTrade: 100,
      maxOpenPositions: 10,
      stopLoss: DEFAULT_STOP_LOSS,
      rebalancePeriod: 0,
      rebalanceHour: 0,
      rebalanceMinutes: MIN_POLL_MINUTES,
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
    // Hand the strat's semantic per-trade filters to the profile page (?tf=)
    // so it opens showing exactly the slice of this trader's flow the strat
    // would copy — not their whole history.
    const qs = new URLSearchParams(filterQs);
    // The market gate travels too (?mq=). It is half of what this strat copies
    // — the other half of the pair `tf` already carried — and the profile has
    // no way to know it otherwise: `filterQs` is the LEADERBOARD's filter
    // state, which on this screen is usually empty, so the gate would be
    // dropped and the tape would open showing markets the strat never touches.
    // The strat's own gate wins over a leftover leaderboard keyword.
    if (marketQuery.trim()) qs.set("mq", marketQuery.trim());
    if (tradeFiltersActive(tradeFilters)) {
      qs.set("tf", JSON.stringify(tradeFilters));
      if (activeIndex?.name) qs.set("tfn", activeIndex.name);
    }
    const s = qs.toString();
    router.push(`/traders/${addr}${s ? `?${s}` : ""}`);
  };


  // ── MARKET SENTIMENT ──
  // The bench's own BUYs inside the market gate — the sample the sentiment
  // card previews against, and the set the book is warmed for. Sentiment
  // gates ENTRIES, exactly like the rest of the trade filter, so exits are
  // not in the sample and never in the fetch.
  const sentimentSample = useMemo(() => {
    // Clamped to the REPLAY WINDOW, not to everything cached. `traderTrades`
    // holds up to 30 days; one price-history request spans at most 14
    // (`MAX_HISTORY_SPAN_MS`), so asking about the whole cache would put most
    // of the sample outside anything the tape can answer and paint a honest
    // but useless "COVERAGE 0%". The window is also the flow this preview is
    // actually about.
    const cutoff = Date.now() - backtestDays * 86400_000;
    const out: PolymarketTrade[] = [];
    for (const addr of watchlist) {
      for (const t of traderTrades.get(addr) || []) {
        if (t.side !== "BUY") continue;
        if (t.timestamp < cutoff) continue;
        if (!marketMatchesQuery(t.market, marketQuery)) continue;
        out.push(t);
      }
    }
    return out;
  }, [watchlist, traderTrades, marketQuery, backtestDays]);

  // Warms only when a sentiment gate is actually on — an unused dimension
  // costs zero price-history requests. Each trade is read AT ITS OWN
  // timestamp, so the preview is the reading the live engine would have taken
  // at the moment it saw the trade, not today's mood applied to last week.
  const sentiment = useSentimentBook(sentimentSample, tradeFilters.sentiment);
  const filterCtx = useMemo(
    () => ({ sentiment: sentiment.book.lookup }),
    [sentiment.book],
  );

  // ── Backtests ──
  const backtests = useMemo((): TraderBacktest[] => {
    return watchlist.map((addr) => {
      // Honor BOTH strat gates so the preview P&L reflects only the flow the
      // live strat would actually copy: the market-topic query, and the
      // semantic per-trade filter (side / entry price / size / category /
      // market sentiment).
      // The trade filter gates ENTRIES only — same as the live engine, which
      // keeps every leader SELL so exits always clear. computeBacktest then
      // drops SELLs with no surviving BUY behind them, so filtering out a BUY
      // takes its exit with it.
      const trades = (traderTrades.get(addr) || [])
        .filter((t) => marketMatchesQuery(t.market, marketQuery))
        .filter((t) => t.side !== "BUY" || tradeMatchesFilters(t, tradeFilters, filterCtx));
      const positions = traderData.get(addr) || [];
      return computeBacktest(trades, positions, backtestDays, addr, capital, rebalancePeriod, rebalanceHour);
    }).sort((a, b) => b.estimatedPnl - a.estimatedPnl);
  }, [watchlist, traderTrades, traderData, backtestDays, capital, rebalancePeriod, rebalanceHour, marketQuery, tradeFilters, filterCtx]);

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




  // ── Strat instance — single source of truth for live + backtest ──
  // The live engine instantiates its OWN copy (so the backtest can
  // re-render without disturbing the live cycle's state), but both
  // construct the SAME standard Strat class (src/app/app/lib/strats/
  // strat.ts) with the same params — what you backtest is what trades.
  const backtestStrat = useMemo(
    () => new Strat({
      maxPerCycle, marketQuery, tradeFilters, filter: traderFilter ?? undefined,
      // The live engine refuses BUYs in markets resolving inside this window
      // (live_engine.rs TOO_SOON). Without it here, the tab happily counted
      // 5-minute Up/Down fills that a deployment would never place.
      minMinutesToClose,
      // Edited live in SIZING → UPSCALE, so it reads the field state (not the
      // persisted strat) and the backtest re-runs on the keystroke.
      maxUpscale,
      ...(activeIndex?.sizing !== undefined && { sizing: activeIndex.sizing }),
      ...(activeIndex?.turnover !== undefined && { turnover: activeIndex.turnover }),
      // ORIGINATION params. Dropping these built a strat that could only
      // mirror, so the tab showed a momentum strat doing nothing while its
      // live session traded every cycle — the same gap the hub card had.
      ...(activeIndex?.momentum && { momentum: activeIndex.momentum }),
    })
      // The MARKET SENTIMENT readings. The strat's gate is synchronous, so the
      // price history is fetched above (`useSentimentBook`) and attached here
      // as a lookup — this is what makes the replay honor the sentiment gate
      // the live engine enforces instead of quietly ignoring it.
      .withSentiment(sentiment.book.lookup),
    [maxPerCycle, marketQuery, tradeFilters, traderFilter, minMinutesToClose,
     maxUpscale, activeIndex?.sizing, activeIndex?.turnover, activeIndex?.momentum,
     sentiment.book],
  );

  // ── The ORIGINATION price tape ──
  // A momentum strat's trades come from a market's own odds, not from anyone's
  // flow, so the replay needs the odds as they stood across the window. Fetched
  // only for strats that originate; every copy strat leaves this undefined and
  // pays nothing for it.
  const [priceTape, setPriceTape] = useState<PriceTape | undefined>(undefined);
  const [tapeLoading, setTapeLoading] = useState(false);
  const momentumKey = JSON.stringify(activeIndex?.momentum ?? null);
  useEffect(() => {
    const mo = activeIndex?.momentum;
    if (!mo) { setPriceTape(undefined); setTapeLoading(false); return; }
    let live = true;
    setTapeLoading(true);
    void tapeFor(mo, marketQuery, backtestDays, Date.now())
      .then((t) => { if (live) setPriceTape(t); })
      .finally(() => { if (live) setTapeLoading(false); });
    return () => { live = false; };
    // Keyed by the momentum PARAMS, not the object identity — the strat list
    // rebuilds its objects on every poll and would re-fetch the tape each time.
  }, [momentumKey, backtestDays, marketQuery]); // eslint-disable-line react-hooks/exhaustive-deps

  /** What the panel says about the price data this replay stood on. */
  const tapeChip = useMemo(() => {
    if (!activeIndex?.momentum) return undefined;
    if (tapeLoading) {
      return { label: "TAPE LOADING…", title: "Fetching the window's price history.", partial: true };
    }
    if (!priceTape || priceTape.markets === 0) {
      return {
        label: "NO PRICE TAPE",
        title: priceTape?.note
          ?? "No price history came back for this window, so there was nothing to replay.",
        partial: true,
      };
    }
    const partial = priceTape.markets < priceTape.expected;
    const hours = (priceTape.toMs - priceTape.fromMs) / 3_600_000;
    const unit = priceTape.mode === "candles" ? "CANDLES" : "MARKETS";
    return {
      label: `${priceTape.markets}/${priceTape.expected} ${unit}`,
      title:
        `Origination replay: ${priceTape.markets} of the window's ${priceTape.expected} ${unit.toLowerCase()}, `
        + `covering ${hours < 1 ? `${Math.round(hours * 60)} minutes` : `${hours.toFixed(1)} hours`} `
        + `at ${Math.round(priceTape.fidelityMs / 60_000)}-minute price bars.\n`
        + `A live session polls every ${Math.max(MIN_POLL_MINUTES, activeIndex?.livePollMinutes ?? MIN_POLL_MINUTES) * 60}s and also reads the CLOB midpoint, `
        + `so it sees moves BETWEEN these bars that this replay cannot — treat the entry/exit timing as coarser than live.`
        + (priceTape.note ? `\n${priceTape.note}` : ""),
      partial,
    };
  }, [activeIndex?.momentum, activeIndex?.livePollMinutes, priceTape, tapeLoading]);

  // ── What the window's markets actually paid out ──
  // The replay has to value whatever it's still holding when the leaders go
  // quiet. Marking it at the last observed price flatters the result — leaders
  // trade winners on the way up and let losers expire, so a loser's last print
  // is its entry price and the loss never books. The server's resolution store
  // knows how the closed ones settled; ask it (store-only, no upstream calls)
  // and hand the answers to the sim. Anything it doesn't know yet is marked,
  // and the panel says how much of the number that is.
  const [resolvedLegs, setResolvedLegs] = useState<Map<string, number>>(new Map());
  const windowConditionIds = useMemo(() => {
    const cutoff = Date.now() - backtestDays * 86400_000;
    const ids = new Set<string>();
    for (const addr of watchlist) {
      for (const t of traderTrades.get(addr) || []) {
        if (t.timestamp >= cutoff && t.conditionId) ids.add(t.conditionId);
      }
    }
    return [...ids].sort();
  }, [watchlist, traderTrades, backtestDays]);
  useEffect(() => {
    if (windowConditionIds.length === 0) { setResolvedLegs(new Map()); return; }
    let live = true;
    void fetchResolvedLegs(windowConditionIds).then((legs) => {
      if (live) setResolvedLegs(legs);
    });
    return () => { live = false; };
    // Keyed by the id SET, not the array identity — the 60s refresh rebuilds
    // the array every poll and would otherwise re-ask on every tick.
  }, [windowConditionIds.join(",")]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── The backtest ──
  // One call into lib/backtest.ts runs the whole pipeline — 30d trader stats
  // → proportional copy ratios → the StratHistory hooks see → the per-cycle
  // top-N rank race → the simulated-wallet replay. The HUB replays every
  // saved strat through this same function, so a card's number and this tab's
  // number are produced by the same code, not by two copies of it.
  // ── Live Polygon gas price ──
  // Fetched once per mount. The cost model works without it (there is a
  // labelled fallback quote), so nothing waits on this — it just upgrades the
  // GAS line from "estimate" to "measured" when it lands.
  const [gasQuote, setGasQuote] = useState<GasQuote | undefined>(undefined);
  useEffect(() => {
    let live = true;
    fetchGasQuote()
      .then((q) => { if (live) setGasQuote(q); })
      .catch(() => { /* the fallback quote is already in play */ });
    return () => { live = false; };
  }, []);

  const backtest = useMemo(() => runBacktest({
    watchlist,
    traderTrades,
    traderPositions: traderData,
    traderWeights,
    traderBankrolls,
    strat: backtestStrat,
    days: backtestDays,
    capital,
    minTrade,
    maxTrade,
    maxOpenPositions,
    stopLossPct,
    takeProfitFrac,
    marketQuery,
    // The engine's LIVE cadence, not the backtest-only rebalance knob.
    pollMinutes: activeIndex?.livePollMinutes ?? rebalanceMinutes ?? 1,
    rebalancePeriod,
    rebalanceHour,
    samplePct,
    showAllTrades,
    loading,
    // Size the preview off the same denominator the live session will.
    sizing: activeIndex?.sizing,
    turnover: activeIndex?.turnover,
    resolved: resolvedLegs,
    // Live Polygon gas price + POL price, so the GAS line is a real number
    // rather than a constant. Falls back to a labelled estimate until it lands.
    gasQuote,
    // Origination replay input — undefined for copy strats.
    tape: priceTape,
  }), [watchlist, traderTrades, traderData, traderWeights, traderBankrolls, backtestStrat,
       backtestDays, capital, minTrade, maxTrade, maxOpenPositions, stopLossPct, takeProfitFrac,
       marketQuery, activeIndex?.livePollMinutes, rebalanceMinutes, rebalancePeriod,
       rebalanceHour, samplePct, showAllTrades, loading, activeIndex?.sizing,
       activeIndex?.turnover, resolvedLegs, gasQuote, priceTape]);

  const traderCopyRatio = backtest.copyRatio;
  const backtestHistory = backtest.history;
  const backtestSim: BacktestSim = backtest.sim;

  // ── CAPITAL PLAN input — the flow this strat would actually copy ──
  // Same gate the engine applies (`shouldMirror`: market query + trade filter
  // + trader FILTER), no per-cycle top-N cap: the question "how much money
  // does this need" is about the whole eligible flow, not one cycle's slice.
  // Sizing denominators are the ones `copyRatioFor` divides by, so the
  // recommendation is in the same units the live engine sizes with.
  const capitalPlanInput = useMemo((): CapitalPlanInput => {
    const cutoff = Date.now() - backtestDays * 86400_000;
    const totalW = watchlist.reduce((s, a) => s + (traderWeights[a] || 0), 0) || 1;
    const weightFraction = new Map<string, number>();
    const traderVolume = new Map<string, number>();
    const trades: { trader: string; notional: number; price: number; timestamp: number }[] = [];
    for (const addr of watchlist) {
      const key = addr.toLowerCase();
      const weight = traderWeights[addr] || 0;
      weightFraction.set(key, weight / totalW);
      let buyVol = 0, sellVol = 0;
      for (const t of traderTrades.get(addr) || []) {
        if (t.timestamp < cutoff) continue;
        const notional = t.price * t.size;
        if (marketMatchesQuery(t.market, marketQuery)) {
          if (t.side === "BUY") buyVol += notional; else sellVol += notional;
        }
        if (t.side !== "BUY") continue;
        const stratTrade: StratTraderTrade = {
          ...t, trader: addr, weight, weightFraction: weight / totalW,
          copyRatio: traderCopyRatio.get(addr) ?? 0, notional,
        };
        if (!backtestStrat.shouldMirror(stratTrade, backtestHistory)) continue;
        trades.push({ trader: key, notional, price: t.price, timestamp: t.timestamp });
      }
      traderVolume.set(key, Math.max(buyVol, sellVol));
    }
    return {
      trades, weightFraction, bankrolls: traderBankrolls, traderVolume,
      capital, minTrade, maxTrade,
      // Cap the advice at the deposit wallet's real USDC — a recommendation
      // of "$200k" for a wallet holding $150 is not a plan.
      available: walletBalance,
      maxUpscale,
      sizing: activeIndex?.sizing,
      turnover: activeIndex?.turnover,
      days: backtestDays,
    };
  }, [watchlist, traderTrades, traderWeights, traderCopyRatio, traderBankrolls, backtestStrat,
      backtestHistory, marketQuery, capital, minTrade, maxTrade, walletBalance,
      maxUpscale, activeIndex?.sizing, activeIndex?.turnover, backtestDays]);



  const linkedTrades = backtestSim.rows;
  const backtestRoi = capital > 0 ? (backtestSim.netPnl / capital) * 100 : 0;

  // ── Persist backtest snapshot to SavedIndex for leaderboard ──
  // All values come from the simulated-wallet replay above, so the leaderboard
  // numbers match what the BACKTEST tab actually displays.
  useEffect(() => {
    if (!activeIndex || backtests.length === 0 || loading) return;
    updateIndex(activeIndex.id, {
      lastPnl: backtestSim.grossPnl,
      lastPnlAfterCosts: backtestSim.netPnl,
      lastRoi1k: Math.round(backtestRoi * 100) / 100,
      lastTradeCount: backtestSim.rows.length,
      lastBacktestAt: Date.now(),
    });
  }, [activeIndex, backtestSim, backtestRoi, loading, backtests.length]);

  // ── Chart ↔ Trade feed hover linking ──
  // The equity chart pins its crosshair by TIMESTAMP (EquityChart's
  // highlightT), and every feed row carries its ts — no index translation
  // between two differently-filtered series anymore.
  const [chartHighlightT, setChartHighlightT] = useState<number | null>(null);
  const [tradeHighlight, setTradeHighlight] = useState<number | null>(null);
  // Pinned trade selection — survives mouse-leave (unlike the hover-only
  // chartHighlightT/tradeHighlight pair above, which reset on every
  // mouseleave). Click a row to pin it; click again to unpin. Indexes into
  // `linkedTrades` (the origIdx used by the feed table below).
  const [selectedTradeIdx, setSelectedTradeIdx] = useState<number | null>(null);
  // Lowercase trader address → isolate the feed to just this trader's trades.
  // `null` shows everyone (the default). Click a trader chip or a trader cell
  // in the feed to set; click again or the ALL chip to clear.
  const [feedTraderFilter, setFeedTraderFilter] = useState<string | null>(null);
  const [feedOrder, setFeedOrder] = useState<"newest" | "oldest">("newest");

  // Chart marker hover → highlight the nearest feed row (and vice versa the
  // feed rows set chartHighlightT directly). On leave, fall back to the
  // pinned selection instead of clearing outright.
  const handleChartHover = useCallback((t: number | null) => {
    if (t === null || linkedTrades.length === 0) {
      if (selectedTradeIdx !== null && linkedTrades[selectedTradeIdx]) {
        setChartHighlightT(linkedTrades[selectedTradeIdx].ts);
        setTradeHighlight(selectedTradeIdx);
      } else {
        setChartHighlightT(null);
        setTradeHighlight(null);
      }
      return;
    }
    setChartHighlightT(t);
    let best = 0, bestDist = Infinity;
    for (let i = 0; i < linkedTrades.length; i++) {
      const d = Math.abs(linkedTrades[i].ts - t);
      if (d < bestDist) { bestDist = d; best = i; }
    }
    setTradeHighlight(best);
  }, [linkedTrades, selectedTradeIdx]);

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


  /** The leader's own activity in the loaded window — how many trades came
      back and how long ago the last one was. Two numbers, because "is this
      screen looking at anything at all?" is the first question a silent copy
      session raises, and the answer is usually "they haven't traded". */
  const leaderStats = useMemo(() => {
    if (!leaderAddress) return null;
    const st =
      traderTradeStatsByAddr.get(leaderAddress) ??
      // The map is keyed by the strat's stored casing, which is normally
      // lowercase but need not be for a hand-edited entry.
      [...traderTradeStatsByAddr.entries()].find(
        ([a]) => a.toLowerCase() === leaderAddress,
      )?.[1];
    if (!st) return null;
    return { total: st.total, last: st.lastTs || null };
  }, [leaderAddress, traderTradeStatsByAddr]);



  // How many of the selected traders' in-window trades pass the keyword
  // filter — the "does my filter bite" readout under MARKETS. Recomputes on
  // every keyword edit, same dependency the backtest keys on.
  const keywordStats = useMemo(() => {
    const cutoff = Date.now() - backtestDays * 24 * 60 * 60 * 1000;
    let total = 0;
    let matched = 0;
    for (const addr of watchlist) {
      const trades = traderTrades.get(addr);
      if (!trades) continue;
      for (const t of trades) {
        if (t.timestamp < cutoff) continue;
        total++;
        if (marketMatchesQuery(t.market, marketQuery)) matched++;
      }
    }
    return { matched, total };
  }, [watchlist, traderTrades, backtestDays, marketQuery]);


  // ══════════════════════════════════════════
  // ── RENDER ──
  // ══════════════════════════════════════════

  // ── Subtab rail ──
  //
  //   Defined here, rendered in ONE of two places. Under the three-tab console
  //   (forcedMode) it is the bottom row of the SETTINGS panel: two pills do not
  //   deserve a bordered panel of their own, and stacking one under the page
  //   header, under the nav, put four header bars above the first number on
  //   the screen. Without forcedMode it stays where it was, under the
  //   TEST/LIVE tabs it belongs to.
  const subtabRail = (
    <div key={mode} className="subtab-rail flex items-center gap-1.5 flex-wrap">
      {SUBTABS[mode].map((s) => {
        const active = activeSub === s.id;
        const accent = SUB_ACCENT[mode];
        return (
          <button
            key={s.id}
            onClick={() => pickSub(mode, s.id)}
            title={s.title}
            style={{ fontFamily: '"Space Grotesk", system-ui, sans-serif', letterSpacing: "0.14em" }}
            className={`group inline-flex items-center gap-1.5 px-3 py-1 text-[11px] font-bold uppercase rounded-full border transition-all duration-200 ${
              active
                ? accent
                : "border-pixel-border/50 text-pixel-gray hover:text-pixel-white hover:border-pixel-border hover:bg-pixel-white/[0.04]"
            }`}
          >
            <span className={`font-mono normal-case tracking-normal text-[10px] transition-opacity ${active ? "opacity-90" : "opacity-50 group-hover:opacity-80"}`}>
              {s.glyph}
            </span>
            {s.label}
          </button>
        );
      })}
      {mode === "LIVE" && isLive && engineState && (
        <span
          className="ml-auto text-[12px] font-mono text-pixel-gray shrink-0"
          title="Free cash · time to next poll cycle — full breakdown on the DESK"
        >
          <span className="text-pixel-white">
            {engineState.balance !== null ? `$${engineState.balance.toFixed(2)}` : "$—"}
          </span>
          {" · "}
          <span className="text-green-400">
            {formatCountdown((engineState.nextCycleAt ?? 0) - railNow)}
          </span>
        </span>
      )}
    </div>
  );

  return (
    <div className="min-w-0 space-y-2">
      {/* ── STRAT — the whole editor, above TEST and LIVE ──
          Not a tab: who you copy (add bar, leaderboard browser, watchlist
          rows) and every tuning knob (window / capital / trade band /
          throttle / top-N / sample / poll cadence / market keywords /
          per-trade filters) feed the test AND the live engine, so they sit
          above both instead of behind a mode switch you have to leave to see
          the result. Collapse it (▾) once it's tuned and the charts move up.
          The FILT column on each trader row shows live how many of that
          trader's in-window trades the current params keep. The status row
          keeps the data-freshness chips so a stalled poll loop is still
          diagnosable from here. */}
      {activeIndex && (
        <div className="pixel-panel px-3 py-2.5 space-y-2.5">
          <div className="flex items-center gap-2 flex-wrap">
            <button
              onClick={toggleStrat}
              className="shrink-0 flex items-center gap-2 group/st"
              title={stratOpen ? "Collapse the settings" : "Expand the settings — the leader, and every gate the engine copies them through"}
              aria-expanded={stratOpen}
            >
              <span className={`text-pixel-gray text-[12px] transition-transform duration-200 ${stratOpen ? "rotate-90" : ""}`}>▸</span>
              <span className="text-[14px] text-pixel-white tracking-[0.2em] group-hover/st:text-green-400 transition-colors">SETTINGS</span>
            </button>
            <div className="w-2 h-2 bg-green-400 shrink-0" />
            {/* Just the name. This was a <select> over every saved strat plus
                an inline rename — a global switcher parked inside the strat it
                was switching. A workspace is one copy-book row now, the row is
                the URL, and the name comes from the server (the row's label,
                or "COPY 0xab…cd"), so there is nothing here to pick or edit. */}
            <span
              className="text-[14px] font-mono text-green-400 font-bold truncate"
              title={activeIndex.name}
            >
              {activeIndex.name}
            </span>
            {/* The last saved backtest, as evidence for running this thing —
                and ONLY on the screen that isn't already showing one. On
                BACKTEST it sat one line above the panel's own P&L, two
                numbers under the same word disagreeing with each other
                (a saved run vs the one on screen). */}
            {mode !== "BACKTEST" && activeIndex.lastPnlAfterCosts !== undefined && (
              <span
                className={`text-[13px] font-mono shrink-0 ${activeIndex.lastPnlAfterCosts >= 0 ? "text-green-400" : "text-red-400"}`}
                title="The last backtest saved for this strat — P&L after fees and gas. Re-run it on BACKTEST; a bare number here read as a live balance to more than one person."
              >
                LAST BACKTEST {activeIndex.lastPnlAfterCosts >= 0 ? "+" : "−"}${Math.abs(activeIndex.lastPnlAfterCosts).toFixed(0)}
              </span>
            )}
            {/* The bench, and what it's pointed at — the two facts the page
                header used to repeat one line above this one ("running your
                bench against the live book · 8 traders"). One row now: this
                one. */}
            <Link
              href="/traders"
              className="text-[12px] font-mono text-pixel-gray-light hover:text-pixel-white shrink-0"
              title="Who is on the bench — edit on TRADERS"
            >
              {watchlist.length} {watchlist.length === 1 ? "trader" : "traders"} →
            </Link>
            <span className="text-pixel-border/40 shrink-0">·</span>
            {/* Window + markets: what every number below is measured over.
                Was a second row that only appeared when the panel was
                collapsed, which made the header change height as you opened
                it. */}
            <span
              className="text-[12px] text-pixel-gray/70 font-mono tracking-wider truncate"
              title={`Window the stats below cover — ${backtestDateRange.from} to ${backtestDateRange.to}`}
            >
              {backtestDays}d · {backtestDateRange.from} → {backtestDateRange.to}
            </span>
            <span className="text-pixel-border/40 shrink-0">·</span>
            <span
              className={`text-[12px] font-mono truncate ${marketQuery.trim() ? "text-amber-300" : "text-pixel-gray/70"}`}
              title="Markets copied — expand SETTINGS to change"
            >
              {marketQuery.trim() || "all markets"}
            </span>
            {/* Freshness, pushed to the right edge: identity on the left, "is
                this data moving" on the right. A stalled poll loop is still
                diagnosable from the header without reading past the strat
                name to find it. */}
            <span className="ml-auto flex items-center gap-2 shrink-0">
            {lastUpdated && fetchAgeMs != null && (
              <>
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
                {lastUpdated && fetchAgeMs != null && (
                  <span className="text-pixel-border/40 shrink-0">·</span>
                )}
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
            </span>
          </div>

          {/* No second row when collapsed. It used to restate the market
              filter and the window under the title — a whole extra bar that
              appeared and vanished with the caret. Both facts are in the row
              above now, open or shut. */}

          {stratOpen && (
          <>
          {/* ── WHO THIS COPIES ──
              A readout, not the editor. Picking WHO goes on the bench is the
              TRADERS tab's whole job — this row shows the result, drops a name
              you regret (✕), and the quick-add bar below takes an address you
              already have. There are no weight sliders any more: an index is
              equal-weight and the sizing that matters is the ratio between
              your capital and each leader's book, not a hand-tuned percentage.

              Two shapes, one row. An IDENTITY strat (a copy-desk row) copies
              exactly one person and that person IS its identity — in the URL,
              in the strat id (`copy-<address>`), in the engine's session key
              and in the ledger bucket. A TRADER INDEX copies a bench, each
              name weighted, every mirror sized by this capital against THEIR
              book. Both are ordinary strats to everything downstream. */}
          <div className="border-t border-pixel-border/40 pt-2.5">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
              <span className="text-[11px] font-bold text-pixel-white tracking-[0.26em]">
                {leaderAddress ? "LEADER" : "BENCH"}
              </span>
              {leaderAddress ? (
                <>
                  <button
                    onClick={() => goToTrader(leaderAddress)}
                    className="font-mono text-[14px] text-green-400 hover:text-pixel-white transition-colors"
                    title={`${leaderAddress} — their own trading record`}
                  >
                    {shortAddress(leaderAddress)} ↗
                  </button>
                  {leaderStats && (
                    <span className="font-mono text-[11px] text-pixel-gray">
                      {leaderStats.total} trade{leaderStats.total === 1 ? "" : "s"} in window
                      {leaderStats.last != null && (
                        <> · last {formatAgoShort(Date.now() - leaderStats.last)} ago</>
                      )}
                    </span>
                  )}
                  <span className="flex-1" />
                  <Link
                    href="/copy"
                    className="text-[11px] font-mono text-pixel-gray hover:text-green-400"
                    title="Every leader you copy, with their amounts"
                  >
                    copy desk →
                  </Link>
                </>
              ) : watchlist.length > 0 ? (
                // A trader INDEX. Every name, clickable, in weight order —
                // capital is split across them and each one's mirrors are
                // sized against their own book (see the SCALE card below).
                <>
                  {watchlist.slice(0, 12).map((addr) => (
                    <span key={addr} className="group inline-flex items-center gap-1">
                      <button
                        onClick={() => goToTrader(addr)}
                        className="font-mono text-[12px] text-green-400 hover:text-pixel-white transition-colors"
                        title={`${addr} — their own trading record`}
                      >
                        {shortAddress(addr)}
                      </button>
                      <button
                        onClick={() => removeTrader(addr)}
                        className="text-[11px] text-pixel-gray hover:text-red-400 opacity-0 group-hover:opacity-100 transition-opacity"
                        title="Drop this trader — the rest re-weight evenly"
                      >
                        ✕
                      </button>
                    </span>
                  ))}
                  {watchlist.length > 12 && (
                    <span className="font-mono text-[11px] text-pixel-gray">
                      +{watchlist.length - 12} more
                    </span>
                  )}
                  <span className="flex-1" />
                  <Link
                    href="/traders"
                    className="text-[11px] font-mono text-pixel-gray hover:text-green-400"
                    title="Add or drop traders on this index"
                  >
                    edit the bench →
                  </Link>
                </>
              ) : (
                <span className="text-[11px] font-mono text-amber-400">
                  nobody on the bench —{" "}
                  <Link href="/traders" className="underline">
                    pick some traders
                  </Link>
                </span>
              )}
            </div>
          </div>

          {/* ── PARAMS — every knob; the filters gate the traders above,
              and the FILT column re-counts on every edit here. Folded and
              CLOSED by default: the defaults are the strat, and a wall of
              inputs above the results buried the answer under the tuning. ── */}
          <div className="border-t border-pixel-border/40 pt-2 space-y-2">
            <button
              onClick={toggleParams}
              aria-expanded={paramsOpen}
              className="w-full flex items-baseline gap-2 text-left group py-0.5"
              title="Every knob — sizing, risk, engine cadence, market + trade filters. Every edit re-runs the backtest and steers the live engine."
            >
              <span className={`text-[11px] font-bold tracking-[0.26em] transition-colors ${paramsOpen ? "text-pixel-white" : "text-pixel-gray group-hover:text-pixel-white"}`}>
                PARAMS
              </span>
              <span className="text-[10px] text-pixel-gray/70 truncate min-w-0">
                {paramsOpen
                  ? "apply to the selected traders above — every edit re-runs the backtest and steers the live engine"
                  : "sizing · risk · engine · market + trade filters"}
              </span>
              <span className="ml-auto text-[9px] text-pixel-gray shrink-0">{paramsOpen ? "▲" : "▼"}</span>
            </button>
            {paramsOpen && (
            <div className="grid gap-2 lg:grid-cols-2">
              <ParamGroup title="SIZING" hint="how much each mirror bets">
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

                {/* The knob that decides how much of the filtered flow ever
                    reaches the book. A small account copying big leaders
                    mirrors at cents; every one of those is skipped as
                    SUB_SCALE unless it's allowed to round up to the floor.
                    0 = ∞ = round them ALL up and place them. */}
                <Field label="UPSCALE" suffix="×">
                  <input
                    type="text"
                    inputMode="numeric"
                    value={maxUpscale === 0 ? "∞" : maxUpscale}
                    onChange={(e) => {
                      const raw = e.target.value.trim();
                      if (raw === "∞" || raw === "") return updateMaxUpscale(0);
                      const v = parseInt(raw.replace(/[^0-9]/g, ""), 10);
                      updateMaxUpscale(isNaN(v) ? 0 : v);
                    }}
                    onFocus={(e) => e.target.select()}
                    title="How far a mirror may be rounded UP past its proportional size to clear the order floor. 2 = a mirror may be at most 2× what proportionality asked for; anything smaller is skipped (SUB_SCALE) so a conviction bet and a throwaway punt never get copied at the same size. 0 (∞) = never skip for size — every filtered trade is placed at the floor, which is how a small account copies large leaders at all."
                    className="bg-transparent w-10 text-right font-mono text-[14px] text-pixel-white outline-none"
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
              </ParamGroup>

              <ParamGroup title="RISK" hint="exits & concurrency caps">
                <Field label="STOP" suffix="%">
                  <input
                    type="text"
                    inputMode="numeric"
                    value={stopLossPct}
                    onChange={(e) => {
                      const v = parseInt(e.target.value.replace(/[^0-9]/g, ""), 10);
                      updateStopLossPct(isNaN(v) ? 0 : v);
                    }}
                    onFocus={(e) => e.target.select()}
                    title="Stop-loss: sell a position once it is DOWN this % from entry (10 = exit at a 10% loss, 50 = exit at half of entry) — caps a market trending to 0 at a known loss. 0 = off. Enforced live every scan and replayed identically in the backtest."
                    className="bg-transparent w-8 text-right font-mono text-[14px] text-pixel-white outline-none"
                  />
                </Field>

                <Field label="MAX POS">
                  <input
                    type="text"
                    inputMode="numeric"
                    value={maxOpenPositions}
                    onChange={(e) => {
                      const v = parseInt(e.target.value.replace(/[^0-9]/g, ""), 10);
                      if (!isNaN(v) && v > 0) updateMaxOpenPositions(v);
                    }}
                    onFocus={(e) => e.target.select()}
                    title="Max concurrent open positions — the live engine skips BUYs that would open a new position past this cap"
                    className="bg-transparent w-8 text-right font-mono text-[14px] text-pixel-white outline-none"
                  />
                </Field>

                {/* THROTTLE (maxTradesPerHour) removed — the knob was never
                    enforced by the live engine OR the backtest, so it silently
                    lied about behavior. Rate control is MAX/CYCLE × poll
                    cadence, which both sides actually honor. */}
                {/* The gate that blocks the most flow, and the only one that
                    used to be invisible: a session could report "225 entries
                    seen, none copied" with no knob anywhere in the console to
                    answer it with. 60 = the engine's default. */}
                <Field label="MIN CLOSE" suffix="MIN">
                  <input
                    type="text"
                    inputMode="numeric"
                    value={minMinutesToClose}
                    onChange={(e) => {
                      const v = parseInt(e.target.value.replace(/[^0-9]/g, ""), 10);
                      updateMinMinutesToClose(isNaN(v) ? 0 : v);
                    }}
                    onFocus={(e) => e.target.select()}
                    title="Minimum minutes a market must still have left before a leader's BUY in it can be copied. 60 (default) excludes the 5m/15m/hourly Up-or-Down candles — mirroring those one poll late is a measured loss (−$253 across 1064 such copies). 0 turns the gate off and copies short-dated flow too. Enforced live and replayed identically in the backtest."
                    className="bg-transparent w-9 text-right font-mono text-[14px] text-pixel-white outline-none"
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
              </ParamGroup>

              <ParamGroup title="ENGINE" hint="scan cadence & lookback window">
                <Field label="POLL EVERY">
                  <select
                    value={rebalanceMinutes}
                    onChange={(e) => updateRebalanceMinutes(Number(e.target.value))}
                    className="bg-transparent font-mono text-[13px] text-pixel-white outline-none cursor-pointer pr-1"
                  >
                    {/* Nothing below 30s: the engine's rate-limit floor
                        (MIN_POLL_MINUTES) clamps faster picks, so offering
                        them would misreport the real cadence. */}
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
              </ParamGroup>

              <ParamGroup title="MARKETS" hint="only act on matching market titles">
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

                {/* Does the query bite? Live count of the watched traders'
                    in-window trades it keeps — the one number the old header
                    dropdown existed for, next to the field that sets it. */}
                {keywordStats.total > 0 && (
                  <div className="w-full flex items-center gap-1.5 pt-0.5 text-[10px] font-mono">
                    <span className="text-pixel-gray tracking-[0.18em] leading-none">MATCHES</span>
                    <span className={marketQuery.trim() && keywordStats.matched === 0 ? "text-red-400" : "text-pixel-white"}>
                      {keywordStats.matched}
                    </span>
                    <span className="text-pixel-gray/60">/ {keywordStats.total} trades · {backtestDays}d</span>
                    {marketQuery.trim() && keywordStats.matched === 0 && (
                      <span className="text-red-400/80">nothing to copy — loosen it</span>
                    )}
                  </div>
                )}
              </ParamGroup>

              {/* Per-trade filter — orthogonal to MARKET: gates on the
                  leader's own trade attributes (side / entry price / size /
                  category) rather than the market title. AND-ed together.
                  Backtest + live engine + catch-up all honor these. */}
              <ParamGroup
                title="TRADE FILTER"
                hint="gate on the leader's own trade — side, entry price, size, category"
                className="lg:col-span-2"
              >
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
              </ParamGroup>

              {/* MARKET SENTIMENT — the third gate, and the only one that
                  asks about the MARKET rather than the trade: which way had
                  the crowd moved the odds on the outcome they bought when
                  they bought it. Off by default and free when off; when on,
                  the card prints what it would keep and how much of the flow
                  it can read before anything is armed. */}
              <ParamGroup
                title="MARKET SENTIMENT"
                hint="copy them only when the crowd was moving a particular way"
                className="lg:col-span-2"
              >
                <SentimentCard
                  value={tradeFilters.sentiment}
                  onChange={(next: SentimentFilter | undefined) => patchTradeFilters({ sentiment: next })}
                  state={sentiment}
                  sampleLabel={`${backtestDays}d of bench entries`}
                />
              </ParamGroup>

              {/* No TRADER FILTER card here. It ranked the watched traders
                  every scan and copied only the top N — a gate on WHICH of
                  many leaders to mirror. With exactly one leader it can only
                  ever answer "them" or "nobody", and the second answer is
                  what the enabled switch on the desk is for. */}

              {/* How much money to run it with — derived from this strat's own
                  filtered flow and the engine's real order floors. */}
              <ParamGroup
                title="CAPITAL PLAN"
                hint="what this strat needs to actually place its trades"
                className="lg:col-span-2"
              >
                <CapitalPlanCard
                  input={capitalPlanInput}
                  onUse={updateCapital}
                  onCopyAll={() => updateMaxUpscale(0)}
                  onPlan={(plan) => {
                    if (!activeIndex) return;
                    updateIndex(activeIndex.id, {
                      suggestedCapital: plan.recommendedCapital,
                      suggestedCapitalAt: Date.now(),
                    });
                  }}
                />
              </ParamGroup>

              {/* ── YOUR SCALE ──
                  Only on a TRADER INDEX, and only there because on a
                  conviction-sized strat the leader's net worth is not the
                  denominator and the card would be describing arithmetic the
                  engine isn't doing. This is the strategy made legible: the
                  ratio between this capital and each leader's book, what one
                  of their trades becomes at that ratio, and which of their
                  trades never reach you at this size. See
                  components/IndexScaleCard.tsx. */}
              {activeIndex && isTraderIndex(activeIndex) && (
                <div className="lg:col-span-2">
                  <IndexScaleCard strat={activeIndex} capital={capital} />
                </div>
              )}
            </div>
            )}
          </div>
          </>
          )}

          {/* The rail, as this panel's footer — see `subtabRail` above. */}
          {forcedMode && (
            <>
              <div className="h-px -mx-3 bg-gradient-to-r from-transparent via-pixel-border/70 to-transparent" />
              {subtabRail}
            </>
          )}
        </div>
      )}

      {/* There is no SOURCE or MARKET view under STRAT any more. SOURCE
          showed the strat's code — read-only for the built-in TypeScript
          strats, editable for uploaded mod.py/mod.rs; MARKET was the strat
          shelf: publish yours, browse everyone else's, import one by CID.
          Both belonged to a console where a strat was an artifact you
          authored and traded. What this console runs is a leader and an
          amount, so the code is the engine's, not the user's. Archived under
          `src/_archive` if that changes back. */}

      {/* ── Header: main tabs + subtab rail ──
          Only for the un-pinned embed. Under the three-tab console the routes
          ARE the mode switch, so this whole panel used to render as a bordered
          strip containing nothing but two pills — a fourth header bar above
          the first number on the screen. Pinned, the rail moves into the
          SETTINGS panel's footer instead and this doesn't render at all. Money
          is not here either way — topping up and taking out live in the side
          panel. */}
      {!forcedMode && (
      <div className="pixel-panel px-3 py-2 space-y-2">
        {/* Tabs */}
        <div className="flex items-center gap-2">
          {(
            [
              // BACKTEST/LIVE are always reachable — backtests run on
              // simulated funds (no wallet, no deposit), and an empty
              // watchlist renders an add-traders empty state instead of a
              // dead grey tab (which read as "needs a funded wallet").
              // WALLET is no longer a main tab — it's the $ subtab under
              // LIVE (SUBTABS above), next to the engine it funds. Neither is
              // STRAT: it's the panel above these two, since a strat feeds
              // both of them.
              // These two tabs are SCREENS, not modes. That distinction is the
              // whole point of the rename: the left one was labelled TEST and
              // the right one LIVE, which collided head-on with TEST and LIVE
              // being the engine's two execution modes — so a tab called LIVE
              // routinely sat over a session that had never placed an order,
              // and "test trading" could mean either a backtest or a dry run
              // depending on who you asked.
              //
              // Now: TEST replays history, LIVE runs the engine, and whether
              // the engine's money is real is the DRY RUN|REAL switch inside
              // it (mirrored on the chip below). Ids are unchanged — they key
              // the accent maps, the localStorage subtab keys and every mode
              // check in this file — so `id: "BACKTEST"` reading TEST is
              // deliberate, not a leftover.
              { id: "BACKTEST", label: "BACKTEST", disabled: false },
              { id: "LIVE", label: "LIVE", disabled: false },
            ] as { id: MainTab; label: string; disabled: boolean }[]
          ).map((t) => {
            const active = mode === t.id;
            // Chip on the TRADE tab whenever a session exists — so from the
            // BACKTEST screen you can still see that something is running AND
            // whether it is spending real money. The old chip only said
            // RUNNING, which was the half of the answer nobody was worried
            // about.
            const showRunning = t.id === "LIVE" && (isLive || backendRunning);
            const runState = engineState?.status === "paused"
              ? "PAUSED" as const
              : engineState?.status === "error"
                ? "ERROR" as const
                : "RUNNING" as const;
            return (
              <button
                key={t.id}
                onClick={() => setMode(t.id)}
                disabled={t.disabled}
                style={{ fontFamily: '"Space Grotesk", system-ui, sans-serif', letterSpacing: "0.16em" }}
                className={`relative text-[12.5px] font-bold px-4 py-2 rounded-[var(--radius-sm)] transition-all duration-150 uppercase flex items-center gap-2 ${
                  active
                    ? MAIN_ACTIVE[t.id]
                    : "text-pixel-gray hover:text-pixel-white hover:bg-pixel-white/[0.04]"
                } disabled:opacity-30 disabled:cursor-not-allowed`}
              >
                {t.label}
                {showRunning && (
                  <SessionChip run={runState} mode={modeOf(autoExecute)} className="rounded-full" />
                )}
                <span
                  className={`absolute left-3 right-3 -bottom-px h-[2px] rounded-full transition-all duration-200 ${
                    active ? `${MAIN_BAR[t.id]} opacity-100` : "opacity-0"
                  }`}
                />
              </button>
            );
          })}

        </div>

        {/* ── Subtab rail — contextual second row under the mode tabs. ── */}
        <div className="h-px -mx-1 bg-gradient-to-r from-transparent via-pixel-border/80 to-transparent" />
        {subtabRail}

      </div>
      )}

      {/* The money panels used to render HERE, as a LIVE → WALLET subtab.
          They are in the SIDE PANEL now (components/MoneyBlock.tsx) — see the
          subtab-rail note at the top of this file. */}

      {/* ── Add trader bar (TEST — the strat panel above has its own) ──
          Hidden for origination strats: they copy nobody, so a permanent
          "search traders to add" bar is an invitation to break the strat. */}
      {mode === "BACKTEST" && !originates && (
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

      {/* ── Active view ── */}
      {activeIndex && (
        <>
          {/* Empty trader state — suppressed for ORIGINATION strats, which are
              complete with zero traders. */}
          {watchlist.length === 0 && !originates && (
            // Real empty state, not a nag line: the panel is the CTA. The old
            // copy pointed at a breadcrumb ("ADD TRADERS FROM PARAMS →
            // TRADERS + PARAMS") that named the panel it was sitting under,
            // so the one thing to do next is now a button.
            <div className="pixel-panel py-10 px-4 flex flex-col items-center text-center gap-3">
              <div
                className="w-11 h-11 grid place-items-center rounded-[var(--radius)] text-pixel-gray"
                style={{ border: "1px dashed var(--border-strong)", background: "var(--input-bg)" }}
              >
                <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                  <circle cx="9" cy="8" r="3.4" />
                  <path d="M3.5 19.5c0-3.2 2.5-5.2 5.5-5.2s5.5 2 5.5 5.2" />
                  <path d="M18.5 8.5v5M16 11h5" />
                </svg>
              </div>
              <div className="font-display text-[15px] font-semibold tracking-[0.08em] text-pixel-white">
                NOBODY ON THE BENCH
              </div>
              <div className="text-[12.5px] leading-5 text-pixel-gray-light max-w-[46ch]">
                This strat copies traders and it hasn&apos;t got any, so a test
                would replay an empty tape and a live session would sit idle.
                Put some names on it and everything below fills in.
              </div>
              <Link
                href="/traders"
                className="mt-1 rounded-[var(--radius)] px-4 py-2 text-[12px] font-bold tracking-[0.1em] transition-all hover:brightness-110 hover:-translate-y-px active:translate-y-0"
                style={{
                  color: "#06130c",
                  background: "linear-gradient(180deg, rgb(var(--accent)) 0%, rgb(var(--accent) / 0.82) 100%)",
                  boxShadow: "0 1px 0 rgba(255,255,255,0.25) inset, 0 8px 22px -10px rgb(var(--accent) / 0.55)",
                }}
              >
                FIND TRADERS
              </Link>
              {mode === "BACKTEST" && (
                <div className="text-[11px] text-pixel-gray tracking-wider">
                  A TEST REPLAYS HISTORY ON SIMULATED FUNDS — NO WALLET OR DEPOSIT NEEDED.
                </div>
              )}
            </div>
          )}

          {/* ── BACKTEST panel ───────────────────────────────────────
              RUN + P&L summary + chart + fee row. Always visible on
              BACKTEST tab; LIVE has its own <LivePanel /> rendered
              elsewhere with real-time engine state. Splitting this out
              of the STRAT panel above keeps the param row scannable. */}
          {(watchlist.length > 0 || originates) && mode === "BACKTEST" && backtestSub === "results" && (() => {
            // Same panel the LIVE tab renders — see PerfPanel. Everything
            // mode-specific (RUN / DAYS / FUNDS, the momentum caveat, the
            // fee-drag warning) comes in as slots; the layout does not fork.
            const { fees, gas, volume, grossPnl, skipped, netPnl } = backtestSim;
            const costTotal = fees + gas;
            const costWarning = costTotal > 5 && (grossPnl <= 0 || costTotal > grossPnl * 0.5);
            return (
            <PerfPanel
              label="BACKTEST"
              chartRef={chartPanelRef}
              loading={loading}
              controls={<>
                  <button
                    onClick={() => {
                      const v = parseInt(backtestDaysInput, 10);
                      if (!isNaN(v) && v > 0 && v <= 365) updateBacktestDays(v);
                      setRefreshKey((k) => k + 1);
                    }}
                    className="inline-flex items-center gap-1.5 text-[12px] font-mono tracking-[0.15em] px-3 h-[24px] border border-green-400 text-green-400 hover:bg-green-400/10 active:bg-green-400/20 transition-colors"
                    title="Run the test with current settings"
                  >
                    ▶ RUN
                  </button>
                  <div
                    className="inline-flex items-center gap-1 text-[12px] font-mono h-[24px] px-2 border border-pixel-border/60 text-pixel-gray"
                    title="Test window (days back from now)"
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
                  {/* ── FUNDS source toggle ──
                      Backtests never spend real money. SIM sizes the replay
                      with the CAPITAL param (editable inline, $1K default) so
                      it works with no wallet and no deposit; WALLET pins the
                      sizing to the deposit wallet's live USDC balance so the
                      preview matches an actual deployment. */}
                  <div
                    className="inline-flex items-center gap-1 text-[12px] font-mono h-[24px] px-2 border border-pixel-border/60 text-pixel-gray"
                    title="Funds used to size simulated trades — backtests never spend real money. SIM = paper capital (no wallet needed); WALLET = your live USDC balance."
                  >
                    <span className="tracking-[0.15em]">FUNDS</span>
                    <button
                      type="button"
                      onClick={() => updateFundsMode("SIM")}
                      className={`px-1.5 tracking-wider transition-colors ${
                        fundsMode === "SIM" ? "text-green-400" : "text-pixel-gray hover:text-pixel-white"
                      }`}
                      title="Simulated funds — size trades with the paper CAPITAL amount"
                    >
                      SIM
                    </button>
                    {fundsMode === "SIM" ? (
                      <input
                        type="text"
                        inputMode="numeric"
                        defaultValue={String(capital)}
                        key={`sim-${capital}`}
                        onBlur={(e) => {
                          const v = parseInt(e.target.value.replace(/[^0-9]/g, ""), 10);
                          if (!isNaN(v) && v > 0) updateCapital(v);
                        }}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") (e.target as HTMLInputElement).blur();
                        }}
                        onFocus={(e) => e.target.select()}
                        className="bg-transparent w-12 text-right font-mono text-[13px] text-green-400 outline-none"
                        title="Simulated capital in USD — persists to the strat's CAPITAL param"
                      />
                    ) : (
                      <span className="w-12 text-right text-pixel-gray">${capital}</span>
                    )}
                    <button
                      type="button"
                      onClick={() => updateFundsMode("WALLET")}
                      disabled={walletBalance == null || walletBalance <= 0}
                      className={`px-1.5 ml-1 tracking-wider border-l border-pixel-border/40 pl-2 transition-colors disabled:opacity-30 disabled:cursor-not-allowed ${
                        fundsMode === "WALLET" ? "text-green-400" : "text-pixel-gray hover:text-pixel-white"
                      }`}
                      title={
                        walletBalance == null
                          ? "Connect a wallet to size against your real balance — SIM backtests work without one"
                          : walletBalance <= 0
                            ? "Wallet is unfunded — backtests still run on simulated funds"
                            : `Size trades with your live USDC balance ($${walletBalance.toFixed(2)})`
                      }
                    >
                      WALLET{walletBalance != null && walletBalance > 0 ? ` $${Math.floor(walletBalance)}` : ""}
                    </button>
                  </div>
              </>}
              notices={<>
                {/* Origination strats ARE replayed now (lib/originationBacktest.ts):
                    the cycle loop runs over the window's price tape and calls the
                    same propose() hook the live engine calls. What this notice says
                    is the honest remainder — where the replay is still coarser than
                    a live session — because a backtest that hides its own resolution
                    is how a strat gets deployed on a number it never earned. */}
                {activeIndex?.momentum && (
                  <div className="text-[11px] font-mono tracking-[0.1em] px-2 py-1.5 border border-amber-400/50 text-amber-400/90 bg-amber-400/5">
                    {tapeLoading ? (
                      <>⏳ LOADING THE PRICE TAPE — an origination strat is replayed
                      against the window&apos;s own odds, so the curve appears once the
                      tape lands.</>
                    ) : priceTape && priceTape.markets > 0 ? (
                      <>⚠ ORIGINATION REPLAY · {priceTape.markets}/{priceTape.expected}{" "}
                      {priceTape.mode === "candles" ? "candles" : "markets"} at{" "}
                      {Math.round(priceTape.fidelityMs / 60_000)}-minute bars. The engine
                      polls every{" "}
                      {Math.max(MIN_POLL_MINUTES, activeIndex?.livePollMinutes ?? MIN_POLL_MINUTES) * 60}s
                      and also reads the CLOB midpoint, so it sees moves BETWEEN these
                      bars that this replay cannot — treat entry/exit timing as coarser
                      than live, and the spread as unmodeled on both sides.</>
                    ) : (
                      <>⚠ NO PRICE TAPE FOR THIS WINDOW — this strat trades a market&apos;s
                      own odds, and none came back for the window selected, so there is
                      nothing to replay. {priceTape?.note ?? "Try a shorter window, or run the engine on PAPER against the live feed."}</>
                    )}
                  </div>
                )}
                {costWarning && !loading && (
                  <div className="px-3 py-2 border border-amber-400/40 bg-amber-400/5">
                    <div className="text-[13px] text-amber-400 font-mono">
                      {costTotal > grossPnl && grossPnl > 0
                        ? `FEES ($${costTotal.toFixed(0)}) EXCEED GROSS P&L (${formatPnl(grossPnl)}) — COPYING ${watchlist.length} TRADERS AT ${linkedTrades.length} TXS IS UNPROFITABLE AFTER COSTS`
                        : grossPnl <= 0
                          ? `STRAT IS NEGATIVE AND INCURS $${costTotal.toFixed(0)} IN FEES/GAS ACROSS ${linkedTrades.length} TXS`
                          : `FEES CONSUME ${Math.round((costTotal / grossPnl) * 100)}% OF GROSS PROFIT — CONSIDER FEWER TRADERS OR HIGHER-CONVICTION PICKS`
                      }
                    </div>
                  </div>
                )}
              </>}
              stats={{
                equity: backtestSim.cash + backtestSim.posValue,
                cash: backtestSim.cash,
                positions: backtestSim.posValue,
                unrealized: backtestSim.unrealized,
                unrealizedPct: backtestSim.costBasis > 0
                  ? (backtestSim.unrealized / backtestSim.costBasis) * 100
                  : null,
                pnl: netPnl,
                roiPct: capital > 0 ? backtestRoi : null,
                pnlTitle: "Final simulated equity minus starting capital — fees and gas already paid out of cash. Same accounting as the LIVE portfolio curve.",
              }}
              costs={{
                amount: volume,
                fees,
                gas,
                breakdown: backtestSim.costs,
                txs: linkedTrades.length,
                gross: grossPnl,
                skipped,
                skippedTitle: `Trades the simulated wallet could NOT place: below the $${minTrade} MIN size, more than the remaining cash, or selling a position it never opened. Raise CAPITAL or lower MIN to execute more — SHOW ALL in the feed previews them.`,
                settled: {
                  resolved: backtestSim.settlement.resolved,
                  marked: backtestSim.settlement.marked,
                  markedUsd: backtestSim.settlement.markedUsd,
                },
                tape: tapeChip,
              }}
              caption={`${backtestDays}D SIMULATED EQUITY`}
              history={backtestSim.equityHistory}
              markers={backtestSim.markers}
              highlightT={chartHighlightT}
              onHoverMarker={handleChartHover}
              emptyHint={originates && watchlist.length === 0
                ? (tapeLoading
                    ? "LOADING THE WINDOW'S PRICE TAPE — THE ORIGINATION REPLAY RUNS ON THE MARKET'S OWN ODDS."
                    : priceTape && priceTape.markets > 0
                      ? "PRICE TAPE REPLAYED — THIS STRAT'S SIGNAL NEVER FIRED IN THIS WINDOW. WIDEN THE WINDOW OR LOOSEN THE ENTRY BAND."
                      : `NO PRICE TAPE FOR THIS WINDOW${priceTape?.note ? ` — ${priceTape.note.toUpperCase()}` : "."}`)
                : "NOT ENOUGH TRADE DATA FOR THE EQUITY CURVE — ADD TRADERS OR WIDEN THE WINDOW."}
              positions={backtestSim.open}
              positionsNote="simulated holds · worst first"
            />
            );
          })()}

          {/* ── BACKTEST → TRADES subtab — every trade with its P&L impact ── */}
          {mode === "BACKTEST" && backtestSub === "trades" && (() => {
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
                            <th className="text-right whitespace-nowrap" title="Probability of success the playbook priced this BUY at — the trader's Laplace-smoothed 30d win rate. Ranking score = P × ROI × mirror $.">P WIN</th>
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
                                  setChartHighlightT(t.ts);
                                  setTradeHighlight(origIdx);
                                  chartPanelRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
                                }}
                                onMouseEnter={() => {
                                  setChartHighlightT(t.ts);
                                  setTradeHighlight(origIdx);
                                }}
                                onMouseLeave={() => {
                                  // Fall back to the pinned selection (if any) instead of
                                  // clearing outright, so a selected trade stays visible
                                  // against the chart once the mouse leaves the row.
                                  if (selectedTradeIdx !== null) {
                                    const sel = linkedTrades[selectedTradeIdx];
                                    setChartHighlightT(sel ? sel.ts : null);
                                    setTradeHighlight(selectedTradeIdx);
                                  } else {
                                    setChartHighlightT(null);
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
                                <td
                                  className={`text-right font-mono whitespace-nowrap ${
                                    t.successProb === undefined ? "text-pixel-gray"
                                      : t.successProb >= 0.6 ? "text-green-400"
                                      : t.successProb >= 0.5 ? "text-pixel-white"
                                      : "text-red-400"
                                  }`}
                                  title={
                                    t.successProb === undefined
                                      ? "Exits aren't ranked — probability applies to BUY candidates"
                                      : `P(success) ${(t.successProb * 100).toFixed(0)}% — trader's smoothed 30d win rate${t.score !== undefined ? ` · rank score $${t.score.toFixed(2)} = P × ROI × mirror $` : ""}`
                                  }
                                >
                                  {t.successProb === undefined ? "—" : `${Math.round(t.successProb * 100)}%`}
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
                                  {Math.round(t.price * 100)}¢
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

          {/* ── Live Panel — controlled by the header's subtab rail; the
              WALLET subtab renders its own panels above instead. ── */}
          {mode === "LIVE" && (watchlist.length > 0 || originates) && (
            <LivePanel
              tab={liveSub}
              onTabChange={(t) => pickSub("LIVE", t)}
              // Open the MONEY drawer over this screen instead of navigating
              // away from a running session to a funding tab.
              onFundNow={() => window.dispatchEvent(new Event(OPEN_MONEY_EVENT))}
            />
          )}
        </>
      )}

    </div>
  );
}
