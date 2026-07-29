"use client";

import { useState, useEffect, useMemo, useCallback, useRef, type ReactNode } from "react";
import nextDynamic from "next/dynamic";
import { useRouter } from "next/navigation";
import { fetchPositions, fetchWalletTradesUntil, fetchWalletTradesIncremental, formatVolume, formatPnl, fetchTradersPage, fetchTopTraderAddresses, TopTrader, CATEGORIES } from "../lib/polymarket";
import { PolymarketPosition, PolymarketTrade, SavedIndex, TraderRoiStats, TradeFilters } from "../lib/types";
import { tradeMatchesFilters, tradeFiltersActive } from "../lib/tradeFilters";
import { Strat, clobMinNotional, statsFromReturns, stopLossTriggered, takeProfitTriggered, successProbability, DEFAULT_STOP_LOSS, DEFAULT_TAKE_PROFIT, REBALANCE_MARGIN_PCT } from "../lib/strats/strat";
import type { TraderTrade as StratTraderTrade, StratHistory } from "../lib/strats/strat";
import { marketMatchesQuery } from "../lib/marketQuery";
import { shortAddress } from "@/lib/auth";
import { useFilterParams, useFilters } from "../context/FiltersContext";
import { useAuth } from "../context/AuthContext";
import { useCopyEngine } from "../context/CopyEngineContext";
import CopyTrading from "./CopyTrading";
import EquityChart, { type EquitySnapshot, type EquityMarker } from "./EquityChart";
import type { CurvePoint } from "./PnlChart";
import { computeFifoTrades, buildPnlCurve, buildCombinedPnlCurve, aggregateToRebalanceWindows } from "../lib/pnlEngine";
import { loadIndexes, saveIndex, deleteIndex, updateIndex, getActiveIndexId, setActiveIndexId, equalWeightTraders } from "../lib/indexStore";
import { pushStrat } from "../lib/stratSync";
import LivePanel, { type LiveTab } from "./LivePanel";
// Client-only: the `?raw` source imports resolve to different strings in the
// server and client bundles (server sees a shorter transform), so SSR-ing the
// viewer text-mismatches on hydration (React #425). No SEO value in strat
// source — skip SSR entirely.
const StratSourceViewer = nextDynamic(() => import("./StratSourceViewer"), { ssr: false });
import WalletTokenPanel from "./WalletTokenPanel";
import WalletPanel from "./WalletPanel";
import WalletFundingPanel from "./WalletFundingPanel";
import PolymarketAccountPanel from "./PolymarketAccountPanel";

// ══════════════════════════════════════════
// ── Subtab rail — second-level nav under the main STRAT / BACKTEST / LIVE
//    tabs. WALLET lives HERE (under LIVE) instead of eating a main tab: it
//    funds the engine, so it sits next to the engine. Each main tab gets its
//    own accent so the rail reads as a mode switch, not just more buttons.
// ══════════════════════════════════════════
type MainTab = "STRATS" | "BACKTEST" | "LIVE";
type StratSub = "build" | "source";
type BacktestSub = "results" | "trades";
type LiveSub = LiveTab | "wallet";

const SUBTABS: Record<MainTab, { id: string; glyph: string; label: string; title: string }[]> = {
  STRATS: [
    { id: "build", glyph: "◈", label: "BUILD", title: "Traders + params — who you copy and every tuning knob" },
    { id: "source", glyph: "</>", label: "SOURCE", title: "The strat's code — read-only built-ins, editable uploads" },
  ],
  BACKTEST: [
    { id: "results", glyph: "◔", label: "RESULTS", title: "Run the sim — P&L, fees, simulated equity curve" },
    { id: "trades", glyph: "⇄", label: "TRADES", title: "Every simulated trade with its P&L impact" },
  ],
  LIVE: [
    { id: "portfolio", glyph: "◔", label: "PORTFOLIO", title: "Equity + performance curve over time" },
    { id: "stats", glyph: "σ", label: "STATS", title: "Engine stats — free cash, orders, volume, cycles, last sync" },
    // POSITIONS folded into TRADES — one tab toggles positions ⇄ my fills ⇄
    // copied-trader feed ⇄ engine log (same on-chain record at different grain).
    { id: "trades", glyph: "⇄", label: "TRADES", title: "My positions · my fills · copied-trader feed · engine log" },
    { id: "wallet", glyph: "$", label: "WALLET", title: "Deposit / withdraw / bridge — the wallet the engine trades through" },
    { id: "help", glyph: "?", label: "HELP", title: "Which wallet do I use?" },
  ],
};

// Active-pill accents (static strings — Tailwind can't see computed classes).
// WALLET always glows amber ($$$) no matter which mode owns it.
const SUB_ACCENT: Record<MainTab, string> = {
  STRATS: "border-green-400/60 text-green-400 bg-green-400/[0.08] shadow-[0_0_16px_rgba(74,222,128,0.25)]",
  BACKTEST: "border-amber-400/60 text-amber-400 bg-amber-400/[0.08] shadow-[0_0_16px_rgba(251,191,36,0.25)]",
  LIVE: "border-cyan-400/60 text-cyan-300 bg-cyan-400/[0.08] shadow-[0_0_16px_rgba(34,211,238,0.25)]",
};
const WALLET_ACCENT = "border-amber-400/60 text-amber-400 bg-amber-400/[0.08] shadow-[0_0_16px_rgba(251,191,36,0.25)]";

// Main-tab accents mirror the subtab rail's per-mode tones so STRAT / BACKTEST
// / LIVE read as three distinct modes from the top row alone.
const MAIN_ACTIVE: Record<MainTab, string> = {
  STRATS: "text-green-400 bg-green-400/[0.08]",
  BACKTEST: "text-amber-400 bg-amber-400/[0.08]",
  LIVE: "text-cyan-300 bg-cyan-400/[0.08]",
};
const MAIN_BAR: Record<MainTab, string> = {
  STRATS: "bg-green-400 shadow-[0_0_10px_rgba(74,222,128,0.7)]",
  BACKTEST: "bg-amber-400 shadow-[0_0_10px_rgba(251,191,36,0.7)]",
  LIVE: "bg-cyan-400 shadow-[0_0_10px_rgba(34,211,238,0.7)]",
};

