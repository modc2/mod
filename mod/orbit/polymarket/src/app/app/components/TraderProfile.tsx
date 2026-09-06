"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { TopTrader, ClosedPosition, CATEGORIES, formatVolume, formatPnl, timeAgo, matchMarketCategory, CategorySlug, MAX_ACTIVITY_ROWS } from "../lib/polymarket";
import { shortAddress } from "@/lib/auth";
import { PolymarketTrade, PolymarketPosition, TradeFilters } from "../lib/types";
import { tradeMatchesFilters, describeTradeFilters } from "../lib/tradeFilters";
import { marketMatchesQuery } from "../lib/marketQuery";
import { useTradeFilterBar } from "./TradeFilterBar";
import ProfileFilters from "./ProfileFilters";
import CopySimPanel from "./CopySimPanel";
import type { AllocationParams } from "../lib/identityStrat";
import PnlChart from "./PnlChart";
import type { CurvePoint } from "./PnlChart";
// No recharts — pure SVG charts for reliability with any version.

interface Props {
  trader: TopTrader;
  trades: PolymarketTrade[];
  positions: PolymarketPosition[];
  /** The settled book — every position the market has finished deciding,
      with realized P&L. `null` = not loaded or the fetch failed, which must
      render as unknown: the trade feed alone cannot answer win rate, because
      a position that expires worthless leaves no sell and no redeem in it. */
  settled?: ClosedPosition[] | null;
  loading: boolean;
  watching: boolean;
  onToggleWatch: () => void;
  onBack: () => void;
  days?: number;
  // Per-trader lookback override (null = following the global window).
  // When onDaysChange is provided the LOOKBACK pill row renders and
  // clicking the active override pill toggles back to the global window.
  daysOverride?: number | null;
  globalDays?: number;
  onDaysChange?: (d: number | null) => void;
  searchFilter?: string;
  categoryFilter?: CategorySlug;
  // The leaderboard's market-topic filter (FiltersContext `marketQuery`, ?mq=),
  // carried in from the row that was clicked. That row's P&L / volume / trade
  // count were recomputed from ONLY the matching markets, so the profile has to
  // narrow the same way — otherwise opening a "bitcoin" trader shows their whole
  // tape and none of the numbers reconcile with the list you came from.
  // Same matcher the strats use (lib/marketQuery.ts). Cleared via the chip's ✕.
  marketQuery?: string;
  onClearMarketQuery?: () => void;
  // Supplied ⇒ the filter rail can SET the topic gate, not just clear it. The
  // profile used to be able only to drop the query it was handed, which made
  // "expand into the trader" a one-way door out of the market you searched.
  onMarketQueryChange?: (q: string) => void;
  // Supplied ⇒ the FILTERS bar renders the global category buckets and can
  // set them from here (same shared filter TRADERS/MARKETS/TRADES use).
  onCategoryChange?: (c: CategorySlug) => void;
  // Strat trade-filter handoff: when set, every trade on this page is gated
  // through the copy engine's own tradeMatchesFilters — you see only the
  // trades the originating strat would mirror. Cleared via the chip's ✕.
  stratFilters?: TradeFilters | null;
  stratFilterName?: string;
  onClearStratFilters?: () => void;
  // Non-null when the trade-history sync failed (rate limit / outage). The
  // stats and tabs must not present 0 trades as a real answer in that case.
  tradesError?: string | null;
  /** The sync ended at Polymarket's activity-feed ceiling instead of at the
   *  requested window. Not an error — the trades shown are real and current —
   *  but they are the most RECENT slice, so anything older in the window is
   *  absent and every window-wide stat is a floor, not a total. */
  feedDepthCapped?: boolean;
  onRetrySync?: () => void;
  // Supplied ⇒ the header renders the COPY DESK control and the COPY SIM
  // panel: put an amount against this trader and add them to the copy book.
  // This is THE action on the page, and the only way the console starts
  // copying anyone. `params` is the configuration that was simulated — the
  // market gate and the engine knobs — so the row that gets written is the
  // one whose replay you just read, not the template's defaults.
  onCopyToDesk?: (allocationUsd: number, params?: AllocationParams) => void | Promise<void>;
  // Their current allocation, when they're already on the desk. `null` ⇒ not
  // copied; a number ⇒ the header says so instead of offering to add them
  // again (adding twice is an update, not a second session, but a button that
  // doesn't say which is a button that gets clicked by mistake).
  deskAllocationUsd?: number | null;
  /** Where the sticky filter rail parks, in px from the top of the viewport.
      The page raises it while the sync strip is on screen. */
  stickyTopPx?: number;
}

// 30 is the ceiling — trade syncs never reach further back than
// MAX_LOOKBACK_DAYS (lib/polymarket.ts), so longer windows would render
// as silently incomplete data.
const LOOKBACK_PRESETS = [1, 3, 7, 14, 30];

type PosSort = "market" | "size" | "avgPrice" | "currentPrice" | "pnlUsd";
type TradeSort = "timestamp" | "market" | "price" | "size" | "pnl";
type SortDir = "asc" | "desc";

function SortArrow({ active, dir }: { active: boolean; dir: SortDir }) {
  if (!active) return <span className="text-pixel-gray/40 ml-1">-</span>;
  return <span className="ml-1">{dir === "desc" ? "\u25BC" : "\u25B2"}</span>;
}

/* ── Pure SVG Bar Chart for Daily Activity ── */
function DailyActivityChart({ data }: { data: { date: string; buys: number; sells: number; volume: number }[] }) {
  const W = 800, H = 160;
  const pad = { top: 16, right: 16, bottom: 36, left: 40 };
  const cw = W - pad.left - pad.right;
  const ch = H - pad.top - pad.bottom;

  const maxVal = Math.max(...data.map((d) => d.buys + d.sells), 1);
  const barW = Math.min(40, (cw / data.length) * 0.7);
  const gap = cw / data.length;

  // Y ticks
  const yTicks: number[] = [];
  const step = Math.ceil(maxVal / 4) || 1;
  for (let i = 0; i <= 4; i++) {
    const v = i * step;
    if (v <= maxVal * 1.1) yTicks.push(v);
  }
  const yMax = (yTicks[yTicks.length - 1] || maxVal) * 1.1;
  const toY = (v: number) => pad.top + ch - (v / yMax) * ch;

  return (
    <div className="pixel-panel p-5">
      <div className="text-[16px] text-pixel-gray-light tracking-wider mb-4">DAILY TRADE ACTIVITY</div>
      {/* currentColor (via text-pixel-white) flips with the theme — the old
          hardcoded #fff bars vanished on the white light-mode panel. */}
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full text-pixel-white" style={{ height: "auto", maxHeight: 160 }}>
        {/* Grid */}
        {yTicks.map((v, i) => (
          <g key={i}>
            <line x1={pad.left} y1={toY(v)} x2={W - pad.right} y2={toY(v)} stroke="currentColor" strokeOpacity={0.12} strokeWidth={1} />
            <text x={pad.left - 6} y={toY(v) + 3} textAnchor="end" fill="currentColor" fillOpacity={0.45} fontSize={9} fontFamily="'IBM Plex Mono', monospace">{v}</text>
          </g>
        ))}
        {/* Bars */}
        {data.map((d, i) => {
          const cx = pad.left + gap * i + gap / 2;
          const buyH = (d.buys / yMax) * ch;
          const sellH = (d.sells / yMax) * ch;
          return (
            <g key={i}>
              {/* Buys (foreground, bottom) */}
              <rect x={cx - barW / 2} y={toY(d.buys + d.sells)} width={barW} height={buyH} fill="currentColor" />
              {/* Sells (gray, stacked on top) */}
              <rect x={cx - barW / 2} y={toY(d.buys + d.sells)} width={barW} height={sellH} style={{ fill: "var(--pixel-gray)" }} />
              {/* X label */}
              <text x={cx} y={H - 8} textAnchor="middle" fill="currentColor" fillOpacity={0.45} fontSize={8} fontFamily="'IBM Plex Mono', monospace">{d.date}</text>
            </g>
          );
        })}
        {/* Axes */}
        <line x1={pad.left} y1={pad.top} x2={pad.left} y2={H - pad.bottom} stroke="currentColor" strokeOpacity={0.28} strokeWidth={1} />
        <line x1={pad.left} y1={H - pad.bottom} x2={W - pad.right} y2={H - pad.bottom} stroke="currentColor" strokeOpacity={0.28} strokeWidth={1} />
      </svg>
      <div className="flex items-center gap-4 mt-1 text-[13px] text-pixel-gray">
        <div className="flex items-center gap-1"><div className="w-2 h-2 bg-pixel-white" /> BUYS</div>
        <div className="flex items-center gap-1"><div className="w-2 h-2 bg-pixel-gray" /> SELLS</div>
      </div>
    </div>
  );
}

