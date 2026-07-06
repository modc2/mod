"use client";

import { useMemo, useState } from "react";
import { TopTrader, formatVolume, formatPnl, timeAgo, matchMarketCategory, CategorySlug } from "../lib/polymarket";
import { shortAddress } from "@/lib/auth";
import { PolymarketTrade, PolymarketPosition } from "../lib/types";
import PnlChart from "./PnlChart";
import type { CurvePoint } from "./PnlChart";
// No recharts — pure SVG charts for reliability with any version.

interface Props {
  trader: TopTrader;
  trades: PolymarketTrade[];
  positions: PolymarketPosition[];
  loading: boolean;
  watching: boolean;
  onToggleWatch: () => void;
  onBack: () => void;
  days?: number;
  searchFilter?: string;
  categoryFilter?: CategorySlug;
}

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
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ height: "auto", maxHeight: 160 }}>
        {/* Grid */}
        {yTicks.map((v, i) => (
          <g key={i}>
            <line x1={pad.left} y1={toY(v)} x2={W - pad.right} y2={toY(v)} stroke="#222" strokeWidth={1} />
            <text x={pad.left - 6} y={toY(v) + 3} textAnchor="end" fill="#666" fontSize={9} fontFamily="'IBM Plex Mono', monospace">{v}</text>
          </g>
        ))}
        {/* Bars */}
        {data.map((d, i) => {
          const cx = pad.left + gap * i + gap / 2;
          const buyH = (d.buys / yMax) * ch;
          const sellH = (d.sells / yMax) * ch;
          return (
            <g key={i}>
              {/* Buys (white, bottom) */}
              <rect x={cx - barW / 2} y={toY(d.buys + d.sells)} width={barW} height={buyH} fill="#fff" />
              {/* Sells (gray, stacked on top) */}
              <rect x={cx - barW / 2} y={toY(d.buys + d.sells)} width={barW} height={sellH} fill="#666" />
              {/* X label */}
              <text x={cx} y={H - 8} textAnchor="middle" fill="#666" fontSize={8} fontFamily="'IBM Plex Mono', monospace">{d.date}</text>
            </g>
          );
        })}
        {/* Axes */}
        <line x1={pad.left} y1={pad.top} x2={pad.left} y2={H - pad.bottom} stroke="#444" strokeWidth={1} />
        <line x1={pad.left} y1={H - pad.bottom} x2={W - pad.right} y2={H - pad.bottom} stroke="#444" strokeWidth={1} />
      </svg>
      <div className="flex items-center gap-4 mt-1 text-[13px] text-pixel-gray">
        <div className="flex items-center gap-1"><div className="w-2 h-2 bg-white" /> BUYS</div>
        <div className="flex items-center gap-1"><div className="w-2 h-2 bg-pixel-gray" /> SELLS</div>
      </div>
    </div>
  );
}