const SUB_KEYS: Record<MainTab, string> = {
  STRATS: "polymarket.sub.strat",
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

// Polymarket costs — kept at LIVE-engine parity. The CLOB charges no
// taker/maker fee on these markets (the engine places every order with
// fee_rate_bps 0) and proxy-wallet trades are relayer-paid, i.e. gasless for
// the user. The sim books the same zero costs so the backtest curve and a
// live session measure the same thing; real friction (spread, unfilled GTC
// limits) is unmodeled on BOTH sides. Bump these only if Polymarket starts
// charging AND the live engine models the same charge.
const TAKER_FEE_BPS = 0;
const GAS_PER_TRADE_USD = 0;
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

/* ── Header keyword-filter dropdown ──
   The strat's market-topic filter, surfaced in the header as editable chips.
   Comma/pipe groups OR, tokens within a group AND (lib/marketQuery.ts) — the
   exact query the backtest AND live engine already gate on. Every add /
   remove / edit commits immediately, which flips `marketQuery`, which every
   backtest useMemo depends on → the backtest re-runs on the spot and the
   MATCHES counter shows the new slice instantly. */
function KeywordFilterDropdown({ query, onCommit, matched, total, onViewBacktest, canBacktest }: {
  query: string;
  onCommit: (q: string) => void;
  matched: number;
  total: number;
  onViewBacktest: () => void;
  canBacktest: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [input, setInput] = useState("");
  const boxRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const groups = query.split(/[,|]/).map((s) => s.trim()).filter(Boolean);
  const commitGroups = (gs: string[]) => onCommit(gs.map((g) => g.trim()).filter(Boolean).join(", "));
  const addFromInput = () => {
    const g = input.trim();
    if (!g) return;
    commitGroups([...groups, g]);
    setInput("");
  };
  // Click a chip to edit it: pull it out of the query and into the input.
  const editChip = (i: number) => {
    setInput(groups[i]);
    commitGroups(groups.filter((_, j) => j !== i));
    inputRef.current?.focus();
  };

  const active = groups.length > 0;
  const pct = total > 0 ? Math.round((matched / total) * 100) : 0;

  const PRESETS: readonly [string, string][] = [
    ["Bitcoin", "bitcoin, btc"],
    ["Ethereum", "ethereum, eth"],
    ["Solana", "solana, sol"],
    ["Crypto", "bitcoin, btc, ethereum, eth, solana, sol, crypto, xrp, dogecoin"],
  ];

  return (
    <div ref={boxRef} className="relative ml-auto">
      <button
        onClick={() => setOpen((v) => !v)}
        title={active
          ? `Copying only markets matching: ${query} — ${matched}/${total} trades pass. Click to edit; the backtest re-runs on every change.`
          : "Filter the selected traders' trades by market keywords — the backtest re-runs on every edit."}
        style={{ fontFamily: '"Space Grotesk", system-ui, sans-serif', letterSpacing: "0.14em" }}
        className={`flex items-center gap-2 text-[11.5px] font-bold px-3 py-1.5 rounded-[var(--radius-sm)] border uppercase transition-all duration-150 ${
          active
            ? "border-amber-400/60 text-amber-300 bg-amber-400/[0.08] shadow-[0_0_14px_-6px_rgba(251,191,36,0.6)]"
            : "border-pixel-border text-pixel-gray hover:text-pixel-white hover:border-pixel-white/40"
        }`}
      >
        <span className="text-[13px] leading-none">⌕</span>
        KEYWORDS
        {active && (
          <span className="text-[9.5px] px-1.5 py-0.5 rounded-full border border-amber-400/50 bg-amber-400/10 font-mono tracking-normal">
            {groups.length}
          </span>
        )}
        <span className={`text-[9px] transition-transform duration-150 ${open ? "rotate-180" : ""}`}>▾</span>
      </button>

      {open && (
        <div className="absolute right-0 top-full mt-2 w-[360px] max-w-[92vw] z-50 border border-pixel-border bg-pixel-black/95 backdrop-blur-md rounded-[var(--radius-sm)] shadow-[0_18px_50px_-12px_rgba(0,0,0,0.9)] p-3 space-y-2.5">
          <div className="flex items-center justify-between">
            <span className="text-[10px] text-pixel-gray tracking-[0.2em]">TRADE KEYWORD FILTER</span>
            <span
              className={`text-[10px] font-mono px-1.5 py-0.5 rounded border ${
                !active ? "border-pixel-border text-pixel-gray"
                : matched > 0 ? "border-green-400/50 text-green-400 bg-green-400/[0.06]"
                : "border-red-400/50 text-red-400 bg-red-400/[0.06]"
              }`}
              title="Trades from the selected traders (inside the backtest window) that pass this filter — recomputed live as you edit."
            >
              {active ? `${matched}/${total} trades · ${pct}%` : `${total} trades · all`}
            </span>
          </div>

          {/* Chips — click to edit, ✕ to drop. Both commit instantly. */}
          <div className="flex items-center gap-1.5 flex-wrap min-h-[24px]">
            {groups.length === 0 && (
              <span className="text-[11px] text-pixel-gray/60 font-mono">no keywords — copying every market the traders touch</span>
            )}
            {groups.map((g, i) => (
              <span key={`${g}-${i}`} className="group inline-flex items-center gap-1 text-[11px] font-mono pl-2 pr-1 py-0.5 rounded border border-amber-400/50 bg-amber-400/[0.08] text-amber-300">
                <button onClick={() => editChip(i)} title="Edit this keyword group" className="hover:text-amber-100">
                  {g}
                </button>
                <button
                  onClick={() => commitGroups(groups.filter((_, j) => j !== i))}
                  title="Remove — backtest re-runs immediately"
                  className="px-0.5 text-amber-400/60 hover:text-red-400"
                >
                  ✕
                </button>
              </span>
            ))}
          </div>

          {/* Add / edit input — Enter commits a group, backtest re-runs. */}
          <div className="flex items-center gap-1.5 h-[30px] px-2 border border-pixel-border bg-pixel-black/60 focus-within:border-amber-400/60 transition-colors">
            <span className="text-[12px] text-pixel-gray">⌕</span>
            <input
              ref={inputRef}
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") addFromInput(); }}
              placeholder='add keywords… e.g. "price bitcoin"'
              className="bg-transparent flex-1 font-mono text-[12.5px] text-pixel-white outline-none placeholder:text-pixel-gray/40"
            />
            <button
              onClick={addFromInput}
              disabled={!input.trim()}
              title="Add keyword group (Enter) — backtest re-runs immediately"
              className="text-[11px] font-bold px-2 py-0.5 rounded border border-green-400/50 text-green-400 bg-green-400/[0.06] hover:bg-green-400/[0.14] disabled:opacity-30 transition-colors"
            >
              + ADD
            </button>
          </div>
          <p className="text-[9.5px] text-pixel-gray/60 leading-relaxed font-mono">
            space = AND within a group · each chip ORs · applies to backtest + live
          </p>

          {/* Presets + clear */}
          <div className="flex items-center gap-1.5 flex-wrap">
            {PRESETS.map(([label, q]) => {
              const presetActive = query.trim().toLowerCase() === q;
              return (
                <button
                  key={label}
                  onClick={() => onCommit(presetActive ? "" : q)}
                  title={`Only copy markets matching: ${q}`}
                  className={`text-[10px] px-2 py-0.5 rounded border font-bold transition-colors ${
                    presetActive
                      ? "border-amber-400/70 bg-amber-500/20 text-amber-300"
                      : "border-pixel-border bg-pixel-black/60 text-pixel-gray-light hover:border-pixel-white/40"
                  }`}
                >
                  {label}
                </button>
              );
            })}
            {active && (
              <button
                onClick={() => onCommit("")}
                title="Clear every keyword — copy all markets again"
                className="text-[10px] px-2 py-0.5 rounded border border-pixel-border bg-pixel-black/60 text-pixel-gray hover:text-red-400 hover:border-red-400/50 transition-colors"
              >
                ✕ CLEAR
              </button>
            )}
          </div>

          <button
            onClick={() => { onViewBacktest(); setOpen(false); }}
            disabled={!canBacktest}
            className="w-full text-[11px] font-bold tracking-[0.16em] px-3 py-1.5 rounded border border-green-400/50 text-green-400 bg-green-400/[0.06] hover:bg-green-400/[0.14] disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            title="The backtest already re-ran with these keywords — jump to the charts"
          >
            VIEW BACKTEST →
          </button>
        </div>
      )}
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
  // Max concurrent open positions — the live engine skips a mirror BUY that
  // would open a NEW token while this many are already held.
  const [maxOpenPositions, setMaxOpenPositions] = useState(10);
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
  const [mode, setMode] = useState<MainTab>("STRATS");

  // ── Subtabs — one remembered position per main tab, so flipping
  // STRAT → LIVE → STRAT lands back where you were. Read after mount
  // (not in the initializer) to dodge a hydration mismatch; writes are
  // quota-safe — modc2.com modules share one localStorage origin.
  const [stratSub, setStratSub] = useState<StratSub>("build");
  const [backtestSub, setBacktestSub] = useState<BacktestSub>("results");
  const [liveSub, setLiveSub] = useState<LiveSub>("portfolio");
  useEffect(() => {
    try {
      const ss = localStorage.getItem(SUB_KEYS.STRATS);
      if (ss === "build" || ss === "source") setStratSub(ss);
      const bs = localStorage.getItem(SUB_KEYS.BACKTEST);
      if (bs === "results" || bs === "trades") setBacktestSub(bs);
      const ls = localStorage.getItem(SUB_KEYS.LIVE);
      // "positions" was folded into the TRADES tab — migrate any persisted
      // value so an old localStorage entry doesn't select a dead subtab.
      const lsMapped = ls === "positions" ? "trades" : ls;
      if (lsMapped && ["portfolio", "stats", "trades", "wallet", "help"].includes(lsMapped)) setLiveSub(lsMapped as LiveSub);
    } catch { /* storage unavailable — keep defaults */ }
  }, []);
  const pickSub = useCallback((m: MainTab, id: string) => {
    if (m === "STRATS") setStratSub(id as StratSub);
    else if (m === "BACKTEST") setBacktestSub(id as BacktestSub);
    else setLiveSub(id as LiveSub);
    try { localStorage.setItem(SUB_KEYS[m], id); } catch { /* quota full — non-fatal */ }
  }, []);
  const activeSub = mode === "STRATS" ? stratSub : mode === "BACKTEST" ? backtestSub : liveSub;

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

  // ── Embedded top-traders leaderboard (STRATS mode) ──
  // The standalone /traders page was folded into strat management: discovering
  // who to copy and assembling a strat now live in one place. The leaderboard
  // reads the shared TopBar filters (category / window / market topic), and
  // selecting a trader toggles them in/out of the active strat.
  // The add-trader bar lives inside this collapsible too, so remember the
  // expand/contract choice across reloads. Read after mount (not in the
  // useState initializer) to avoid a hydration mismatch, and keep writes
  // quota-safe — modc2.com modules share one localStorage origin.
  const [browseOpen, setBrowseOpen] = useState(false);
  useEffect(() => {
    try {
      if (localStorage.getItem("polymarket.stratTradersOpen") === "1") setBrowseOpen(true);
    } catch { /* storage unavailable — stay collapsed */ }
  }, []);
  useEffect(() => {
    try {
      localStorage.setItem("polymarket.stratTradersOpen", browseOpen ? "1" : "0");
    } catch { /* quota full — non-fatal, just don't persist */ }
  }, [browseOpen]);
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
    // Backtests are always paper — a strat carrying capital 0 (persisted
    // from an unfunded wallet or imported that way) would zero every
    // simulated trade, so fall back to the $1K default.
    setCapital(activeIndex.capital && activeIndex.capital > 0 ? activeIndex.capital : DEFAULT_CAPITAL);
    // Funds source (SIM vs WALLET) — the wallet-balance sync effect below
    // overwrites `capital` with the live balance when this lands on WALLET.
    setFundsMode(activeIndex.fundsMode === "WALLET" ? "WALLET" : "SIM");
    setMinTrade(activeIndex.minTrade ?? 5);
    setMaxTrade(activeIndex.maxTrade ?? 100);
    setMaxPerCycle(activeIndex.maxPerCycle ?? 3);
    setMaxOpenPositions(activeIndex.maxOpenPositions ?? 10);
    setStopLossPct(stopLossFracToLossPct(activeIndex.stopLoss));
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

  const updateMaxOpenPositions = (n: number) => {
    const clamped = Math.max(1, n);
    setMaxOpenPositions(clamped);
    if (activeIndex) {
      updateIndex(activeIndex.id, { maxOpenPositions: clamped, updatedAt: Date.now() });
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
        setCapital(found.capital && found.capital > 0 ? found.capital : DEFAULT_CAPITAL);
        setMinTrade(found.minTrade ?? 5);
        setMaxTrade(found.maxTrade ?? 100);
        setMaxPerCycle(found.maxPerCycle ?? 3);
        setMaxOpenPositions(found.maxOpenPositions ?? 10);
        setStopLossPct(stopLossFracToLossPct(found.stopLoss));
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
    // Hand the strat's semantic per-trade filters to the profile page (?tf=)
    // so it opens showing exactly the slice of this trader's flow the strat
    // would copy — not their whole history.
    const qs = new URLSearchParams(filterQs);
    if (tradeFiltersActive(tradeFilters)) {
      qs.set("tf", JSON.stringify(tradeFilters));
      if (activeIndex?.name) qs.set("tfn", activeIndex.name);
    }
    const s = qs.toString();
    router.push(`/traders/${addr}${s ? `?${s}` : ""}`);
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
  // The live engine instantiates its OWN copy (so the backtest can
  // re-render without disturbing the live cycle's state), but both
  // construct the SAME standard Strat class (src/app/app/lib/strats/
  // strat.ts) with the same params — what you backtest is what trades.
  const backtestStrat = useMemo(
    () => new Strat({ maxPerCycle, marketQuery, tradeFilters }),
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
      out.set(addr, {
        address: addr.toLowerCase(),
        windowDays: 30,
        ...statsFromReturns(returns),
        cashDeployed,
        syncedAt: Date.now(),
      });
    }
    return out;
  }, [watchlist, traderTrades, traderData, marketQuery]);

  // ── Per-trader copy ratio — same formula the live engine hands hooks:
  // (capital × weightFraction) / max(buyVol, sellVol) over the window.
  // The backtest used to pass copyRatio: 0 into scoreCandidate, which made
  // every BUY score roi × notional × 0 = 0 — and the top-N filter only keeps
  // scores > 0, so NO buy ever survived and every backtest executed 0 trades
  // (the "P&L -$3.64 · 0 TXS · GROSS +$1.56" header nonsense).
  const traderCopyRatio = useMemo(() => {
    const out = new Map<string, number>();
    const cutoff = Date.now() - backtestDays * 86400_000;
    for (const addr of watchlist) {
      let buyVol = 0, sellVol = 0;
      for (const t of traderTrades.get(addr) || []) {
        if (t.timestamp < cutoff || !marketMatchesQuery(t.market, marketQuery)) continue;
        const v = t.price * t.size;
        if (t.side === "BUY") buyVol += v; else sellVol += v;
      }
      const wFrac = totalWeight > 0 ? (traderWeights[addr] || 0) / totalWeight : 1 / watchlist.length;
      out.set(addr, (capital * wFrac) / Math.max(buyVol, sellVol, 1));
    }
    return out;
  }, [watchlist, traderTrades, traderWeights, totalWeight, backtestDays, capital, marketQuery]);

  // ── Backtest StratHistory — same shape the live engine hands hooks ──
  // Every strat hook takes the full observed history, so history-aware
  // strats score identically in backtest and live. Positions/balance are
  // unknowable in a backtest preview — empty/null by contract.
  const backtestHistory = useMemo((): StratHistory => {
    const totalW = watchlist.reduce((s, a) => s + (traderWeights[a] || 0), 0) || 1;
    const windowCutoffMs = Date.now() - backtestDays * 86400_000;
    const trades: StratTraderTrade[] = [];
    const stats: Record<string, TraderRoiStats> = {};
    for (const addr of watchlist) {
      const weight = traderWeights[addr] || 0;
      const st = traderStatsMap.get(addr);
      if (st) stats[addr.toLowerCase()] = st;
      for (const t of traderTrades.get(addr) || []) {
        if (t.timestamp < windowCutoffMs) continue;
        trades.push({
          ...t, trader: addr, weight, weightFraction: weight / totalW,
          copyRatio: traderCopyRatio.get(addr) ?? 0, notional: t.price * t.size,
        });
      }
    }
    trades.sort((a, b) => b.timestamp - a.timestamp);
    return {
      trades,
      traderStats: stats,
      positions: [],
      balance: null,
      capital,
      watchlist: watchlist.map((a) => ({ address: a, weight: traderWeights[a] || 0 })),
      cycle: 0,
      now: Date.now(),
    };
  }, [watchlist, traderTrades, traderStatsMap, traderWeights, backtestDays, capital, traderCopyRatio]);

  // ── Top-N sampling: which BUY IDs survive the strat's filter ──
  // Goes through the SAME strat class the live engine uses (registry).
  // Swapping the strat in registry.ts updates BOTH live behavior AND
  // this backtest preview — no separate inline math to keep in sync.
  const keptBuyIds = useMemo(() => {
    if (watchlist.length === 0) return new Set<string>();
    const strat = backtestStrat;
    // One bucket = one live engine cycle. Prefer the strat's LIVE poll
    // cadence (what the engine actually runs at) over the backtest-only
    // rebalanceMinutes knob; both share the engine's 60s minIntervalMs
    // floor, so a 5s UI setting still buckets at the 1-min clamp live
    // enforces.
    const pollMin = activeIndex?.livePollMinutes ?? rebalanceMinutes ?? 1;
    const cycleBucketMs = Math.max(60_000, Math.round((pollMin || 1) * 60_000));
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
          copyRatio: traderCopyRatio.get(addr) ?? 0, notional: t.price * t.size,
        };
        // Same pre-filter the live engine applies — a filtered BUY never
        // enters the rank race, so it drops out of the backtest curve too.
        if (!strat.shouldMirror(stratTrade, backtestHistory)) continue;
        const score = strat.scoreCandidate(stratTrade, stats, backtestHistory);
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
  }, [watchlist, traderTrades, traderStatsMap, rebalanceMinutes, activeIndex?.livePollMinutes, backtestDays, backtestStrat, backtestHistory, traderWeights, traderCopyRatio]);

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
    conditionId: string;
    trader: string;
    side: "BUY" | "SELL";
    amount: number;       // executed notional at user scale ($)
    price: number;        // trade price (0-1)
    fee: number;          // trading fee ($)
    realized: number;     // realized PnL on SELL vs avg entry (user scale)
    runningPnl: number;   // simulated equity − starting capital (MTM, net of costs)
    pnlDelta: number;     // equity change caused by this trade (mark move + costs)
    cash: number;         // simulated free cash after this trade
    pos: number;          // simulated open-position value after this trade
    /** Sharpe-weighted EV score the live engine would have assigned this
        candidate when it was eligible. Undefined for SELLs (always honored)
        and for BUYs from traders without enough 30d closed trades. */
    score?: number;
    sharpe?: number;
    /** P(success) the playbook priced this BUY at — the trader's smoothed
        30d win rate (0.5 = coin-flip prior). Undefined on exits. */
    successProb?: number;
  }

  // ── Backtest portfolio simulation — the ONE source of truth ──
  // Replays the constrained trade set through a simulated wallet: cash starts
  // at CAPITAL, BUYs must fit the remaining cash (the live engine can't spend
  // money it doesn't have), SELLs can only close inventory the sim actually
  // holds, fees + gas come out of cash. Every surface on the BACKTEST tab —
  // trade feed, equity curve, P&L/ROI header, fee row — derives from this one
  // replay, so the numbers can never disagree with each other again (the old
  // header mixed three different trade sets and showed "-$3.64 P&L, 0 TXS,
  // GROSS +$1.56"). The equity history is the same {t, liq, pos} snapshot
  // shape the LIVE portfolio records, rendered through the same EquityChart.
  interface BacktestSim {
    rows: LinkedTrade[];
    equityHistory: EquitySnapshot[];
    markers: EquityMarker[];
    /** Trades dropped by the MIN size gate, the cash budget, or missing
        inventory — surfaced in the fee row so 0 TXS is explainable. */
    skipped: number;
    netPnl: number;    // final equity − capital (fees/gas already paid)
    grossPnl: number;  // netPnl before fees/gas
    fees: number;
    gas: number;
    volume: number;    // total executed notional
  }

  const backtestSim = useMemo((): BacktestSim => {
    const empty: BacktestSim = {
      rows: [], equityHistory: [], markers: [],
      skipped: 0, netPnl: 0, grossPnl: 0, fees: 0, gas: 0, volume: 0,
    };
    if (watchlist.length === 0 || loading) return empty;
    const cutoffMs = Date.now() - backtestDays * 86400_000;

    // Build from raw trades directly (not curve points) to use conditionId for filtering
    type RawEntry = {
      ts: number; market: string; conditionId: string; trader: string;
      side: "BUY" | "SELL"; size: number; price: number; realized: number;
      /** Full strat-shaped trade — sizing goes through the SAME
          sizeAndPrice hook the live engine calls, for both sides. */
      stratTrade: StratTraderTrade;
      score?: number; sharpe?: number; successProb?: number;
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

      // Score via the SAME strat instance used live + by keptBuyIds — so
      // dropping in a new strat changes BUY scores in the chart tooltip
      // and feed without any extra wiring. copyRatio comes from the shared
      // traderCopyRatio map — the same proportional ratio the live engine
      // hands hooks — so sizing below scales exactly like a deployment.
      const stats = traderStatsMap.get(addr) ?? null;
      const sharpe = stats?.sharpe ?? 0;
      const totalW = watchlist.reduce((s, a) => s + (traderWeights[a] || 0), 0) || 1;
      const weight = traderWeights[addr] || 0;
      const weightFraction = weight / totalW;
      for (const t of windowAnnotated) {
        const stratTrade: StratTraderTrade = {
          ...t, trader: addr, weight, weightFraction,
          copyRatio: traderCopyRatio.get(addr) ?? 0, notional: t.price * t.size,
        };
        let score: number | undefined;
        if (t.side === "BUY") {
          const s = backtestStrat.scoreCandidate(stratTrade, stats, backtestHistory);
          if (s > 0) score = s;
        }
        allEntries.push({
          ts: t.timestamp, market: t.market, conditionId: t.conditionId || t.market,
          trader: addr, side: t.side, size: t.size, price: t.price,
          realized: t.realized, stratTrade,
          score,
          sharpe: sharpe > 0 ? sharpe : undefined,
          successProb: t.side === "BUY" ? successProbability(stats) : undefined,
        });
      }
    }

    allEntries.sort((a, b) => a.ts - b.ts);

    // Last time ANY leader trade touched each market — sorted ascending, so
    // the final write per key is its max. Drives the settlement pass below.
    const lastSeen = new Map<string, number>();
    for (const e of allEntries) lastSeen.set(e.conditionId, e.ts);

    // Current market prices for the final mark-to-market point — same source
    // the live portfolio uses (position currentPrice), falling back to the
    // last traded price the replay observed.
    const curPx = new Map<string, number>();
    for (const addr of watchlist) {
      for (const p of traderData.get(addr) || []) {
        const key = p.conditionId || p.market;
        if (key && p.currentPrice > 0) curPx.set(key, p.currentPrice);
      }
    }

    // Replay through the simulated wallet. TRADE SIZE constraints match the
    // live engine: amount < MIN → dust, not placed; amount > MAX → clamped;
    // a SELL only closes shares the sim actually holds. A BUY that doesn't
    // fit the remaining cash triggers the SAME capital-aware rebalance the
    // live engine runs (live_engine.rs `free_capital_via_sells`): sell the
    // weakest-score holds the candidate out-scores by the rebalance margin,
    // then place — only skip when no eligible hold can free enough. New
    // positions past `maxOpenPositions` are skipped exactly like live's
    // MAX_POSITIONS gate. SHOW ALL lifts every gate to preview the
    // unconstrained mirror.
    const round2 = (v: number) => Math.round(v * 100) / 100;
    // Live defaults (live_engine.rs): rebalancing ON, candidate must
    // out-score a hold by the shared playbook margin before that hold is
    // sold to fund it.
    const REBALANCE_MARGIN = 1 + REBALANCE_MARGIN_PCT;
    let cash = capital;
    const book = new Map<string, { shares: number; avgPx: number; entryScore: number; market: string }>();
    const lastPx = new Map<string, number>();
    const rows: LinkedTrade[] = [];
    const equityHistory: EquitySnapshot[] = [];
    const markers: EquityMarker[] = [];
    let skipped = 0;
    let fees = 0;
    let volume = 0;

    const posValue = (marks: Map<string, number>) => {
      let v = 0;
      for (const [k, b] of book) {
        if (b.shares > 1e-9) v += b.shares * (marks.get(k) ?? lastPx.get(k) ?? b.avgPx);
      }
      return v;
    };
    const openCount = () => {
      let n = 0;
      for (const b of book.values()) if (b.shares > 1e-9) n++;
      return n;
    };

    // Seed: all cash at the window start, so the curve starts at CAPITAL
    // exactly like a fresh live deployment.
    equityHistory.push({ t: Math.min(cutoffMs, allEntries[0]?.ts ?? cutoffMs), liq: capital, pos: 0 });

    // Mark-to-market snapshots BETWEEN executed trades. Every observed leader
    // trade moves a mark whether or not we act on it — without recording
    // those moves the curve froze at the last fill and drew a dead-straight
    // horizontal line from there to "now" (the sim kept skipping once cash
    // ran out, so hours of real mark movement rendered as one flat segment).
    // Bucketed so a 30d window doesn't emit tens of thousands of points.
    const SNAP_MS = Math.max(15 * 60_000, (backtestDays * 86400_000) / 400);
    let lastSnapT = equityHistory[0].t;
    const snapshotMark = (ts: number) => {
      if (ts - lastSnapT < SNAP_MS) return;
      lastSnapT = ts;
      equityHistory.push({ t: ts, liq: cash, pos: posValue(lastPx) });
    };

    // ── Settlement pass — the live engine's auto-redeem, simulated ──
    // Short-lived markets (5-min "Bitcoin Up or Down", hourly, daily) RESOLVE
    // mid-window. Live, the engine's 5-min auto-redeem pass converts those
    // positions to cash; the sim had no such model, so the book filled up with
    // dead resolved markets held at their last traded price FOREVER. Capital
    // never came back, `maxOpenPositions` was permanently saturated, and every
    // later BUY skipped — a 3d run on HFT leaders executed 42 trades in the
    // first 20 minutes then drew a flat line with "1032 SKIPPED".
    //
    // A held market settles at its LAST OBSERVED price once (a) its leader
    // feed has been quiet for the debounce window and (b) it has no live
    // current price (leaders hold none of it today) — i.e. it resolved, or
    // every leader exited and a copier would have exited with them. Settling
    // at the last mark is expectation-neutral (the position was already
    // valued there); what it fixes is freeing the cash and the position slot
    // so the replay keeps trading like a live deployment. Gas is charged like
    // any redeem; no taker fee (redeems aren't CLOB fills). Rendered as the
    // same amber REDEEM markers the LIVE chart uses — not as feed rows.
    const STALE_SETTLE_MS = 30 * 60_000;
    let settles = 0;
    const settleDead = (now: number) => {
      for (const [k, b] of [...book]) {
        if (b.shares <= 1e-9) { book.delete(k); continue; }
        if (curPx.has(k)) continue; // market is alive today — keep marking
        if (now - (lastSeen.get(k) ?? 0) < STALE_SETTLE_MS) continue;
        const px = lastPx.get(k) ?? b.avgPx;
        const proceeds = b.shares * px;
        cash += proceeds - GAS_PER_TRADE_USD;
        settles++;
        book.delete(k);
        markers.push({ t: now, side: "REDEEM", label: `SETTLE · ${b.market}`, usd: proceeds });
        equityHistory.push({ t: now, liq: cash, pos: posValue(lastPx) });
        lastSnapT = now;
      }
    };

    for (const t of allEntries) {
      settleDead(t.ts);
      const key = t.conditionId;
      const prevEquity = cash + posValue(lastPx);
      // Observed price is real market information whether or not we trade on
      // it — the mark moves either way (that's what MTM means).
      lastPx.set(key, t.price);
      // Live stop-loss parity: the engine sells a hold once its price decays
      // to ≤ entry × stopLoss (whole position, at the mark — same shape as
      // its bid-priced exit), via the SAME `stopLossTriggered` helper the
      // playbook exports (mirror of live_engine.rs `stop_loss_hit`). Checked
      // on every mark move of a held market so the backtest fires at the
      // same decay point live would. The tick that tripped the stop is then
      // consumed — live never re-enters a market in the same cycle it just
      // stopped out of.
      // Live take-profit parity: the engine liquidates a hold once its bid
      // runs to ≥ takeProfit (0.99 default — the market ran to ~100%, it's
      // decided), via the SAME `takeProfitTriggered` helper the playbook
      // exports (mirror of live_engine.rs `take_profit_hit`). Checked before
      // the stop so the labels match live's precedence.
      if (!showAllTrades && takeProfitFrac > 0) {
        const topped = book.get(key);
        if (topped && topped.shares > 1e-9 && takeProfitTriggered(t.price, takeProfitFrac)) {
          const proceeds = topped.shares * t.price;
          const sellFee = topped.shares * Math.min(t.price, 1 - t.price) * (TAKER_FEE_BPS / 10_000);
          const tpRealized = (t.price - topped.avgPx) * topped.shares;
          cash += proceeds - sellFee - GAS_PER_TRADE_USD;
          fees += sellFee;
          volume += proceeds;
          book.delete(key);
          const pos = posValue(lastPx);
          const equity = cash + pos;
          rows.push({
            ts: t.ts,
            market: topped.market,
            conditionId: key,
            trader: t.trader,
            side: "SELL",
            amount: proceeds,
            price: t.price,
            fee: round2(sellFee),
            realized: round2(tpRealized),
            runningPnl: round2(equity - capital),
            pnlDelta: round2(equity - prevEquity),
            cash: round2(cash),
            pos: round2(pos),
          });
          equityHistory.push({ t: t.ts, liq: cash, pos });
          lastSnapT = t.ts;
          markers.push({ t: t.ts, side: "SELL", label: `TAKE PROFIT · ${topped.market}`, usd: proceeds });
          skipped++;
          continue;
        }
      }
      if (!showAllTrades && stopLossPct > 0) {
        const stopped = book.get(key);
        if (stopped && stopped.shares > 1e-9 && stopLossTriggered(stopped.avgPx, t.price, (100 - stopLossPct) / 100)) {
          const proceeds = stopped.shares * t.price;
          const sellFee = stopped.shares * Math.min(t.price, 1 - t.price) * (TAKER_FEE_BPS / 10_000);
          const stopRealized = (t.price - stopped.avgPx) * stopped.shares;
          cash += proceeds - sellFee - GAS_PER_TRADE_USD;
          fees += sellFee;
          volume += proceeds;
          book.delete(key);
          const pos = posValue(lastPx);
          const equity = cash + pos;
          rows.push({
            ts: t.ts,
            market: stopped.market,
            conditionId: key,
            trader: t.trader,
            side: "SELL",
            amount: proceeds,
            price: t.price,
            fee: round2(sellFee),
            realized: round2(stopRealized),
            runningPnl: round2(equity - capital),
            pnlDelta: round2(equity - prevEquity),
            cash: round2(cash),
            pos: round2(pos),
          });
          equityHistory.push({ t: t.ts, liq: cash, pos });
          lastSnapT = t.ts;
          markers.push({ t: t.ts, side: "SELL", label: `STOP LOSS · ${stopped.market}`, usd: proceeds });
          skipped++;
          continue;
        }
      }
      // Size EXACTLY like the live engine: through the strat's sizeAndPrice
      // hook, which clamps proportional dust UP to max(MIN, CLOB floor) and
      // only skips true leader dust / no-legal-size cases. The old inline
      // gate here (`rawAmount < minTrade → skip`) silently killed EVERY
      // trade at small SIM funds — $100 spread over high-volume traders
      // mirrors to pennies, so 100% of the replay was "N SKIPPED · 0 TXS"
      // while a live deployment with identical params would have traded.
      // SHOW ALL still previews the unconstrained proportional mirror.
      const rawAmount = t.stratTrade.notional * t.stratTrade.copyRatio;
      let gatedAmount: number;
      if (showAllTrades) {
        gatedAmount = rawAmount;
      } else {
        const decision = backtestStrat.sizeAndPrice(t.stratTrade, {
          userFloor: minTrade,
          userCeiling: maxTrade,
          clobFloor: clobMinNotional(t.price),
          capital,
        }, backtestHistory);
        if (decision.mirrorNotional <= 0) { skipped++; snapshotMark(t.ts); continue; }
        gatedAmount = decision.mirrorNotional;
      }

      let amount: number;
      let fee: number;
      let realized = 0;
      if (t.side === "BUY") {
        amount = gatedAmount;
        const shares = t.price > 0 ? amount / t.price : 0;
        fee = shares * Math.min(t.price, 1 - t.price) * (TAKER_FEE_BPS / 10_000);
        const cost = amount + fee + GAS_PER_TRADE_USD;
        // Live MAX_POSITIONS gate: opening a NEW market past the cap is
        // skipped; topping up an already-held one always passes.
        const held = book.get(key);
        if (!showAllTrades && !(held && held.shares > 1e-9) && openCount() >= maxOpenPositions) {
          skipped++; snapshotMark(t.ts); continue;
        }
        // Live capital-aware rebalance (free_capital_via_sells): when the BUY
        // doesn't fit the remaining cash, sell the weakest-score holds this
        // candidate out-scores by ≥ the margin — weakest first, full position
        // at its current mark — until the shortfall is covered. Without this
        // the sim froze the moment cash ran out while a live deployment kept
        // rotating capital into better-scoring trades.
        if (!showAllTrades && cost > cash) {
          const candScore = t.score ?? 0;
          const sellable = [...book.entries()]
            .filter(([k, b]) => k !== key && b.shares > 1e-9 && candScore >= b.entryScore * REBALANCE_MARGIN)
            .sort((a, b) => a[1].entryScore - b[1].entryScore);
          for (const [k, b] of sellable) {
            if (cost <= cash) break;
            const mark = lastPx.get(k) ?? b.avgPx;
            const proceeds = b.shares * mark;
            const sellFee = b.shares * Math.min(mark, 1 - mark) * (TAKER_FEE_BPS / 10_000);
            cash += proceeds - sellFee - GAS_PER_TRADE_USD;
            fees += sellFee;
            volume += proceeds;
            book.delete(k);
            const pos = posValue(lastPx);
            const equity = cash + pos;
            rows.push({
              ts: t.ts,
              market: b.market,
              conditionId: k,
              trader: t.trader,
              side: "SELL",
              amount: proceeds,
              price: mark,
              fee: round2(sellFee),
              realized: round2((mark - b.avgPx) * b.shares),
              runningPnl: round2(equity - capital),
              pnlDelta: round2(equity - prevEquity),
              cash: round2(cash),
              pos: round2(pos),
            });
            equityHistory.push({ t: t.ts, liq: cash, pos });
            markers.push({ t: t.ts, side: "SELL", label: `REBALANCE · ${b.market}`, usd: proceeds });
          }
          if (cost > cash) { skipped++; snapshotMark(t.ts); continue; } // nothing left to rotate out
        }
        cash -= cost;
        const b = book.get(key) ?? { shares: 0, avgPx: 0, entryScore: 0, market: t.market };
        const newShares = b.shares + shares;
        b.avgPx = newShares > 0 ? (b.avgPx * b.shares + t.price * shares) / newShares : 0;
        b.shares = newShares;
        // Freeze the strongest score seen at entry — the bar a future
        // candidate must clear (× margin) to rotate this hold out.
        b.entryScore = Math.max(b.entryScore, t.score ?? 0);
        book.set(key, b);
      } else {
        const b = book.get(key);
        const held = b?.shares ?? 0;
        if (!b || held <= 1e-9) { skipped++; snapshotMark(t.ts); continue; } // nothing to close at our scale
        const shares = Math.min(t.price > 0 ? gatedAmount / t.price : 0, held);
        amount = shares * t.price;
        fee = shares * Math.min(t.price, 1 - t.price) * (TAKER_FEE_BPS / 10_000);
        cash += amount - fee - GAS_PER_TRADE_USD;
        realized = (t.price - b.avgPx) * shares;
        b.shares -= shares;
        if (b.shares <= 1e-9) book.delete(key);
      }

      fees += fee;
      volume += amount;
      const pos = posValue(lastPx);
      const equity = cash + pos;
      rows.push({
        ts: t.ts,
        market: t.market,
        conditionId: key,
        trader: t.trader,
        side: t.side,
        amount,
        price: t.price,
        fee: round2(fee),
        realized: round2(realized),
        runningPnl: round2(equity - capital),
        pnlDelta: round2(equity - prevEquity),
        cash: round2(cash),
        pos: round2(pos),
        score: t.score,
        sharpe: t.sharpe,
        successProb: t.successProb,
      });
      equityHistory.push({ t: t.ts, liq: cash, pos });
      lastSnapT = t.ts; // fills reset the mark-snapshot bucket
      markers.push({
        t: t.ts,
        side: t.side,
        usd: round2(amount),
        label: `${t.market}${t.stratTrade.outcome ? ` · ${t.stratTrade.outcome}` : ""} @ ${Math.round(t.price * 100)}¢`,
      });
    }

    // Final settlement sweep — anything still held in a dead market settles
    // before the NOW mark, so resolved inventory shows as cash (what a live
    // deployment would actually be holding today), not as phantom positions.
    settleDead(Date.now());

    // Final NOW point — open inventory re-marked at current market prices so
    // the curve ends where a live deployment's portfolio would sit today.
    const nowPos = posValue(curPx);
    equityHistory.push({ t: Date.now(), liq: cash, pos: nowPos });

    const gas = (rows.length + settles) * GAS_PER_TRADE_USD;
    const netPnl = round2(cash + nowPos - capital);
    return {
      rows,
      equityHistory,
      markers,
      skipped,
      netPnl,
      grossPnl: round2(netPnl + fees + gas),
      fees: round2(fees),
      gas: round2(gas),
      volume: round2(volume),
    };
  }, [watchlist, traderTrades, traderData, backtestDays, traderWeights, totalWeight, capital, loading, rebalancePeriod, rebalanceHour, samplePct, minTrade, maxTrade, maxOpenPositions, stopLossPct, takeProfitFrac, showAllTrades, keptBuyIds, traderStatsMap, backtestStrat, backtestHistory, marketQuery, traderCopyRatio]);

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
  const [indexTradeLimit, setIndexTradeLimit] = useState(100);
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


  // How many of the selected traders' in-window trades pass the keyword
  // filter — the header dropdown's live "does my filter bite" readout.
  // Recomputes on every keyword edit, same dependency the backtest keys on.
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

  // Per-trader params bite — how many of each trader's in-window trades
  // survive the FULL params gate (market keywords AND side/price/size/
  // category trade filters). Renders as the FILT column in the trader rows,
  // so selecting a trader shows on the spot which slice of their flow the
  // current params would actually copy.
  const paramsFilterActive = marketQuery.trim() !== "" || tradeFiltersActive(tradeFilters);
  const traderFilteredByAddr = useMemo(() => {
    const cutoff = Date.now() - backtestDays * 24 * 60 * 60 * 1000;
    const m = new Map<string, { matched: number; total: number }>();
    for (const addr of allTraderAddrs) {
      const trades = traderTrades.get(addr);
      if (!trades) continue;
      let total = 0;
      let matched = 0;
      for (const t of trades) {
        if (t.timestamp < cutoff) continue;
        total++;
        if (marketMatchesQuery(t.market, marketQuery) && tradeMatchesFilters(t, tradeFilters)) matched++;
      }
      m.set(addr, { matched, total });
    }
    return m;
  }, [allTraderAddrs, traderTrades, backtestDays, marketQuery, tradeFilters]);

  // ══════════════════════════════════════════
  // ── RENDER ──
  // ══════════════════════════════════════════

  return (
    <div className="min-w-0 space-y-2">
      {/* ── Header: main tabs + subtab rail ──
          Two-level nav: STRAT / BACKTEST / LIVE on top, a contextual
          subtab rail below. Wallet/token/QR + trading wallet + funding
          live under LIVE → WALLET ($ pill). */}
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
              // LIVE (SUBTABS above), next to the engine it funds.
              { id: "STRATS", label: "STRAT", disabled: false },
              { id: "BACKTEST", label: "BACKTEST", disabled: false },
              { id: "LIVE", label: "LIVE", disabled: false },
            ] as { id: MainTab; label: string; disabled: boolean }[]
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
                    ? MAIN_ACTIVE[t.id]
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
                    active ? `${MAIN_BAR[t.id]} opacity-100` : "opacity-0"
                  }`}
                />
              </button>
            );
          })}

          {/* Keyword filter on the selected traders' trades — every edit
              commits to the strat, the backtest recomputes immediately, and
              the LIVE engine copies the same filtered slice. */}
          <KeywordFilterDropdown
            query={marketQuery}
            onCommit={(q) => { setMarketQueryInput(q); updateMarketQuery(q); }}
            matched={keywordStats.matched}
            total={keywordStats.total}
            onViewBacktest={() => setMode("BACKTEST")}
            canBacktest={watchlist.length > 0}
          />
        </div>

        {/* ── Subtab rail — contextual second row, re-animates on mode swap
            (key={mode}). Pills carry the mode's accent; WALLET is always
            amber. Right side echoes free cash + next-cycle countdown while
            the engine runs, so no subtab ever hides the numbers that
            matter minute-to-minute. */}
        <div className="h-px -mx-1 bg-gradient-to-r from-transparent via-pixel-border/80 to-transparent" />
        <div key={mode} className="subtab-rail flex items-center gap-1.5 flex-wrap">
          {SUBTABS[mode].map((s) => {
            const active = activeSub === s.id;
            const accent = s.id === "wallet" ? WALLET_ACCENT : SUB_ACCENT[mode];
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
              title="Free cash · time to next poll cycle — full breakdown in STATS"
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

      </div>

      {/* ── LIVE → WALLET subtab ──
          The MONEY panel (one flow for deposit / withdraw / send) is the
          hero, full width at the top — LIVE's FUND NOW banner jumps here
          via onFundNow. Session pairing, cross-chain bridging and the
          legacy V1 Safe are secondary and sit in a grid below it.
          Renders regardless of watchlist — you can fund before you copy. */}
      {mode === "LIVE" && liveSub === "wallet" && (
        <div className="space-y-2 max-w-[1100px]">
          {/* ONE money panel: both balances, one amount, one button. */}
          <div id="sidebar-wallet-panel">
            <WalletPanel />
          </div>
          <div className="grid md:grid-cols-2 gap-2 items-start">
            <div className="space-y-2 min-w-0">
              {/* Full wallet + token + sign-in-QR pairing panel. */}
              <WalletTokenPanel />
            </div>
            <div className="space-y-2 min-w-0">
              {/* Bridge / send funds into Polygon USDC from any chain. */}
              <WalletFundingPanel />
              {/* Legacy V1 Safe — only renders once there's a leftover balance. */}
              <PolymarketAccountPanel />
            </div>
          </div>
        </div>
      )}

      {/* ── TRADERS + PARAMS (STRAT tab) ──
          One header for the whole strat editor: who you copy (add bar,
          leaderboard browser, watchlist rows) and every tuning knob
          (window / capital / trade band / throttle / top-N / sample /
          poll cadence / market focus / per-trade filters) in the same
          panel. The FILT column on each trader row shows live how many
          of that trader's in-window trades the current params keep.
          The strat's SOURCE code renders directly below this panel.
          The status row keeps the strat picker + data-freshness chips
          so a stalled poll loop is still diagnosable from here. */}
      {mode === "STRATS" && stratSub === "build" && activeIndex && (
        <div className="pixel-panel px-3 py-2.5 space-y-2.5">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-[14px] text-pixel-white tracking-[0.2em] shrink-0">TRADERS + PARAMS</span>
            <div className="w-2 h-2 bg-green-400 shrink-0" />
            {/* Name doubles as the strat switcher; ✎ (or a double-click on
                the name) turns it into an inline rename field. */}
            {renamingId === activeIndex.id ? (
              <input
                autoFocus
                value={renameValue}
                onChange={(e) => setRenameValue(e.target.value)}
                onFocus={(e) => e.currentTarget.select()}
                onKeyDown={(e) => {
                  if (e.key === "Enter") handleRename(activeIndex.id, renameValue);
                  if (e.key === "Escape") setRenamingId(null);
                }}
                onBlur={() => handleRename(activeIndex.id, renameValue)}
                maxLength={60}
                className="bg-pixel-bg border border-green-400/60 px-1.5 py-0.5 text-[14px] font-mono text-green-400 font-bold outline-none w-[180px] shrink-0"
                title="Enter to save · Esc to cancel"
              />
            ) : (
              <>
                <select
                  value={activeIndex.id}
                  onChange={(e) => selectStrategy(e.target.value)}
                  onDoubleClick={() => { setRenamingId(activeIndex.id); setRenameValue(activeIndex.name); }}
                  className="bg-transparent text-[14px] font-mono text-green-400 font-bold truncate outline-none border-none cursor-pointer hover:underline pr-1"
                  title="Switch active strat · double-click to rename"
                >
                  {savedIndexes.map((s) => (
                    <option key={s.id} value={s.id} className="bg-pixel-bg text-pixel-white">
                      {s.name}
                    </option>
                  ))}
                </select>
                <button
                  onClick={() => { setRenamingId(activeIndex.id); setRenameValue(activeIndex.name); }}
                  className="shrink-0 text-[12px] text-pixel-gray hover:text-green-400 px-1 leading-none"
                  title={`Rename "${activeIndex.name}"`}
                >
                  ✎
                </button>
              </>
            )}
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
          {/* ── TRADERS — browse/add bar + watchlist rows ── */}
          <div className="border-t border-pixel-border/40 pt-2.5 space-y-2">
            {/* Browse Traders — one collapsible unit: the toggle is the
                header, with the search/add bar and leaderboard browser
                directly below it inside the same div so the whole
                trader-picking UI folds away together. Clicking a
                leaderboard trader toggles them in/out of the active strat. */}
            <div className="border border-pixel-border/60 rounded-[var(--radius-sm)]">
              <button
                onClick={() => setBrowseOpen((v) => !v)}
                className="w-full flex items-center justify-between px-3 py-2 text-left group/bt"
              >
                <span className="flex items-center gap-2.5">
                  <span className={`text-pixel-gray text-[12px] transition-transform duration-200 ${browseOpen ? "rotate-90" : ""}`}>▸</span>
                  <span
                    className="text-[13px] font-bold text-pixel-white uppercase tracking-[0.18em] group-hover/bt:text-green-400 transition-colors"
                    style={{ fontFamily: '"Space Grotesk", system-ui, sans-serif' }}
                  >
                    Browse Traders
                  </span>
                  <span className="text-[11px] text-pixel-gray hidden sm:inline tracking-wide">
                    search, or click a leaderboard trader to add
                  </span>
                </span>
                <span className="flex items-center gap-2 shrink-0">
                  {loading && (
                    <span className="text-[12px] text-green-400 font-mono animate-pulse">
                      {loadedCount}/{watchlist.length}
                    </span>
                  )}
                  {allTraderAddrs.length > 0 && (
                    <span className="text-[11px] text-green-400 font-mono px-2 py-0.5 rounded-full bg-green-400/[0.08] border border-green-400/30">
                      {allTraderAddrs.length} in strat
                    </span>
                  )}
                </span>
              </button>
              {browseOpen && (
                <div className="px-3 pb-3 border-t border-pixel-border/40 pt-3 space-y-3">
                  {/* Search-first add bar — lives under the browse header inside
                      the same collapsible so both fold away together. */}
                  <AddTraderBar watchlist={watchlist} onAdd={addTrader} />
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

            {/* Watchlist editor — everyone in the strat, with the FILT column
                showing how the params below bite each trader's flow. */}
            {allTraderAddrs.length > 0 && (
              <div className="space-y-1">
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
                  <span className="w-16 text-right" title="In-window trades passing the PARAMS below (market keywords + trade filter) — the slice the strat would actually copy">FILT</span>
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
                        {/* FILT — how many of this trader's in-window trades pass
                            the params below (market keywords AND per-trade
                            filters). Red when the params exclude this trader
                            entirely; gray "all" when no filter is active. */}
                        {(() => {
                          const f = traderFilteredByAddr.get(t.address);
                          if (!f) {
                            return (
                              <span className="w-16 text-right text-[13px] font-mono text-pixel-gray">…</span>
                            );
                          }
                          const compact = (n: number) => n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);
                          if (!paramsFilterActive) {
                            return (
                              <span
                                className="w-16 text-right text-[13px] font-mono text-pixel-gray-light"
                                title={`${f.total} trades in the ${backtestDays}d window — no params filter active, everything passes`}
                              >
                                {compact(f.total)}
                              </span>
                            );
                          }
                          const cls = f.matched === 0 ? "text-red-400" : "text-amber-300";
                          return (
                            <span
                              className={`w-16 text-right text-[13px] font-mono ${cls}`}
                              title={`${f.matched}/${f.total} trades in the ${backtestDays}d window pass the current params (market keywords + trade filter)`}
                            >
                              {compact(f.matched)}/{compact(f.total)}
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
          </div>

          {/* ── PARAMS — every knob; the filters gate the traders above,
              and the FILT column re-counts on every edit here ── */}
          <div className="border-t border-pixel-border/40 pt-3 space-y-2">
            <div className="flex items-baseline gap-2 flex-wrap">
              <span className="text-[11px] font-bold text-pixel-white tracking-[0.26em]">PARAMS</span>
              <span className="text-[10px] text-pixel-gray">
                apply to the selected traders above — every edit re-runs the backtest and steers the live engine
              </span>
            </div>
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
            </div>
          </div>
        </div>
      )}

      {/* Strat list + "+ New" live in the TopBar strat picker (HeaderStratPicker). */}

      {/* ── STRAT → SOURCE subtab — the strat's code ──
          Read-only for built-in TS strats; editable for user-uploaded
          mod.py / mod.rs files (persisted via /api/polymarket/user-strats). */}
      {mode === "STRATS" && stratSub === "source" && <StratSourceViewer />}

      {/* ── Add trader bar (BACKTEST — on STRAT it lives inside TRADERS + PARAMS) ── */}
      {mode === "BACKTEST" && (
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
          {/* Empty trader state */}
          {watchlist.length === 0 && !(mode === "LIVE" && liveSub === "wallet") && (
            <div className="pixel-panel p-4 text-center space-y-2">
              <div className="text-[14px] text-pixel-gray">NO TRADERS YET</div>
              <div className="text-[12px] text-pixel-gray-light">
                ADD TRADERS FROM{" "}
                <button onClick={() => { setMode("STRATS"); pickSub("STRATS", "build"); setBrowseOpen(true); }} className="text-pixel-white hover:text-green-400 transition-colors">
                  PARAMS → TRADERS + PARAMS
                </button>.
              </div>
              {mode === "BACKTEST" && (
                <div className="text-[11px] text-pixel-gray tracking-wider">
                  BACKTESTS RUN ON SIMULATED FUNDS — NO WALLET OR DEPOSIT NEEDED.
                </div>
              )}
            </div>
          )}

          {/* ── BACKTEST panel ───────────────────────────────────────
              RUN + P&L summary + chart + fee row. Always visible on
              BACKTEST tab; LIVE has its own <LivePanel /> rendered
              elsewhere with real-time engine state. Splitting this out
              of the STRAT panel above keeps the param row scannable. */}
          {watchlist.length > 0 && mode === "BACKTEST" && backtestSub === "results" && (
            <div className="pixel-panel px-3 py-2.5 space-y-3">
              {activeIndex?.momentum && (
                <div className="text-[11px] font-mono tracking-[0.1em] px-2 py-1.5 border border-amber-400/50 text-amber-400/90 bg-amber-400/5">
                  ⚠ MOMENTUM ORIGINATION IS NOT SIMULATED — this backtest replays
                  only the copy-mirror side of the strat. Live, the engine also
                  originates its own entries from rising prices, so live results
                  will diverge from this curve. Judge momentum params in a DRY
                  RUN live session instead.
                </div>
              )}
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
                </div>
                <div className="flex items-center gap-4 text-[13px] font-mono">
                  <div
                    className="flex items-baseline gap-1.5"
                    title="Final simulated equity minus starting capital — fees and gas already paid out of cash. Same accounting as the LIVE portfolio curve."
                  >
                    <span className="text-[12px] text-pixel-gray tracking-[0.15em]">P&L</span>
                    <span className={backtestSim.netPnl >= 0 ? "text-green-400" : "text-red-400"}>
                      {formatPnl(backtestSim.netPnl)}
                    </span>
                  </div>
                  <div className="flex items-baseline gap-1.5">
                    <span className="text-[12px] text-pixel-gray tracking-[0.15em]">ROI</span>
                    <span className={backtestRoi >= 0 ? "text-green-400" : "text-red-400"}>
                      {backtestRoi >= 0 ? "+" : ""}{backtestRoi.toFixed(1)}%
                    </span>
                  </div>
                </div>
              </div>

              {/* Fee/gas cost summary — same simulated-wallet replay as the
                  P&L header, the curve, and the feed, so the numbers agree. */}
              {(() => {
                const { fees: feedFees, gas: feedGas, volume: feedVolume, grossPnl, skipped } = backtestSim;
                const feedCosts = feedFees + feedGas;
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
                        {skipped > 0 && (
                          <>
                            <span className="text-pixel-border/60">·</span>
                            <span
                              className="text-[12px] text-pixel-gray/70 tracking-wider"
                              title={`Trades the simulated wallet could NOT place: below the $${minTrade} MIN size, more than the remaining cash, or selling a position it never opened. Raise CAPITAL or lower MIN to execute more — SHOW ALL in the feed previews them.`}
                            >
                              {skipped} SKIPPED
                            </span>
                          </>
                        )}
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

              {/* BACKTEST equity chart — the simulated wallet's cash +
                  positions over time, rendered through the SAME EquityChart
                  the LIVE portfolio uses, so backtest and live plot the same
                  quantity the same way. Markers are the replay's executed
                  trades, hover-linked to the feed below. */}
              <div ref={chartPanelRef}>
              {loading ? (
                <div className="p-6 text-center">
                  <span className="text-[13px] text-pixel-gray animate-pulse">LOADING...</span>
                </div>
              ) : (
                <div className="space-y-1">
                  <div className="flex items-center justify-between px-1">
                    <span className="text-[12px] text-pixel-gray tracking-[0.15em]">
                      {backtestDays}D SIMULATED EQUITY · CASH + POSITIONS (MTM) — SAME CURVE AS LIVE
                    </span>
                    <span className={`text-[13px] font-mono ${backtestSim.netPnl >= 0 ? "text-green-400" : "text-red-400"}`}>
                      {formatPnl(backtestSim.netPnl)}
                    </span>
                  </div>
                  <EquityChart
                    history={backtestSim.equityHistory}
                    markers={backtestSim.markers}
                    highlightT={chartHighlightT}
                    onHoverMarker={handleChartHover}
                    emptyHint="NOT ENOUGH TRADE DATA FOR THE EQUITY CURVE — ADD TRADERS OR WIDEN THE WINDOW."
                  />
                </div>
              )}
              </div>
            </div>
          )}

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

          {/* ── Live Panel — controlled by the header's subtab rail; the
              WALLET subtab renders its own panels above instead. ── */}
          {mode === "LIVE" && liveSub !== "wallet" && watchlist.length > 0 && (
            <LivePanel
              tab={liveSub}
              onTabChange={(t) => pickSub("LIVE", t)}
              onFundNow={() => pickSub("LIVE", "wallet")}
            />
          )}
        </>
      )}

    </div>
  );
}