export default function TraderProfile({
  trader,
  trades,
  positions,
  settled = null,
  loading,
  watching,
  onToggleWatch,
  onBack,
  days = 30,
  daysOverride = null,
  globalDays,
  onDaysChange,
  searchFilter = "",
  categoryFilter = "",
  marketQuery = "",
  onClearMarketQuery,
  onMarketQueryChange,
  onCategoryChange,
  stratFilters = null,
  stratFilterName = "",
  onClearStratFilters,
  tradesError = null,
  feedDepthCapped = false,
  onRetrySync,
  onCopyToDesk,
  deskAllocationUsd = null,
  stickyTopPx = 56,
}: Props) {
  // The COPY DESK amount field, opened from the header button.
  const [deskAmount, setDeskAmount] = useState("100");
  const [deskOpen, setDeskOpen] = useState(false);
  const [deskBusy, setDeskBusy] = useState(false);
  const dayLabel = days > 0 ? `${days}D` : "ALL-TIME";
  // How far the synced tape actually reaches — the honest answer to "is this
  // the whole window?" when the feed came back depth-capped.
  const oldestTradeMs = useMemo(
    () => trades.reduce((m, t) => (m === 0 || t.timestamp < m ? t.timestamp : m), 0),
    [trades],
  );
  const [customDays, setCustomDays] = useState("");
  const [posSort, setPosSort] = useState<PosSort>("pnlUsd");
  const [posSortDir, setPosSortDir] = useState<SortDir>("desc");
  const [tradeSort, setTradeSort] = useState<TradeSort>("timestamp");
  const [tradeSortDir, setTradeSortDir] = useState<SortDir>("desc");
  const [showCurrent, setShowCurrent] = useState(false);
  // Top level: what you're looking at. TRADES = the tape (with its own
  // OPEN/CLOSED/ALL/POSITIONS view), P&L = curve + activity + results,
  // INFO = who this trader is and how the numbers were built.
  type ProfileTab = "trades" | "pnl" | "info";
  const [profileTab, setProfileTab] = useState<ProfileTab>("trades");
  type TradeView = "all" | "open" | "closed" | "positions";
  const [tradeView, setTradeView] = useState<TradeView>("all");
  // Same FILTERS bar as the TRADES tape — slices this trader's flow by side /
  // entry price / size / keyword / category. Narrows EVERYTHING below (stats,
  // curve, tables, and the copy simulator), like the search + category filters
  // already do. It lives in the sticky rail below the header, not behind a
  // toggle in the tab bar: a gate you can't see is one you forget you set.
  const bar = useTradeFilterBar();
  // Selected-market filter over THIS trader's trades — set by clicking a
  // market name in the trade/results tables. No standing input: the chip in
  // the tab bar only appears while a market is selected (✕ clears it).
  const [tradeQuery, setTradeQuery] = useState("");

  const handlePosSort = (col: PosSort) => {
    if (posSort === col) setPosSortDir((d) => (d === "desc" ? "asc" : "desc"));
    else { setPosSort(col); setPosSortDir("desc"); }
  };

  const handleTradeSort = (col: TradeSort) => {
    if (tradeSort === col) setTradeSortDir((d) => (d === "desc" ? "asc" : "desc"));
    else { setTradeSort(col); setTradeSortDir("desc"); }
  };

  // days = 0 means ALL history → cutoff at epoch so nothing is filtered out.
  const cutoffMs = useMemo(
    () => (days > 0 ? Date.now() - days * 24 * 60 * 60 * 1000 : 0),
    [days],
  );

  // Replay full trade history with FIFO bookkeeping to compute realized
  // P&L on each SELL and track the matching buy price/time.
  //
  // Each market keeps a queue of buy lots: { price, size, timestamp }.
  // When a SELL arrives, we drain lots FIFO and record the weighted-avg
  // entry price and earliest buy timestamp on the sell trade.
  const tradesWithRealized = useMemo(() => {
    const seedAvgPrice = new Map<string, number>();
    for (const p of positions) {
      const key = p.conditionId || p.market;
      if (key && p.avgPrice > 0) seedAvgPrice.set(key, p.avgPrice);
    }
    const sorted = [...trades].sort((a, b) => a.timestamp - b.timestamp);
    type BuyLot = { price: number; size: number; ts: number };
    const book = new Map<string, BuyLot[]>();

    return sorted.map((t) => {
      const key = t.conditionId || t.market;
      if (!book.has(key)) book.set(key, []);
      const lots = book.get(key)!;

      let realized = 0;
      let hasBasis = true;
      let buyPrice: number | undefined;
      let buyTimestamp: number | undefined;

      if (t.side === "BUY") {
        lots.push({ price: t.price, size: t.size, ts: t.timestamp });
      } else {
        // SELL — drain FIFO lots
        let remaining = t.size;
        let totalCost = 0;
        let totalFilled = 0;
        let earliestBuyTs = Infinity;

        while (remaining > 0 && lots.length > 0) {
          const lot = lots[0];
          const take = Math.min(remaining, lot.size);
          totalCost += lot.price * take;
          totalFilled += take;
          if (lot.ts < earliestBuyTs) earliestBuyTs = lot.ts;
          lot.size -= take;
          remaining -= take;
          if (lot.size <= 1e-9) lots.shift();
        }

        if (totalFilled > 0) {
          const avgEntry = totalCost / totalFilled;
          realized = (t.price - avgEntry) * totalFilled;
          buyPrice = avgEntry;
          buyTimestamp = earliestBuyTs;
        } else if (seedAvgPrice.has(key)) {
          const seed = seedAvgPrice.get(key) || 0;
          realized = (t.price - seed) * t.size;
          buyPrice = seed;
        } else {
          hasBasis = false;
        }
      }

      return { ...t, realized, hasBasis, buyPrice, buyTimestamp };
    });
  }, [trades, positions]);

  const tradesInWindow = useMemo(
    () => tradesWithRealized.filter((t) => t.timestamp >= cutoffMs),
    [tradesWithRealized, cutoffMs],
  );

  // Apply market-name search + semantic-category filters for trade-level
  // filtering. All downstream consumers (stats, P&L curve, daily activity,
  // trade log, closed results) use filteredTrades so the whole page is
  // consistent.
  const filteredTrades = useMemo(() => {
    const q = searchFilter.trim().toLowerCase();
    const local = tradeQuery.trim().toLowerCase();
    const mq = marketQuery.trim();
    if (!q && !local && !mq && !categoryFilter && !stratFilters && !bar.active) return tradesInWindow;
    return tradesInWindow.filter((t) => {
      if (q && !t.market.toLowerCase().includes(q)) return false;
      if (local && !t.market.toLowerCase().includes(local)) return false;
      // The topic filter the traders list was ranked by — same matcher, so the
      // profile shows the same slice of flow the leaderboard scored.
      if (mq && !marketMatchesQuery(t.market, mq)) return false;
      if (categoryFilter && !matchMarketCategory(t.market, categoryFilter)) return false;
      // The FILTERS bar — side / entry price / size / keyword.
      if (!bar.matches(t)) return false;
      // The same gate the copy engine applies — the strat's own filters and
      // nothing else.
      if (stratFilters && !tradeMatchesFilters(t, stratFilters)) return false;
      return true;
    });
  }, [tradesInWindow, searchFilter, tradeQuery, marketQuery, categoryFilter, stratFilters, bar.active, bar.matches]);

  // Any trade-level filter active (TopBar search, local keyword, leaderboard
  // topic query, category, FILTERS bar, strat trade-filter handoff)?
  const filterActive = !!(searchFilter.trim() || tradeQuery.trim() || marketQuery.trim() || categoryFilter || stratFilters || bar.active);

  // Mark-to-market cumulative P&L, one point per trade, plus a final "NOW"
  // mark that revalues any still-open inventory at current position prices.
  //
  // MTM = cumulative cash flow + value of held inventory at last known price.
  //   BUY  → cash -= px*sz, inventory += sz (instantaneous Δ MTM = 0)
  //   SELL → cash += px*sz, inventory -= sz, also re-marks remaining
  //          inventory of that market to the new fill price
  // We use realized-only for the per-trade tooltip number, but plot MTM so
  // the curve actually moves with BUYs (price discovery on inventory) — a
  // realized-only curve sits flat at $0 for traders who mostly bought in
  // the window, which made the chart look empty.
  const pnlCurve = useMemo((): CurvePoint[] => {
    if (!filteredTrades.length) return [];
    const sorted = [...filteredTrades].sort((a, b) => a.timestamp - b.timestamp);

    // Prefer the trader's current position curPrice as the mark for any
    // market they still hold — this makes BUYs in open positions
    // immediately reflect their forward-looking P&L instead of staying
    // pinned at $0 until the next sell.
    const curByKey = new Map<string, number>();
    for (const p of positions) {
      const key = p.conditionId || p.market;
      if (key && p.currentPrice > 0) curByKey.set(key, p.currentPrice);
    }

    const inv = new Map<string, number>();      // remaining size per market
    const lastPx = new Map<string, number>();   // last-seen price per market
    let cash = 0;
    const markFor = (key: string) =>
      curByKey.get(key) ?? lastPx.get(key) ?? 0;

    const fmtDate = (ts: number) =>
      new Date(ts).toLocaleDateString([], { month: "short", day: "numeric" });
    const fmtTime = (ts: number) =>
      new Date(ts).toLocaleString([], {
        month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
      });

    const points: CurvePoint[] = sorted.map((t, i) => {
      const key = t.conditionId || t.market;
      if (t.side === "BUY") {
        cash -= t.price * t.size;
        inv.set(key, (inv.get(key) || 0) + t.size);
      } else {
        cash += t.price * t.size;
        inv.set(key, (inv.get(key) || 0) - t.size);
      }
      lastPx.set(key, t.price);

      let invValue = 0;
      for (const [k, s] of inv) {
        if (s !== 0) invValue += s * markFor(k);
      }
      const mtm = cash + invValue;

      return {
        i,
        ts: t.timestamp,
        date: fmtDate(t.timestamp),
        time: fmtTime(t.timestamp),
        pnl: Math.round(mtm * 100) / 100,
        side: t.side,
        realized: t.realized,
        market: t.market,
        size: t.size,
        price: t.price,
        buyPrice: t.buyPrice,
        buyTimestamp: t.buyTimestamp,
      };
    });

    // Append a final "NOW" point so the chart shows the latest mark even
    // when no trade has happened in the last few hours — uses the same
    // markFor() lookup as the per-trade points.
    let nowInvValue = 0;
    let hasOpenInventory = false;
    for (const [k, s] of inv) {
      if (Math.abs(s) < 1e-9) continue;
      nowInvValue += s * markFor(k);
      hasOpenInventory = true;
    }
    if (hasOpenInventory) {
      const nowTs = Date.now();
      const nowMtm = cash + nowInvValue;
      points.push({
        i: points.length,
        ts: nowTs,
        date: fmtDate(nowTs),
        time: "NOW",
        pnl: Math.round(nowMtm * 100) / 100,
        side: "MARK",
        realized: 0,
        market: "",
        size: 0,
        price: 0,
      });
    }

    return points;
  }, [filteredTrades, positions]);

  const dailyActivity = useMemo(() => {
    const dayMap = new Map<string, { buys: number; sells: number; volume: number }>();
    for (const t of filteredTrades) {
      const day = new Date(t.timestamp).toLocaleDateString([], { month: "short", day: "numeric" });
      const existing = dayMap.get(day) || { buys: 0, sells: 0, volume: 0 };
      if (t.side === "BUY") existing.buys++;
      else existing.sells++;
      existing.volume += t.size * t.price;
      dayMap.set(day, existing);
    }
    return Array.from(dayMap.entries()).map(([date, data]) => ({ date, ...data }));
  }, [filteredTrades]);

  const stats = useMemo(() => {
    // Only count SELLs where we actually have a cost basis. SELLs whose
    // matching BUY pre-dates our 500-trade history have hasBasis=false
    // and would otherwise pollute every stat with an artificial $0.
    const scoredSells = filteredTrades.filter(
      (t) => t.side === "SELL" && t.hasBasis,
    );
    const wins = scoredSells.filter((t) => t.realized > 0).length;
    const losses = scoredSells.filter((t) => t.realized < 0).length;
    const totalPnl = scoredSells.reduce((s, t) => s + t.realized, 0);
    const avgTrade = scoredSells.length ? totalPnl / scoredSells.length : 0;
    const biggestWin = scoredSells.length ? Math.max(...scoredSells.map((t) => t.realized)) : 0;
    const biggestLoss = scoredSells.length ? Math.min(...scoredSells.map((t) => t.realized)) : 0;
    // `wins`/`losses` count SCORED SELLS and are rendered as exactly that.
    // They are deliberately not turned into a rate here: a position that
    // expired worthless was never sold, so it is in neither counter, and a
    // percentage built from them can only read high. The WIN RATE tile uses
    // `settledStats` below instead.
    return { wins, losses, totalPnl, avgTrade, biggestWin, biggestLoss };
  }, [filteredTrades]);

  /// Win rate off the SETTLED book: of the positions this market has finished
  /// deciding inside the window, the share that returned more than they cost.
  ///
  /// This is the number the leaderboard shows. Counting exits instead — which
  /// is all the trade feed can support — drops every loser that expired
  /// worthless and only ever reads high; on the live board that put traders
  /// at a flat 100%.
  ///
  /// Scoped by the same market-title filter as `filteredTrades`, so a
  /// filtered profile and a filtered board agree about the record.
  const settledStats = useMemo(() => {
    if (!settled) return { rate: -1, wins: 0, decided: 0, known: false };
    const titles = filterActive
      ? new Set(filteredTrades.map((t) => t.market.toLowerCase()))
      : null;
    let wins = 0;
    let decided = 0;
    for (const p of settled) {
      if (p.timestamp < cutoffMs) continue;
      if (titles && !titles.has(p.market.toLowerCase())) continue;
      if (p.totalBought <= 0 && p.realizedPnl === 0) continue;
      decided += 1;
      // Money decides, not the outcome: a position bought at 97¢ that
      // resolves YES and exits at 96¢ resolved your way and still lost.
      if (p.realizedPnl !== 0 ? p.realizedPnl > 0 : p.curPrice >= 0.99) wins += 1;
    }
    return {
      rate: decided > 0 ? Math.round((wins / decided) * 100) : -1,
      wins,
      decided,
      known: true,
    };
  }, [settled, filteredTrades, filterActive, cutoffMs]);

  // Per-market realized results inside the window.
  // "Closed in window" = market had SELL activity in the window AND
  // size sold ≥ size bought across the window (round-trip closed).
  const closedInWindow = useMemo(() => {
    const byMarket = new Map<string, {
      conditionId: string;
      market: string;
      outcome: string;
      bought: number;
      sold: number;
      realized: number;
      lastTs: number;
      tradeCount: number;
    }>();
    for (const t of filteredTrades) {
      const key = t.conditionId || t.market;
      const entry = byMarket.get(key) || {
        conditionId: t.conditionId,
        market: t.market,
        outcome: t.outcome || "",
        bought: 0,
        sold: 0,
        realized: 0,
        lastTs: 0,
        tradeCount: 0,
      };
      if (t.side === "BUY") entry.bought += t.size;
      else entry.sold += t.size;
      if (t.side === "SELL" && t.hasBasis) entry.realized += t.realized;
      entry.lastTs = Math.max(entry.lastTs, t.timestamp);
      entry.tradeCount += 1;
      byMarket.set(key, entry);
    }
    return Array.from(byMarket.values())
      .filter((m) => m.realized !== 0 || m.sold > 0)
      .sort((a, b) => b.realized - a.realized);
  }, [filteredTrades]);

  // Filter positions by search + category so copy-trading targets only
  // matching markets.
  const filteredPositions = useMemo(() => {
    const q = searchFilter.trim().toLowerCase();
    const mq = marketQuery.trim();
    const stratCats = stratFilters?.categories ?? [];
    if (!q && !mq && !categoryFilter && stratCats.length === 0 && bar.keywords.length === 0) return positions;
    return positions.filter((p) => {
      if (q && !p.market.toLowerCase().includes(q)) return false;
      if (mq && !marketMatchesQuery(p.market, mq)) return false;
      if (categoryFilter && !matchMarketCategory(p.market, categoryFilter)) return false;
      // Only the bar's keyword dimension applies — a position has no
      // side/price/size-of-fill to gate on.
      if (!bar.matchesText(`${p.market} ${p.outcome}`)) return false;
      // Positions have no side/price/size-of-fill, so only the strat filter's
      // category dimension can gate them.
      if (stratCats.length > 0 && !stratCats.some((c) => matchMarketCategory(p.market, c))) return false;
      return true;
    });
  }, [positions, searchFilter, marketQuery, categoryFilter, stratFilters, bar.keywords, bar.matchesText]);

  const sortedPositions = useMemo(() => {
    return [...filteredPositions].sort((a, b) => {
      let cmp = 0;
      switch (posSort) {
        case "market": cmp = a.market.localeCompare(b.market); break;
        case "size": cmp = a.size - b.size; break;
        case "avgPrice": cmp = a.avgPrice - b.avgPrice; break;
        case "currentPrice": cmp = a.currentPrice - b.currentPrice; break;
        case "pnlUsd": cmp = a.pnlUsd - b.pnlUsd; break;
      }
      return posSortDir === "desc" ? -cmp : cmp;
    });
  }, [filteredPositions, posSort, posSortDir]);

  // Set of conditionIds with open positions — used to toggle "current" trades
  const openConditionIds = useMemo(() => {
    const ids = new Set<string>();
    for (const p of positions) {
      if (p.conditionId) ids.add(p.conditionId);
    }
    return ids;
  }, [positions]);

  // Trades filtered by showCurrent toggle
  const visibleTrades = useMemo(() => {
    if (showCurrent) return filteredTrades;
    return filteredTrades.filter((t) => !openConditionIds.has(t.conditionId));
  }, [filteredTrades, showCurrent, openConditionIds]);

  // Separate open (current) vs closed trades for tabs
  const openTrades = useMemo(
    () => filteredTrades.filter((t) => openConditionIds.has(t.conditionId)),
    [filteredTrades, openConditionIds],
  );
  const closedTrades = useMemo(
    () => filteredTrades.filter((t) => !openConditionIds.has(t.conditionId)),
    [filteredTrades, openConditionIds],
  );

  // Trades for the active TRADES view
  const tabTrades = useMemo(() => {
    if (tradeView === "open") return openTrades;
    if (tradeView === "closed") return closedTrades;
    return filteredTrades; // "all"
  }, [tradeView, openTrades, closedTrades, filteredTrades]);

  // ── INFO tab — the shape of this trader's activity, not the P&L of it.
  // Everything here is derived from the same filtered flow the other tabs
  // show, so INFO never contradicts what's on screen.
  const info = useMemo(() => {
    const buys = filteredTrades.filter((t) => t.side === "BUY");
    const sells = filteredTrades.filter((t) => t.side === "SELL");
    const notional = (ts: typeof filteredTrades) => ts.reduce((s, t) => s + t.size * t.price, 0);
    const timestamps = filteredTrades.map((t) => t.timestamp);
    const markets = new Set(filteredTrades.map((t) => t.conditionId || t.market));
    // Category mix by trade count — a trader's actual beat, not the buckets
    // they'd claim. Untagged titles fall into OTHER.
    const mix: { label: string; slug: CategorySlug; count: number }[] = CATEGORIES
      .filter((c) => c.slug)
      .map((c) => ({
        label: c.label as string,
        slug: c.slug as CategorySlug,
        count: filteredTrades.filter((t) => matchMarketCategory(t.market, c.slug)).length,
      }));
    const untagged = filteredTrades.filter(
      (t) => !CATEGORIES.some((c) => c.slug && matchMarketCategory(t.market, c.slug)),
    ).length;
    if (untagged > 0) mix.push({ label: "OTHER", slug: "" as CategorySlug, count: untagged });
    return {
      buyCount: buys.length,
      sellCount: sells.length,
      buyNotional: notional(buys),
      sellNotional: notional(sells),
      avgSize: filteredTrades.length ? notional(filteredTrades) / filteredTrades.length : 0,
      firstTs: timestamps.length ? Math.min(...timestamps) : 0,
      lastTs: timestamps.length ? Math.max(...timestamps) : 0,
      markets: markets.size,
      openValue: filteredPositions.reduce((s, p) => s + p.value, 0),
      openPnl: filteredPositions.reduce((s, p) => s + p.pnlUsd, 0),
      resolved: filteredPositions.filter((p) => p.redeemable).length,
      mix: mix.filter((m) => m.count > 0).sort((a, b) => b.count - a.count),
    };
  }, [filteredTrades, filteredPositions]);

  const sortedTrades = useMemo(() => {
    return [...tabTrades].sort((a, b) => {
      let cmp = 0;
      switch (tradeSort) {
        case "timestamp": cmp = a.timestamp - b.timestamp; break;
        case "market": cmp = a.market.localeCompare(b.market); break;
        case "price": cmp = a.price - b.price; break;
        case "size": cmp = (a.size * a.price) - (b.size * b.price); break;
        case "pnl": cmp = a.realized - b.realized; break;
      }
      return tradeSortDir === "desc" ? -cmp : cmp;
    });
  }, [tabTrades, tradeSort, tradeSortDir]);

  return (
    <div className="space-y-3">
      {/* ── Header: back, identity, lookback and watch all on one line ──
          Lookback pills set how far back this trader's stats/curve/tables
          reach. The choice is saved for THIS trader (persisted by the page);
          clicking the active pill again clears it back to the global window. */}
      <div className="flex items-center gap-2 flex-wrap">
        <button
          onClick={onBack}
          className="pixel-btn border-pixel-border text-pixel-gray hover:text-pixel-white hover:border-pixel-white text-[16px] py-1"
        >
          BACK
        </button>
        <span className="text-sm text-pixel-white glow-green font-mono">
          {shortAddress(trader.address)}
        </span>
        <button
          onClick={() => navigator.clipboard.writeText(trader.address)}
          className="text-[15px] text-pixel-gray hover:text-pixel-white"
        >
          [COPY]
        </button>
        {/* THE primary action: put money against this trader. */}
        {onCopyToDesk &&
          (deskAllocationUsd !== null ? (
            <Link
              href={`/copy/${trader.address.toLowerCase()}`}
              title="Already on the copy desk — open their workspace to change the amount, backtest them or start them"
              className="pixel-btn border-pixel-green/70 text-pixel-green hover:text-pixel-white hover:border-pixel-white text-[13px] py-1 tracking-wider"
            >
              ✓ ON DESK ${deskAllocationUsd.toLocaleString("en-US")}
            </Link>
          ) : deskOpen ? (
            <span className="inline-flex items-center gap-1">
              <input
                autoFocus
                className="pixel-input-sm w-24 font-mono text-[13px]"
                value={deskAmount}
                inputMode="decimal"
                onChange={(e) => setDeskAmount(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Escape") setDeskOpen(false);
                  if (e.key === "Enter") (e.target as HTMLInputElement).blur();
                }}
                placeholder="$"
              />
              <button
                className="pixel-btn border-pixel-green/70 text-pixel-green text-[13px] py-1 tracking-wider"
                disabled={deskBusy || !(Number(deskAmount) > 0)}
                onClick={async () => {
                  setDeskBusy(true);
                  try {
                    // Carry the topic gate the profile is being read under —
                    // "find on bitcoin, copy on bitcoin" has to survive the
                    // quick-add too, not just the simulator's CTA.
                    await onCopyToDesk(Number(deskAmount), {
                      marketQuery: marketQuery.trim(),
                    });
                    setDeskOpen(false);
                  } finally {
                    setDeskBusy(false);
                  }
                }}
              >
                {deskBusy ? "…" : "ADD TO DESK"}
              </button>
            </span>
          ) : (
            <button
              onClick={() => setDeskOpen(true)}
              title={`Copy ${shortAddress(trader.address)} with an amount — adds them to the copy desk`}
              className="pixel-btn border-pixel-green/70 text-pixel-green hover:text-pixel-white hover:border-pixel-white text-[13px] py-1 tracking-wider"
            >
              ＋ COPY WITH $
            </button>
          ))}
        {/* There is no second copy button. "＋ COPY WITH $" used to sit beside
            "⑂ IDENTITY", which forked a local strat that copied this one trader
            — the same act, in a second vocabulary, writing to a second store.
            Copying a trader is one thing now: a row on the server's copy book. */}

        {onDaysChange && (
          <>
            <span className="text-[13px] text-pixel-gray tracking-wider ml-2">LOOKBACK</span>
            {LOOKBACK_PRESETS.map((d) => {
              const active = days === d;
              return (
                <button
                  key={d}
                  onClick={() => onDaysChange(daysOverride === d ? null : d)}
                  title={`Last ${d} days`}
                  className={`px-2 py-1 border-2 text-[13px] font-mono tracking-wider transition-colors ${
                    active
                      ? daysOverride !== null
                        ? "border-pixel-white text-pixel-white bg-pixel-white/10"
                        : "border-pixel-gray-light text-pixel-gray-light"
                      : "border-pixel-border text-pixel-gray hover:border-pixel-white hover:text-pixel-white"
                  }`}
                >
                  {`${d}D`}
                </button>
              );
            })}
            {/* Custom non-preset override shows as its own active pill */}
            {days > 0 && !LOOKBACK_PRESETS.includes(days) && (
              <button
                onClick={() => onDaysChange(null)}
                title={`Custom ${days}-day window — click to reset`}
                className="px-2 py-1 border-2 border-pixel-white text-pixel-white bg-pixel-white/10 text-[13px] font-mono tracking-wider"
              >
                {days}D
              </button>
            )}
            <input
              type="text"
              inputMode="numeric"
              value={customDays}
              onChange={(e) => setCustomDays(e.target.value.replace(/[^0-9]/g, "").slice(0, 2))}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  const n = parseInt(customDays, 10);
                  if (Number.isFinite(n) && n > 0 && n <= 30) onDaysChange(n);
                  setCustomDays("");
                }
              }}
              onBlur={() => {
                const n = parseInt(customDays, 10);
                if (Number.isFinite(n) && n > 0 && n <= 30) onDaysChange(n);
                setCustomDays("");
              }}
              placeholder="N"
              title="Custom lookback in days (1–30) — Enter to apply"
              className="w-10 bg-transparent border-2 border-pixel-border px-1 py-1 text-[13px] font-mono text-pixel-white text-center placeholder:text-pixel-gray focus:border-pixel-white outline-none"
            />
            <span className="text-[12px] text-pixel-gray tracking-wider">
              {daysOverride !== null
                ? "SAVED"
                : `GLOBAL${globalDays ? ` ${globalDays}D` : ""}`}
            </span>
          </>
        )}

        <button
          onClick={onToggleWatch}
          className={`pixel-btn text-[16px] py-1 ml-auto ${
            watching
              ? "border-pixel-white text-pixel-white bg-pixel-white/10"
              : "border-pixel-border text-pixel-gray hover:border-pixel-white hover:text-pixel-white"
          }`}
        >
          {watching ? "WATCHING" : "WATCH"}
        </button>
      </div>

      {/* ── The filter rail — ALWAYS on screen ──
          The market gate the board was found with, still adjustable after you
          open the trader. It narrows the stats, the curve, every tab AND the
          copy simulator, so one control surface answers "which slice is this?"
          for the whole page. See components/ProfileFilters.tsx. */}
      <ProfileFilters
        marketQuery={marketQuery}
        onMarketQueryChange={onMarketQueryChange}
        category={categoryFilter}
        onCategoryChange={onCategoryChange}
        bar={bar}
        matched={filteredTrades.length}
        total={tradesInWindow.length}
        stickyTopPx={stickyTopPx}
      />

      {/* ── Strat trade-filter chip ──
          Shown when the profile was opened from a strat's trader list with
          active per-trade filters: everything below (stats, curve, tables)
          is gated exactly like the copy engine gates live flow. ✕ reverts
          to the trader's full history. */}
      {stratFilters && (
        <div className="pixel-panel p-3 flex items-center gap-2 flex-wrap">
          <span className="text-[13px] text-pixel-gray tracking-wider">
            STRAT FILTER{stratFilterName ? ` — ${stratFilterName.toUpperCase()}` : ""}
          </span>
          <span className="border-2 border-pixel-white px-2 py-1 text-[13px] font-mono text-pixel-white">
            {describeTradeFilters(stratFilters) || "no filters — every trade"}
          </span>
          <span className="text-[12px] text-pixel-gray tracking-wider">
            SHOWING ONLY TRADES THIS STRAT WOULD COPY
          </span>
          {onClearStratFilters && (
            <button
              onClick={onClearStratFilters}
              className="text-[14px] text-pixel-gray hover:text-pixel-white px-1"
              title="Clear the strat filter — show all trades"
            >
              ✕
            </button>
          )}
        </div>
      )}

      {loading ? (
        <div className="pixel-panel p-12 text-center">
          <div className="text-sm text-pixel-white animate-pulse glow-green">
            {`LOADING ${dayLabel} HISTORY...`}
          </div>
        </div>
      ) : (
        <>
          {/* Trade-sync failure — say so loudly. Without this banner a
              rate-limited /activity fetch rendered "$0 / 0 trades" stats
              that looked like a real answer next to 100 open positions. */}
          {tradesError && (
            <div className="pixel-panel-red p-4 flex items-center justify-between gap-3 flex-wrap">
              <div>
                <div className="text-[15px] text-red-400 tracking-wider">
                  TRADE HISTORY SYNC FAILED — {tradesError}
                </div>
                <div className="text-[13px] text-pixel-gray mt-1">
                  {trades.length > 0
                    ? `SHOWING ${trades.length} TRADES FETCHED BEFORE THE FAILURE — STATS MAY BE INCOMPLETE`
                    : "STATS BELOW ARE NOT REAL ZEROS — THE TRADE FEED COULD NOT BE LOADED"}
                </div>
              </div>
              {onRetrySync && (
                <button
                  onClick={onRetrySync}
                  className="pixel-btn border-pixel-white text-pixel-white hover:bg-pixel-white/10 text-[15px]"
                >
                  RETRY SYNC
                </button>
              )}
            </div>
          )}

          {/* Not a failure — a ceiling. Polymarket's activity feed refuses to
              page past 5,500 rows for any wallet, and the traders worth
              copying are exactly the ones who blow through that inside the
              window. Say what IS held rather than pretending the window was
              covered: every stat below is a floor. */}
          {feedDepthCapped && !tradesError && (
            <div className="pixel-panel p-4 border-amber-500/50">
              <div className="text-[15px] text-amber-400 tracking-wider">
                {`FEED DEPTH LIMIT — POLYMARKET SERVES AT MOST ${MAX_ACTIVITY_ROWS.toLocaleString()} ACTIVITY ROWS PER WALLET`}
              </div>
              <div className="text-[13px] text-pixel-gray mt-1">
                {oldestTradeMs > 0
                  ? `THIS TRADER HAS MORE THAN THAT IN ${dayLabel} — HISTORY BELOW REACHES BACK ${timeAgo(oldestTradeMs)}, NOT ${dayLabel}. WINDOW STATS ARE FLOORS.`
                  : `THIS TRADER HAS MORE THAN THAT IN ${dayLabel} — ONLY THE MOST RECENT SLICE IS SHOWN. WINDOW STATS ARE FLOORS.`}
              </div>
            </div>
          )}

          {/* Stats Grid */}
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
            {([
              { label: `${dayLabel} P&L`, value: formatPnl(stats.totalPnl), tone: stats.totalPnl > 0 ? "good" : stats.totalPnl < 0 ? "bad" : "neutral" },
              {
                label: settledStats.decided > 0 ? `WIN RATE · ${settledStats.decided}` : "WIN RATE",
                value: settledStats.rate < 0 ? "—" : `${settledStats.rate}%`,
                tone: settledStats.rate < 0 ? "neutral" : settledStats.rate >= 50 ? "good" : "bad",
                hint: !settledStats.known
                  ? "Settled book not loaded — a rate off the trade feed alone would only count winners, so this stays blank."
                  : settledStats.decided === 0
                    ? "Nothing this trader bought has settled inside the window yet."
                    : `${settledStats.wins} of ${settledStats.decided} settled position(s) returned more than they cost. Includes positions that expired worthless — those leave no sell and no redeem in the trade feed.${
                        settledStats.decided < 10 ? " Thin sample — treat as noise." : ""
                      }`,
              },
              { label: "TRADES", value: filterActive ? `${filteredTrades.length}/${tradesInWindow.length}` : filteredTrades.length.toString(), tone: "neutral" },
              { label: "VOLUME", value: formatVolume(filteredTrades.reduce((s, t) => s + t.size * t.price, 0)), tone: "neutral" },
              { label: "AVG TRADE", value: formatPnl(stats.avgTrade), tone: stats.avgTrade > 0 ? "good" : stats.avgTrade < 0 ? "bad" : "neutral" },
              { label: "POSITIONS", value: filterActive ? `${filteredPositions.length}/${positions.length}` : positions.length.toString(), tone: "neutral" },
            ] as const).map((stat) => {
              const valueClass =
                stat.tone === "good" ? "text-green-400" :
                stat.tone === "bad" ? "text-red-400" :
                "text-pixel-white glow-green";
              return (
                <div
                  key={stat.label}
                  className="pixel-panel px-3 py-2 text-center"
                  title={"hint" in stat ? stat.hint : undefined}
                >
                  <div className="text-[14px] text-pixel-gray tracking-wider mb-1">
                    {stat.label}
                  </div>
                  <div className={`text-sm ${valueClass}`}>
                    {stat.value}
                  </div>
                </div>
              );
            })}
          </div>

          {/* ── SIMULATE THE COPY ──
              The stats above are the TRADER's record. This is YOURS: the same
              window replayed as a copy of them, at a dollar amount you name,
              under the gate the rail is showing. It sits above the tape on
              purpose — deciding whether to copy someone is the reason this
              page is open, and it used to require adding them first. */}
          {onCopyToDesk && (
            <CopySimPanel
              address={trader.address}
              trades={trades}
              positions={positions}
              days={days}
              marketQuery={marketQuery}
              tradeFilters={
                // The rail's enforceable dimensions, plus the strat handoff
                // when the profile was opened from one. Keywords are excluded
                // deliberately — the panel says why.
                bar.tfActive || categoryFilter || stratFilters
                  ? {
                      ...(stratFilters ?? {}),
                      ...(bar.tfActive ? bar.tf : {}),
                      ...(categoryFilter ? { categories: [categoryFilter] } : {}),
                    }
                  : null
              }
              keywords={bar.keywords}
              onUseKeywordsAsTopic={
                onMarketQueryChange && bar.keywords.length > 0
                  ? () => {
                      onMarketQueryChange(bar.keywords.join(", "));
                      bar.clear();
                    }
                  : undefined
              }
              loading={loading}
              feedDepthCapped={feedDepthCapped}
              deskAllocationUsd={deskAllocationUsd}
              onCopyToDesk={onCopyToDesk}
            />
          )}

          {/* ── Tabbed: TRADES / P&L / INFO ── */}
          <div className="pixel-panel overflow-hidden">
            {/* Tab bar */}
            <div className="flex items-center border-b-2 border-pixel-border">
              {([
                { id: "trades" as ProfileTab, label: "TRADES", count: filteredTrades.length },
                { id: "pnl" as ProfileTab, label: "P&L", count: null },
                { id: "info" as ProfileTab, label: "INFO", count: null },
              ]).map((tab) => (
                <button
                  key={tab.id}
                  onClick={() => setProfileTab(tab.id)}
                  className={`px-4 py-3 text-[15px] font-mono tracking-wider transition-colors border-b-2 -mb-[2px] ${
                    profileTab === tab.id
                      ? "border-pixel-white text-pixel-white"
                      : "border-transparent text-pixel-gray hover:text-pixel-white"
                  }`}
                >
                  {tab.label}
                  {tab.count !== null && (
                    <span className="ml-1.5 text-[13px] text-pixel-gray">{tab.count}</span>
                  )}
                </button>
              ))}
              <div className="ml-auto mr-2 flex items-center gap-1.5 min-w-0">
                {/* Topic chip — the leaderboard keyword this trader was found
                    with. It narrows every tab, so it has to be visible here:
                    an invisible filter that hides most of a tape reads as a
                    trader who barely trades. ✕ shows their whole flow. */}
                {marketQuery.trim() && (
                  <>
                    <span
                      className="border-2 border-green-400/60 text-green-400 px-2 py-1 text-[13px] font-mono truncate max-w-[280px]"
                      title={`Showing only markets matching “${marketQuery.trim()}” — the keyword the traders list was ranked by. Every number on this page is that slice, not their whole record.`}
                    >
                      {marketQuery.trim()}
                    </span>
                    {onClearMarketQuery && (
                      <button
                        onClick={onClearMarketQuery}
                        className="text-[14px] text-pixel-gray hover:text-pixel-white px-1"
                        title="Clear the keyword filter — show every market this trader traded"
                      >
                        ✕
                      </button>
                    )}
                  </>
                )}
                {/* Selected-market chip — only shown once a market is picked
                    (click a market name in any table below). Narrows every tab
                    (P&L curve, trade tables) and the stats above. */}
                {tradeQuery && (
                  <>
                    <span
                      className="border-2 border-pixel-border px-2 py-1 text-[13px] font-mono text-pixel-white truncate max-w-[280px]"
                      title={tradeQuery}
                    >
                      {tradeQuery}
                    </span>
                    <button
                      onClick={() => setTradeQuery("")}
                      className="text-[14px] text-pixel-gray hover:text-pixel-white px-1"
                      title="Clear market filter"
                    >
                      ✕
                    </button>
                  </>
                )}
              </div>
            </div>
            {/* No FILTERS toggle here — the dimensions it used to hide are in
                the sticky rail above, which is on screen whichever tab this
                is. Two places to set one filter is how a tape ends up narrowed
                by something you can't see. */}

            {/* Tab content */}
            {profileTab === "trades" ? (
              <>
                {/* Which slice of the flow — the old top-level tabs, now a
                    view switch inside TRADES. */}
                <div className="flex items-center gap-1.5 flex-wrap px-3 py-2 border-b-2 border-pixel-border">
                  {([
                    { id: "all" as TradeView, label: "ALL", count: filteredTrades.length },
                    { id: "open" as TradeView, label: "OPEN", count: openTrades.length },
                    { id: "closed" as TradeView, label: "CLOSED", count: closedTrades.length },
                    { id: "positions" as TradeView, label: "POSITIONS", count: filteredPositions.length },
                  ]).map((v) => (
                    <button
                      key={v.id}
                      onClick={() => setTradeView(v.id)}
                      className={`pixel-btn text-[13px] px-2 py-0.5 font-mono transition-colors ${
                        tradeView === v.id
                          ? "border-green-400 text-green-400 bg-green-400/10"
                          : "border-pixel-border text-pixel-gray hover:text-pixel-white hover:border-pixel-white"
                      }`}
                    >
                      {v.label}
                      <span className="ml-1.5 text-[12px] opacity-70">{v.count}</span>
                    </button>
                  ))}
                  {filterActive && (
                    <span className="ml-auto text-[12px] text-pixel-gray font-mono tracking-wider">
                      {`${filteredTrades.length}/${tradesInWindow.length} TRADES MATCH`}
                    </span>
                  )}
                </div>
                {tradeView === "positions" ? (
                /* ── Current open positions (data-api /positions) ── */
                sortedPositions.length > 0 ? (
                  <div className="max-h-[500px] overflow-y-auto overflow-x-auto">
                    <table className="pixel-table" style={{ tableLayout: "fixed", width: "100%", minWidth: "700px" }}>
                      <colgroup>
                        <col style={{ width: "34%" }} />
                        <col style={{ width: "70px" }} />
                        <col style={{ width: "80px" }} />
                        <col style={{ width: "70px" }} />
                        <col style={{ width: "70px" }} />
                        <col style={{ width: "80px" }} />
                        <col style={{ width: "85px" }} />
                      </colgroup>
                      <thead>
                        <tr>
                          <th className={`sortable ${posSort === "market" ? "sorted" : ""}`} onClick={() => handlePosSort("market")}>
                            MARKET <SortArrow active={posSort === "market"} dir={posSortDir} />
                          </th>
                          <th>OUTCOME</th>
                          <th className={`sortable text-right ${posSort === "size" ? "sorted" : ""}`} onClick={() => handlePosSort("size")}>
                            SHARES <SortArrow active={posSort === "size"} dir={posSortDir} />
                          </th>
                          <th className={`sortable text-right ${posSort === "avgPrice" ? "sorted" : ""}`} onClick={() => handlePosSort("avgPrice")}>
                            AVG <SortArrow active={posSort === "avgPrice"} dir={posSortDir} />
                          </th>
                          <th className={`sortable text-right ${posSort === "currentPrice" ? "sorted" : ""}`} onClick={() => handlePosSort("currentPrice")}>
                            NOW <SortArrow active={posSort === "currentPrice"} dir={posSortDir} />
                          </th>
                          <th className="text-right">VALUE</th>
                          <th className={`sortable text-right ${posSort === "pnlUsd" ? "sorted" : ""}`} onClick={() => handlePosSort("pnlUsd")}>
                            P&L <SortArrow active={posSort === "pnlUsd"} dir={posSortDir} />
                          </th>
                        </tr>
                      </thead>
                      <tbody>
                        {sortedPositions.slice(0, 300).map((p, i) => {
                          const profit = p.pnlUsd > 0;
                          const flat = p.pnlUsd === 0;
                          return (
                            <tr key={`${p.conditionId}-${p.outcome}-${i}`}>
                              <td
                                className={`truncate cursor-pointer hover:text-green-400 ${
                                  tradeQuery === p.market ? "text-green-400" : "text-pixel-white"
                                }`}
                                title={`${p.market} — click to filter trades to this market`}
                                onClick={() =>
                                  setTradeQuery((q) => (q === p.market ? "" : p.market))
                                }
                              >
                                {p.market}
                              </td>
                              <td>
                                <span className="pixel-badge border-pixel-gray-light text-pixel-gray-light">
                                  {p.outcome || "—"}{p.redeemable ? " · RESOLVED" : ""}
                                </span>
                              </td>
                              <td className="num text-right text-pixel-white font-mono">
                                {p.size.toFixed(0)}
                              </td>
                              <td className="num text-right text-pixel-gray-light font-mono">
                                {Math.round(p.avgPrice * 100)}¢
                              </td>
                              <td className="num text-right text-pixel-white font-mono">
                                {Math.round(p.currentPrice * 100)}¢
                              </td>
                              <td className="num text-right text-pixel-white font-mono">
                                ${p.value.toFixed(2)}
                              </td>
                              <td className={`num text-right font-mono ${flat ? "text-pixel-gray-light" : profit ? "text-green-400" : "text-red-400"}`}>
                                {`${p.pnlUsd >= 0 ? "+" : ""}$${p.pnlUsd.toFixed(2)}`}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <div className="p-8 text-center">
                    <div className="text-[15px] text-pixel-gray">
                      {filterActive ? "NO MATCHING POSITIONS" : "NO OPEN POSITIONS"}
                    </div>
                  </div>
                )
                ) : (
                  /* ── Trade tables for open / closed / all ── */
                sortedTrades.length > 0 ? (
                  <div className="max-h-[500px] overflow-y-auto overflow-x-auto">
                    <table className="pixel-table" style={{ tableLayout: "fixed", width: "100%", minWidth: "700px" }}>
                      <colgroup>
                        <col style={{ width: "65px" }} />
                        <col style={{ width: "32%" }} />
                        <col style={{ width: "42px" }} />
                        <col style={{ width: "60px" }} />
                        <col style={{ width: "80px" }} />
                        <col style={{ width: "60px" }} />
                        <col style={{ width: "80px" }} />
                      </colgroup>
                      <thead>
                        <tr>
                          <th className={`sortable ${tradeSort === "timestamp" ? "sorted" : ""}`} onClick={() => handleTradeSort("timestamp")}>
                            TIME <SortArrow active={tradeSort === "timestamp"} dir={tradeSortDir} />
                          </th>
                          <th className={`sortable ${tradeSort === "market" ? "sorted" : ""}`} onClick={() => handleTradeSort("market")}>
                            MARKET <SortArrow active={tradeSort === "market"} dir={tradeSortDir} />
                          </th>
                          <th>SIDE</th>
                          <th className={`sortable text-right ${tradeSort === "price" ? "sorted" : ""}`} onClick={() => handleTradeSort("price")}>
                            PRICE <SortArrow active={tradeSort === "price"} dir={tradeSortDir} />
                          </th>
                          <th className={`sortable text-right ${tradeSort === "size" ? "sorted" : ""}`} onClick={() => handleTradeSort("size")}>
                            SIZE <SortArrow active={tradeSort === "size"} dir={tradeSortDir} />
                          </th>
                          <th className="text-right">ENTRY</th>
                          <th className={`sortable text-right ${tradeSort === "pnl" ? "sorted" : ""}`} onClick={() => handleTradeSort("pnl")}>
                            P&L <SortArrow active={tradeSort === "pnl"} dir={tradeSortDir} />
                          </th>
                        </tr>
                      </thead>
                      <tbody>
                        {sortedTrades.slice(0, 200).map((trade, i) => {
                          const isEntering = trade.side === "BUY";
                          const showRealized = !isEntering && trade.hasBasis;
                          const isProfit = showRealized && trade.realized > 0;
                          const sideColor = isEntering
                            ? "border-pixel-gray-light text-pixel-gray-light"
                            : !showRealized
                            ? "border-pixel-gray text-pixel-gray"
                            : isProfit
                            ? "border-green-400 text-green-400"
                            : "border-red-400 text-red-400";
                          const pnlColor = !showRealized
                            ? "text-pixel-gray-light"
                            : isProfit
                            ? "text-green-400"
                            : "text-red-400";
                          const hasBuyInfo = !isEntering && trade.buyPrice !== undefined;
                          return (
                            <tr key={`${trade.id}-${i}`}>
                              <td className="text-pixel-gray font-mono">
                                {timeAgo(trade.timestamp)}
                              </td>
                              <td
                                className={`truncate cursor-pointer hover:text-green-400 ${
                                  tradeQuery === trade.market ? "text-green-400" : "text-pixel-white"
                                }`}
                                title={`${trade.market} — click to filter to this market`}
                                onClick={() =>
                                  setTradeQuery((q) => (q === trade.market ? "" : trade.market))
                                }
                              >
                                {trade.market}
                              </td>
                              <td>
                                <span className={`pixel-badge ${sideColor}`}>
                                  {trade.side}
                                </span>
                              </td>
                              <td className="num text-right text-pixel-white font-mono">
                                {Math.round(trade.price * 100)}¢
                              </td>
                              <td className="num text-right text-pixel-white font-mono">
                                ${(trade.size * trade.price).toFixed(2)}
                              </td>
                              <td className="num text-right font-mono text-pixel-gray-light">
                                {hasBuyInfo ? `${Math.round(trade.buyPrice! * 100)}¢` : isEntering ? "" : "\u2014"}
                              </td>
                              <td
                                className={`num text-right font-mono ${pnlColor}`}
                                title={!showRealized && !isEntering ? "no cost basis in trade history" : undefined}
                              >
                                {isEntering
                                  ? ""
                                  : !showRealized
                                  ? "\u2014"
                                  : `${trade.realized >= 0 ? "+" : ""}$${trade.realized.toFixed(2)}`}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <div className="p-8 text-center">
                    <div className="text-[15px] text-pixel-gray">
                      {filterActive
                        ? "NO TRADES MATCH THESE FILTERS"
                        : tradeView === "open"
                        ? "NO OPEN TRADES"
                        : tradeView === "closed"
                        ? "NO CLOSED TRADES"
                        : "NO TRADES"}
                    </div>
                  </div>
                  )
                )}
              </>
            ) : profileTab === "pnl" ? (
              <div className="p-0">
                {pnlCurve.length > 0 ? (
                  <PnlChart points={pnlCurve} dayLabel={dayLabel} tradesInWindow={filteredTrades} filtered={filterActive} />
                ) : (
                  <div className="p-8 text-center">
                    <div className="text-[16px] text-pixel-gray-light tracking-wider mb-2">
                      {`${dayLabel} P&L CURVE`}
                    </div>
                    <div className="text-[15px] text-pixel-gray">
                      {tradesError
                        ? "TRADE FEED UNAVAILABLE — RETRY SYNC ABOVE"
                        : filterActive
                        ? "NO MATCHING TRADES — TRY A DIFFERENT FILTER"
                        : positions.length > 0
                        ? "NO TRADES IN WINDOW — CHECK POSITIONS TAB"
                        : "NO TRADE DATA"}
                    </div>
                  </div>
                )}
              </div>
            ) : (
              /* ── INFO — who this trader is, and how the numbers above
                 were built. Everything here honors the same filters. ── */
              <div className="p-4 space-y-4">
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
                  {/* Identity */}
                  <div className="border-2 border-pixel-border p-3 space-y-2">
                    <div className="text-[13px] text-pixel-gray tracking-wider">ADDRESS</div>
                    <div className="font-mono text-[13px] text-pixel-white break-all">{trader.address}</div>
                    <div className="flex items-center gap-3 flex-wrap text-[13px] font-mono">
                      <button
                        onClick={() => navigator.clipboard.writeText(trader.address)}
                        className="text-pixel-gray hover:text-pixel-white"
                      >
                        [COPY]
                      </button>
                      <a
                        href={`https://polymarket.com/profile/${trader.address}`}
                        target="_blank"
                        rel="noreferrer"
                        className="text-pixel-gray hover:text-green-400"
                      >
                        [POLYMARKET ↗]
                      </a>
                      <a
                        href={`https://polygonscan.com/address/${trader.address}`}
                        target="_blank"
                        rel="noreferrer"
                        className="text-pixel-gray hover:text-green-400"
                      >
                        [POLYGONSCAN ↗]
                      </a>
                      <span className={watching ? "text-pixel-white" : "text-pixel-gray"}>
                        {watching ? "· ON WATCHLIST" : "· NOT WATCHED"}
                      </span>
                    </div>
                  </div>
                  {/* Where the data came from — a window + sync provenance
                      card, so a thin-looking profile is explainable. */}
                  <div className="border-2 border-pixel-border p-3 space-y-1.5 font-mono text-[13px]">
                    {([
                      ["WINDOW", `${dayLabel}${daysOverride !== null ? " (PINNED)" : ""}`],
                      ["TRADES SYNCED", `${trades.length} · ${filteredTrades.length} AFTER FILTERS`],
                      ["FIRST TRADE", info.firstTs ? new Date(info.firstTs).toLocaleString() : "—"],
                      ["LAST TRADE", info.lastTs ? timeAgo(info.lastTs).toUpperCase() : "—"],
                      ["MARKETS TRADED", `${info.markets}`],
                      ["FEED", tradesError
                        ? `SYNC FAILED — ${tradesError}`
                        : feedDepthCapped
                        ? `DEPTH-CAPPED AT ${MAX_ACTIVITY_ROWS.toLocaleString()} UPSTREAM ROWS`
                        : "OK"],
                    ] as const).map(([k, v]) => (
                      <div key={k} className="flex items-baseline gap-2">
                        <span className="text-pixel-gray tracking-wider w-[130px] shrink-0">{k}</span>
                        <span className={
                          k === "FEED" && tradesError ? "text-red-400"
                            : k === "FEED" && feedDepthCapped ? "text-amber-400"
                              : "text-pixel-white"
                        }>{v}</span>
                      </div>
                    ))}
                  </div>
                </div>

                {/* Flow shape — buy/sell split and exposure, the things the
                    P&L tab can't tell you. */}
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                  {([
                    { label: "BUYS", value: `${info.buyCount}`, sub: formatVolume(info.buyNotional) },
                    { label: "SELLS", value: `${info.sellCount}`, sub: formatVolume(info.sellNotional) },
                    { label: "AVG SIZE", value: formatVolume(info.avgSize), sub: "PER FILL" },
                    { label: "WINS / LOSSES", value: `${stats.wins} / ${stats.losses}`, sub: "SCORED SELLS" },
                    { label: "OPEN VALUE", value: formatVolume(info.openValue), sub: `${filteredPositions.length} POSITIONS` },
                    { label: "OPEN P&L", value: formatPnl(info.openPnl), sub: "UNREALIZED" },
                    { label: "RESOLVED", value: `${info.resolved}`, sub: "REDEEMABLE" },
                    { label: "TURNOVER", value: formatVolume(info.buyNotional + info.sellNotional), sub: dayLabel },
                  ] as const).map((c) => (
                    <div key={c.label} className="border-2 border-pixel-border p-3">
                      <div className="text-[13px] text-pixel-gray tracking-wider mb-1">{c.label}</div>
                      <div
                        className={`text-[15px] font-mono ${
                          c.label === "OPEN P&L"
                            ? info.openPnl > 0
                              ? "text-green-400"
                              : info.openPnl < 0
                              ? "text-red-400"
                              : "text-pixel-white"
                            : "text-pixel-white"
                        }`}
                      >
                        {c.value}
                      </div>
                      <div className="text-[12px] text-pixel-gray tracking-wider mt-0.5">{c.sub}</div>
                    </div>
                  ))}
                </div>

                {/* What they actually trade — category mix by fill count.
                    Click a bucket to filter the whole profile to it. */}
                {info.mix.length > 0 && (
                  <div className="border-2 border-pixel-border p-3">
                    <div className="text-[13px] text-pixel-gray tracking-wider mb-2">MARKET MIX (BY FILLS)</div>
                    <div className="space-y-1">
                      {info.mix.map((m) => {
                        const pct = Math.round((m.count / filteredTrades.length) * 100);
                        const clickable = !!m.slug && !!onCategoryChange;
                        return (
                          <button
                            key={m.label}
                            disabled={!clickable}
                            onClick={() => onCategoryChange?.(categoryFilter === m.slug ? "" : m.slug)}
                            title={clickable ? `Filter this profile to ${m.label}` : undefined}
                            className={`w-full flex items-center gap-2 font-mono text-[13px] group ${
                              clickable ? "cursor-pointer" : "cursor-default"
                            }`}
                          >
                            <span
                              className={`w-[90px] shrink-0 text-left tracking-wider ${
                                categoryFilter && categoryFilter === m.slug ? "text-green-400" : "text-pixel-gray-light"
                              } ${clickable ? "group-hover:text-green-400" : ""}`}
                            >
                              {m.label}
                            </span>
                            <span className="flex-1 h-2 bg-pixel-border/40">
                              <span
                                className={`block h-full ${
                                  categoryFilter && categoryFilter === m.slug ? "bg-green-400" : "bg-pixel-gray-light"
                                }`}
                                style={{ width: `${Math.max(2, pct)}%` }}
                              />
                            </span>
                            <span className="w-[76px] shrink-0 text-right text-pixel-white">
                              {m.count} · {pct}%
                            </span>
                          </button>
                        );
                      })}
                    </div>
                    <div className="text-[12px] text-pixel-gray mt-2">
                      BUCKETS OVERLAP — A TITLE CAN MATCH MORE THAN ONE
                    </div>
                  </div>
                )}

                {/* Exactly what is narrowing this page right now. */}
                <div className="border-2 border-pixel-border p-3 font-mono text-[13px] space-y-1">
                  <div className="text-[13px] text-pixel-gray tracking-wider mb-1">ACTIVE FILTERS</div>
                  {filterActive ? (
                    <div className="flex items-center gap-1.5 flex-wrap">
                      {searchFilter.trim() && (
                        <span className="pixel-badge border-pixel-gray-light text-pixel-gray-light">SEARCH “{searchFilter.trim()}”</span>
                      )}
                      {tradeQuery && (
                        <span className="pixel-badge border-pixel-gray-light text-pixel-gray-light truncate max-w-[320px]">MARKET “{tradeQuery}”</span>
                      )}
                      {marketQuery.trim() && (
                        <span className="pixel-badge border-green-400/60 text-green-400 truncate max-w-[320px]">TOPIC “{marketQuery.trim()}”</span>
                      )}
                      {categoryFilter && (
                        <span className="pixel-badge border-pixel-gray-light text-pixel-gray-light">{categoryFilter.toUpperCase()}</span>
                      )}
                      {bar.active && (
                        <span className="pixel-badge border-green-400/60 text-green-400">{bar.describe()}</span>
                      )}
                      {stratFilters && (
                        <span className="pixel-badge border-green-400/60 text-green-400">
                          STRAT{stratFilterName ? ` — ${stratFilterName.toUpperCase()}` : ""}
                        </span>
                      )}
                    </div>
                  ) : (
                    <div className="text-pixel-gray">NONE — SHOWING THIS TRADER&apos;S FULL {dayLabel} FLOW</div>
                  )}
                </div>
              </div>
            )}
          </div>

          {/* ── P&L tab extras: activity, extremes, per-market results ── */}
          {profileTab === "pnl" && (
            <>
            {/* Daily Activity */}
            {dailyActivity.length > 0 && (
              <DailyActivityChart data={dailyActivity} />
            )}

            {/* Biggest Wins/Losses */}
            <div className="grid grid-cols-2 gap-3">
              <div className="pixel-panel p-5 text-center">
                <div className="text-[15px] text-pixel-gray tracking-wider mb-2">BIGGEST WIN</div>
                <div className="text-base text-green-400">
                  {formatPnl(stats.biggestWin)}
                </div>
              </div>
              <div className="pixel-panel-red p-5 text-center">
                <div className="text-[15px] text-pixel-gray tracking-wider mb-2">BIGGEST LOSS</div>
                <div className="text-base text-red-400">
                  {formatPnl(stats.biggestLoss)}
                </div>
              </div>
            </div>

            {/* Closed/Realized Results in Window */}
            {closedInWindow.length > 0 && (
              <div className="pixel-panel overflow-hidden">
                <div className="px-5 py-4 border-b-2 border-pixel-border flex items-center justify-between">
                  <span className="text-[16px] text-pixel-gray-light tracking-wider">
                    {`${dayLabel} CLOSED RESULTS (PER MARKET)`}
                  </span>
                  <span className="text-[15px] text-pixel-gray">{closedInWindow.length} MARKETS</span>
                </div>
                <div className="overflow-x-auto">
                  <table className="pixel-table" style={{ tableLayout: "fixed", width: "100%", minWidth: "640px" }}>
                    <colgroup>
                      <col style={{ width: "44%" }} />
                      <col style={{ width: "12%" }} />
                      <col style={{ width: "12%" }} />
                      <col style={{ width: "10%" }} />
                      <col style={{ width: "22%" }} />
                    </colgroup>
                    <thead>
                      <tr>
                        <th>MARKET</th>
                        <th className="text-right">BOUGHT</th>
                        <th className="text-right">SOLD</th>
                        <th className="text-right">TRADES</th>
                        <th className="text-right">REALIZED</th>
                      </tr>
                    </thead>
                    <tbody>
                      {closedInWindow.map((m) => {
                        const isOpen = m.realized === 0;
                        const profit = !isOpen && m.realized > 0;
                        const pnlColor = isOpen
                          ? "text-pixel-gray-light"
                          : profit
                          ? "text-green-400"
                          : "text-red-400";
                        return (
                          <tr key={m.conditionId}>
                            <td
                              className={`truncate cursor-pointer hover:text-green-400 ${
                                m.market && tradeQuery === m.market ? "text-green-400" : "text-pixel-white"
                              }`}
                              title={`${m.market || m.conditionId} — click to filter to this market`}
                              onClick={() =>
                                m.market && setTradeQuery((q) => (q === m.market ? "" : m.market))
                              }
                            >
                              {m.market || m.conditionId.slice(0, 12)}
                            </td>
                            <td className="num text-right text-pixel-gray-light font-mono">
                              {m.bought.toFixed(0)}
                            </td>
                            <td className="num text-right text-pixel-gray-light font-mono">
                              {m.sold.toFixed(0)}
                            </td>
                            <td className="num text-right text-pixel-gray-light font-mono">
                              {m.tradeCount}
                            </td>
                            <td className={`num text-right font-mono ${pnlColor}`}>
                              {isOpen ? "—" : `${profit ? "+" : ""}$${m.realized.toFixed(2)}`}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
            </>
          )}

          {tradesInWindow.length === 0 && positions.length === 0 && (
            <div className="pixel-panel p-12 text-center">
              <div className="text-sm text-pixel-gray mb-2">{`NO ${dayLabel} DATA`}</div>
              <div className="text-[15px] text-pixel-gray-light">
                THIS TRADER HAS NO RECENT ACTIVITY
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