export default function TraderProfile({
  trader,
  trades,
  positions,
  loading,
  watching,
  onToggleWatch,
  onBack,
  days = 30,
  searchFilter = "",
  categoryFilter = "",
}: Props) {
  const dayLabel = `${days}D`;
  const [posSort, setPosSort] = useState<PosSort>("pnlUsd");
  const [posSortDir, setPosSortDir] = useState<SortDir>("desc");
  const [tradeSort, setTradeSort] = useState<TradeSort>("timestamp");
  const [tradeSortDir, setTradeSortDir] = useState<SortDir>("desc");
  const [showCurrent, setShowCurrent] = useState(false);
  type ProfileTab = "chart" | "open" | "closed" | "all";
  const [profileTab, setProfileTab] = useState<ProfileTab>("chart");

  const handlePosSort = (col: PosSort) => {
    if (posSort === col) setPosSortDir((d) => (d === "desc" ? "asc" : "desc"));
    else { setPosSort(col); setPosSortDir("desc"); }
  };

  const handleTradeSort = (col: TradeSort) => {
    if (tradeSort === col) setTradeSortDir((d) => (d === "desc" ? "asc" : "desc"));
    else { setTradeSort(col); setTradeSortDir("desc"); }
  };

  const cutoffMs = useMemo(() => Date.now() - days * 24 * 60 * 60 * 1000, [days]);

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
    if (!q && !categoryFilter) return tradesInWindow;
    return tradesInWindow.filter((t) => {
      if (q && !t.market.toLowerCase().includes(q)) return false;
      if (categoryFilter && !matchMarketCategory(t.market, categoryFilter)) return false;
      return true;
    });
  }, [tradesInWindow, searchFilter, categoryFilter]);

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
    const winRate = wins + losses > 0 ? Math.round((wins / (wins + losses)) * 100) : -1;
    return { wins, losses, totalPnl, avgTrade, biggestWin, biggestLoss, winRate };
  }, [filteredTrades]);

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
    if (!q && !categoryFilter) return positions;
    return positions.filter((p) => {
      if (q && !p.market.toLowerCase().includes(q)) return false;
      if (categoryFilter && !matchMarketCategory(p.market, categoryFilter)) return false;
      return true;
    });
  }, [positions, searchFilter, categoryFilter]);

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

  // Trades for the active tab
  const tabTrades = useMemo(() => {
    if (profileTab === "open") return openTrades;
    if (profileTab === "closed") return closedTrades;
    return filteredTrades; // "all"
  }, [profileTab, openTrades, closedTrades, filteredTrades]);

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
    <div className="space-y-4">
      {/* Back + Header */}
      <div className="flex items-center gap-3">
        <button
          onClick={onBack}
          className="pixel-btn border-pixel-border text-pixel-gray hover:text-pixel-white hover:border-pixel-white text-[16px]"
        >
          BACK
        </button>
        <div className="flex-1">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 border-2 border-pixel-white flex items-center justify-center">
              <div className="w-4 h-4 bg-pixel-white" />
            </div>
            <span className="text-sm text-pixel-white glow-green font-mono">
              {shortAddress(trader.address)}
            </span>
            <button
              onClick={() => navigator.clipboard.writeText(trader.address)}
              className="text-[15px] text-pixel-gray hover:text-pixel-white"
            >
              [COPY]
            </button>
          </div>
        </div>
        <button
          onClick={onToggleWatch}
          className={`pixel-btn text-[16px] ${
            watching
              ? "border-pixel-white text-pixel-white bg-pixel-white/10"
              : "border-pixel-border text-pixel-gray hover:border-pixel-white hover:text-pixel-white"
          }`}
        >
          {watching ? "WATCHING" : "WATCH"}
        </button>
      </div>

      {loading ? (
        <div className="pixel-panel p-12 text-center">
          <div className="text-sm text-pixel-white animate-pulse glow-green">
            {`LOADING ${dayLabel} HISTORY...`}
          </div>
        </div>
      ) : (
        <>
          {/* Stats Grid */}
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
            {([
              { label: `${dayLabel} P&L`, value: formatPnl(stats.totalPnl), tone: stats.totalPnl > 0 ? "good" : stats.totalPnl < 0 ? "bad" : "neutral" },
              { label: "WIN RATE", value: stats.winRate < 0 ? "—" : `${stats.winRate}%`, tone: stats.winRate < 0 ? "neutral" : stats.winRate >= 50 ? "good" : "bad" },
              { label: "TRADES", value: (searchFilter.trim() || categoryFilter) ? `${filteredTrades.length}/${tradesInWindow.length}` : filteredTrades.length.toString(), tone: "neutral" },
              { label: "VOLUME", value: formatVolume(filteredTrades.reduce((s, t) => s + t.size * t.price, 0)), tone: "neutral" },
              { label: "AVG TRADE", value: formatPnl(stats.avgTrade), tone: stats.avgTrade > 0 ? "good" : stats.avgTrade < 0 ? "bad" : "neutral" },
              { label: "POSITIONS", value: (searchFilter.trim() || categoryFilter) ? `${filteredPositions.length}/${positions.length}` : positions.length.toString(), tone: "neutral" },
            ] as const).map((stat) => {
              const valueClass =
                stat.tone === "good" ? "text-green-400" :
                stat.tone === "bad" ? "text-red-400" :
                "text-pixel-white glow-green";
              return (
                <div key={stat.label} className="pixel-panel p-4 text-center">
                  <div className="text-[14px] text-pixel-gray tracking-wider mb-2">
                    {stat.label}
                  </div>
                  <div className={`text-sm ${valueClass}`}>
                    {stat.value}
                  </div>
                </div>
              );
            })}
          </div>

          {/* ── Tabbed: Chart / Open / Closed / All ── */}
          <div className="pixel-panel overflow-hidden">
            {/* Tab bar */}
            <div className="flex items-center border-b-2 border-pixel-border">
              {([
                { id: "chart" as ProfileTab, label: "P&L", count: pnlCurve.length },
                { id: "open" as ProfileTab, label: "OPEN", count: openTrades.length },
                { id: "closed" as ProfileTab, label: "CLOSED", count: closedTrades.length },
                { id: "all" as ProfileTab, label: "ALL", count: filteredTrades.length },
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
                  <span className="ml-1.5 text-[13px] text-pixel-gray">{tab.count}</span>
                </button>
              ))}
            </div>

            {/* Tab content */}
            {profileTab === "chart" ? (
              <div className="p-0">
                {pnlCurve.length > 0 ? (
                  <PnlChart points={pnlCurve} dayLabel={dayLabel} tradesInWindow={filteredTrades} filtered={!!(searchFilter.trim() || categoryFilter)} />
                ) : (
                  <div className="p-8 text-center">
                    <div className="text-[16px] text-pixel-gray-light tracking-wider mb-2">
                      {`${dayLabel} P&L CURVE`}
                    </div>
                    <div className="text-[15px] text-pixel-gray">
                      {(searchFilter.trim() || categoryFilter)
                        ? "NO MATCHING TRADES — TRY A DIFFERENT FILTER"
                        : positions.length > 0
                        ? "NO TRADES IN WINDOW — CHECK POSITIONS TAB"
                        : "NO TRADE DATA"}
                    </div>
                  </div>
                )}
              </div>
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
                            <td className="text-pixel-white truncate" title={trade.market}>
                              {trade.market}
                            </td>
                            <td>
                              <span className={`pixel-badge ${sideColor}`}>
                                {trade.side}
                              </span>
                            </td>
                            <td className="num text-right text-pixel-white font-mono">
                              {Math.round(trade.price * 100)}c
                            </td>
                            <td className="num text-right text-pixel-white font-mono">
                              ${(trade.size * trade.price).toFixed(2)}
                            </td>
                            <td className="num text-right font-mono text-pixel-gray-light">
                              {hasBuyInfo ? `${Math.round(trade.buyPrice! * 100)}c` : isEntering ? "" : "\u2014"}
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
                    {profileTab === "open" ? "NO OPEN TRADES" : profileTab === "closed" ? "NO CLOSED TRADES" : "NO TRADES"}
                  </div>
                </div>
              )
            )}
          </div>

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
                          <td className="text-pixel-white truncate" title={m.market}>
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
