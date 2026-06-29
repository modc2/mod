"use client";

import { Suspense, useState, useEffect, useCallback } from "react";
import TopBar from "../components/TopBar";
import CopyTrading from "../components/CopyTrading";
import PnlChart from "../components/PnlChart";
import type { CurvePoint } from "../components/PnlChart";
import { useFilters, useUrlSync } from "../context/FiltersContext";
import { loadIndexes, getActiveIndexId, saveIndex, updateIndex } from "../lib/indexStore";
import { fetchWalletTrades, fetchPositions } from "../lib/polymarket";
import { computeFifoTrades, buildTradeByTradeCombinedCurve, buildPnlCurve } from "../lib/pnlEngine";
import type { SavedIndex, IndexTrader } from "../lib/types";

function getOrCreateActiveIndex(): SavedIndex {
  const indexes = loadIndexes();
  const activeId = getActiveIndexId();
  const active = activeId ? indexes.find((i) => i.id === activeId) : indexes[0];
  if (active) return active;
  const now = Date.now();
  const newIdx: SavedIndex = {
    id: `idx_${now}`,
    name: "Strategy 1",
    traders: [],
    createdAt: now,
    updatedAt: now,
  };
  saveIndex(newIdx);
  return newIdx;
}

const TRADES_PAGE_SIZE = 50;

function TradersInner() {
  useUrlSync();
  const { search, category, marketQuery, daysAgo, minPerDay, reloadKey } = useFilters();
  const days = Number(daysAgo) > 0 ? Number(daysAgo) : 7;
  const minTradesPerDay =
    minPerDay !== "" && Number.isFinite(Number(minPerDay))
      ? Math.max(0, Number(minPerDay))
      : 0;

  const [selectedAddresses, setSelectedAddresses] = useState<string[]>([]);

  // Backtest state
  const [activeTab, setActiveTab] = useState<string>("STRATEGY");
  const [pnlCurve, setPnlCurve] = useState<CurvePoint[]>([]);
  const [loadingChart, setLoadingChart] = useState(false);
  const [allTrades, setAllTrades] = useState<any[]>([]);
  const [tradesPage, setTradesPage] = useState(0);
  const [hoveredTradeIndex, setHoveredTradeIndex] = useState<number | null>(null);
  const [traderData, setTraderData] = useState<Map<string, {
    pnlCurve: CurvePoint[];
    trades: any[];
    weight: number;
  }>>(new Map());
  const [showBacktest, setShowBacktest] = useState(false);

  // Load current strategy traders on mount
  useEffect(() => {
    const refresh = () => {
      const idx = getOrCreateActiveIndex();
      setSelectedAddresses(idx.traders.map((t) => t.address));
    };
    refresh();
    window.addEventListener("strat-updated", refresh);
    return () => window.removeEventListener("strat-updated", refresh);
  }, []);

  const handleToggle = useCallback((address: string) => {
    const addr = address.toLowerCase();
    const idx = getOrCreateActiveIndex();
    const exists = idx.traders.some((t) => t.address.toLowerCase() === addr);

    let newTraders: IndexTrader[];
    if (exists) {
      newTraders = idx.traders.filter((t) => t.address.toLowerCase() !== addr);
    } else {
      const count = idx.traders.length + 1;
      const weight = 1 / count;
      newTraders = idx.traders.map((t) => ({ ...t, weight }));
      newTraders.push({ address: addr, weight, enabled: true });
    }

    updateIndex(idx.id, { traders: newTraders, updatedAt: Date.now() });
    setSelectedAddresses(newTraders.map((t) => t.address));
    window.dispatchEvent(new Event("strat-updated"));
  }, []);

  // Load backtest data when selected addresses change and backtest is visible
  useEffect(() => {
    if (!showBacktest || selectedAddresses.length === 0) {
      setPnlCurve([]);
      setAllTrades([]);
      setTraderData(new Map());
      return;
    }

    const idx = getOrCreateActiveIndex();
    const loadAllData = async () => {
      setLoadingChart(true);
      try {
        const cutoffMs = Date.now() - days * 24 * 60 * 60 * 1000;
        const rawTraderData: {
          address: string; trades: any[]; positions: any[]; weight: number; traderAddress: string;
        }[] = [];

        for (const trader of idx.traders) {
          try {
            const [trades, positions] = await Promise.all([
              fetchWalletTrades(trader.address, 500),
              fetchPositions(trader.address),
            ]);
            rawTraderData.push({
              address: trader.address, traderAddress: trader.address,
              trades: computeFifoTrades(trades, positions),
              positions, weight: trader.weight,
            });
          } catch (err) {
            console.error(`Failed to load data for ${trader.address}:`, err);
          }
        }

        // Combined strategy curve
        setPnlCurve(buildTradeByTradeCombinedCurve(rawTraderData, cutoffMs));

        // Merge all trades
        const mergedTrades: any[] = [];
        for (const td of rawTraderData) {
          const currentPrices = new Map<string, number>();
          for (const pos of td.positions) {
            const key = pos.conditionId || pos.market;
            if (key && pos.currentPrice > 0) currentPrices.set(key, pos.currentPrice);
          }
          for (const trade of td.trades) {
            if (trade.timestamp >= cutoffMs) {
              const key = trade.conditionId || trade.market;
              const currentPrice = currentPrices.get(key) || trade.price;
              mergedTrades.push({
                ...trade, traderAddress: td.address, weight: td.weight,
                currentPrice, unrealized: trade.side === "BUY" ? (currentPrice - trade.price) * trade.size : 0,
              });
            }
          }
        }
        mergedTrades.sort((a, b) => a.timestamp - b.timestamp);
        setAllTrades(mergedTrades);

        // Individual trader data
        const traderMap = new Map<string, { pnlCurve: CurvePoint[]; trades: any[]; weight: number }>();
        for (const td of rawTraderData) {
          const curve = buildPnlCurve(td.trades, td.positions, cutoffMs);
          const currentPrices = new Map<string, number>();
          for (const pos of td.positions) {
            const key = pos.conditionId || pos.market;
            if (key && pos.currentPrice > 0) currentPrices.set(key, pos.currentPrice);
          }
          const traderTrades: any[] = [];
          for (const trade of td.trades) {
            if (trade.timestamp >= cutoffMs) {
              const key = trade.conditionId || trade.market;
              const currentPrice = currentPrices.get(key) || trade.price;
              traderTrades.push({
                ...trade, traderAddress: td.address, weight: td.weight,
                currentPrice, unrealized: trade.side === "BUY" ? (currentPrice - trade.price) * trade.size : 0,
              });
            }
          }
          traderTrades.sort((a, b) => a.timestamp - b.timestamp);
          traderMap.set(td.address, { pnlCurve: curve, trades: traderTrades, weight: td.weight });
        }
        setTraderData(traderMap);
      } catch (err) {
        console.error("Failed to load backtest:", err);
      } finally {
        setLoadingChart(false);
      }
    };

    loadAllData();
  }, [showBacktest, selectedAddresses, days]);

  // Current tab data
  const isStrategyTab = activeTab === "STRATEGY";
  const currentTraderData = isStrategyTab ? null : traderData.get(activeTab);
  const currentPnlCurve = isStrategyTab ? pnlCurve : (currentTraderData?.pnlCurve || []);
  const currentTrades = isStrategyTab ? allTrades : (currentTraderData?.trades || []);

  return (
    <div className="max-w-[1920px] mx-auto">
      <TopBar searchPlaceholder="SEARCH TRADERS..." />
      <div className="p-4 space-y-4">
        <CopyTrading
          days={days}
          minTradesPerDay={minTradesPerDay}
          reloadKey={reloadKey}
          search={search}
          category={category}
          marketQuery={marketQuery}
          onSelect={handleToggle}
          selectedAddresses={selectedAddresses}
        />

        {/* Strategy Backtest Section */}
        {selectedAddresses.length > 0 && (
          <div className="space-y-3">
            {/* Toggle backtest */}
            <div className="flex items-center gap-3">
              <button
                onClick={() => setShowBacktest((v) => !v)}
                className={`pixel-btn text-[14px] px-4 py-1.5 ${
                  showBacktest
                    ? "border-green-400 text-green-400 bg-green-400/10"
                    : "border-pixel-border text-pixel-gray hover:text-pixel-white"
                }`}
              >
                {showBacktest ? "HIDE BACKTEST" : "SHOW BACKTEST"} ({selectedAddresses.length} TRADERS)
              </button>
            </div>

            {showBacktest && (
              <div className="space-y-3">
                {/* Trader tabs */}
                <div className="flex items-center gap-2 overflow-x-auto pb-1">
                  <button
                    onClick={() => { setActiveTab("STRATEGY"); setTradesPage(0); }}
                    className={`pixel-btn text-[14px] px-4 py-1.5 whitespace-nowrap ${
                      isStrategyTab ? "border-pixel-white text-pixel-white" : "border-pixel-gray text-pixel-gray hover:text-pixel-white"
                    }`}
                  >
                    STRATEGY
                  </button>
                  {selectedAddresses.map((addr) => (
                    <button
                      key={addr}
                      onClick={() => { setActiveTab(addr); setTradesPage(0); }}
                      className={`pixel-btn text-[14px] px-4 py-1.5 whitespace-nowrap ${
                        activeTab === addr ? "border-pixel-white text-pixel-white" : "border-pixel-gray text-pixel-gray hover:text-pixel-white"
                      }`}
                    >
                      {addr.slice(0, 6)}...{addr.slice(-4)}
                    </button>
                  ))}
                </div>

                {/* Trader header */}
                {!isStrategyTab && currentTraderData && (
                  <div className="pixel-panel p-3">
                    <div className="flex items-center justify-between">
                      <div className="text-[15px] text-pixel-gray-light tracking-wider">TRADER BACKTEST</div>
                      <div className="flex items-center gap-3 text-[14px]">
                        <span className="font-mono text-pixel-white">{activeTab.slice(0, 6)}...{activeTab.slice(-4)}</span>
                        <span className="text-pixel-gray">WEIGHT {(currentTraderData.weight * 100).toFixed(0)}%</span>
                      </div>
                    </div>
                  </div>
                )}

                {/* Chart + Table */}
                {loadingChart ? (
                  <div className="pixel-panel p-8 text-center">
                    <div className="text-[16px] text-pixel-white animate-pulse mb-2">
                      LOADING {isStrategyTab ? "STRATEGY" : "TRADER"} DATA...
                    </div>
                    <div className="text-[14px] text-pixel-gray">
                      Fetching trades from {selectedAddresses.length} trader{selectedAddresses.length !== 1 ? "s" : ""}
                    </div>
                  </div>
                ) : currentPnlCurve.length > 0 ? (
                  <>
                    <PnlChart
                      points={currentPnlCurve}
                      dayLabel={`${days}D`}
                      tradesInWindow={[]}
                      highlightIndex={hoveredTradeIndex}
                      onHoverChange={setHoveredTradeIndex}
                    />

                    <div className="pixel-panel overflow-hidden">
                      <div className="overflow-x-auto">
                        <table className="pixel-table" style={{ minWidth: isStrategyTab ? "1080px" : "980px", tableLayout: "fixed" }}>
                          <colgroup>
                            <col style={{ width: "120px" }} />
                            {isStrategyTab && <col style={{ width: "100px" }} />}
                            <col style={{ width: "26%" }} />
                            <col style={{ width: "50px" }} />
                            <col style={{ width: "80px" }} />
                            <col style={{ width: "80px" }} />
                            <col style={{ width: "70px" }} />
                            <col style={{ width: "70px" }} />
                            <col style={{ width: "90px" }} />
                            <col style={{ width: "90px" }} />
                          </colgroup>
                          <thead>
                            <tr>
                              <th>TIME</th>
                              {isStrategyTab && <th>TRADER</th>}
                              <th>MARKET</th>
                              <th>SIDE</th>
                              <th className="text-right">PRICE</th>
                              <th className="text-right">SIZE</th>
                              <th className="text-right">ENTRY</th>
                              <th className="text-right">FEE</th>
                              <th className="text-right">CURRENT</th>
                              <th className="text-right">P&L</th>
                            </tr>
                          </thead>
                          <tbody>
                            {currentTrades.slice(tradesPage * TRADES_PAGE_SIZE, (tradesPage + 1) * TRADES_PAGE_SIZE).map((trade, i) => {
                              const isEntering = trade.side === "BUY";
                              const showRealized = !isEntering && trade.hasBasis;
                              const isProfit = showRealized && trade.realized > 0;
                              const sideColor = isEntering
                                ? "border-pixel-gray-light text-pixel-gray-light"
                                : !showRealized ? "border-pixel-gray text-pixel-gray"
                                : isProfit ? "border-green-400 text-green-400" : "border-red-400 text-red-400";
                              const pnlColor = !showRealized ? "text-pixel-gray-light" : isProfit ? "text-green-400" : "text-red-400";
                              const hasBuyInfo = !isEntering && trade.buyPrice !== undefined;
                              const timeStr = new Date(trade.timestamp).toLocaleString([], {
                                month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
                              });
                              const curveIndex = currentPnlCurve.findIndex(p => p.ts === trade.timestamp && p.side === trade.side);
                              const isHovered = hoveredTradeIndex === curveIndex;

                              return (
                                <tr
                                  key={`${trade.id}-${i}`}
                                  className={`${isHovered ? "bg-pixel-white/5" : ""} transition-colors`}
                                  onMouseEnter={() => curveIndex >= 0 && setHoveredTradeIndex(curveIndex)}
                                  onMouseLeave={() => setHoveredTradeIndex(null)}
                                >
                                  <td className="text-pixel-gray-light font-mono text-[13px]">{timeStr}</td>
                                  {isStrategyTab && (
                                    <td className="text-pixel-white font-mono text-[13px]">
                                      {trade.traderAddress.slice(0, 6)}...{trade.traderAddress.slice(-4)}
                                    </td>
                                  )}
                                  <td className="text-pixel-white truncate text-[13px]" title={trade.market}>{trade.market}</td>
                                  <td><span className={`pixel-badge ${sideColor}`}>{trade.side}</span></td>
                                  <td className="num text-right text-pixel-white font-mono">{Math.round(trade.price * 100)}c</td>
                                  <td className="num text-right text-pixel-white font-mono">${(trade.size * trade.price).toFixed(2)}</td>
                                  <td className="num text-right font-mono text-pixel-gray-light">
                                    {hasBuyInfo ? `${Math.round(trade.buyPrice * 100)}c` : isEntering ? "" : "\u2014"}
                                  </td>
                                  <td className="num text-right font-mono text-red-400">${(trade.fee || 0).toFixed(2)}</td>
                                  <td className={`num text-right font-mono ${
                                    isEntering ? (trade.unrealized > 0 ? "text-green-400" : trade.unrealized < 0 ? "text-red-400" : "text-pixel-gray") : "text-pixel-gray-light"
                                  }`}>
                                    {isEntering ? `${trade.unrealized >= 0 ? "+" : ""}$${trade.unrealized.toFixed(2)}` : "\u2014"}
                                  </td>
                                  <td className={`num text-right font-mono ${pnlColor}`}>
                                    {isEntering ? "" : !showRealized ? "\u2014" : `${trade.realized >= 0 ? "+" : ""}$${trade.realized.toFixed(2)}`}
                                  </td>
                                </tr>
                              );
                            })}
                          </tbody>
                        </table>
                      </div>
                    </div>

                    {currentTrades.length > TRADES_PAGE_SIZE && (
                      <div className="flex items-center justify-between px-1">
                        <span className="text-[12px] text-pixel-gray font-mono">
                          {tradesPage * TRADES_PAGE_SIZE + 1}-{Math.min((tradesPage + 1) * TRADES_PAGE_SIZE, currentTrades.length)} of {currentTrades.length}
                        </span>
                        <div className="flex items-center gap-1">
                          <button
                            onClick={() => setTradesPage((p) => Math.max(0, p - 1))}
                            disabled={tradesPage === 0}
                            className="pixel-btn text-[12px] px-2 py-0.5 border-pixel-border text-pixel-gray hover:text-pixel-white disabled:opacity-20 disabled:cursor-not-allowed"
                          >PREV</button>
                          <span className="text-[12px] text-pixel-gray font-mono">
                            PAGE {tradesPage + 1} / {Math.ceil(currentTrades.length / TRADES_PAGE_SIZE)}
                          </span>
                          <button
                            onClick={() => setTradesPage((p) => Math.min(Math.ceil(currentTrades.length / TRADES_PAGE_SIZE) - 1, p + 1))}
                            disabled={tradesPage >= Math.ceil(currentTrades.length / TRADES_PAGE_SIZE) - 1}
                            className="pixel-btn text-[12px] px-2 py-0.5 border-pixel-border text-pixel-gray hover:text-pixel-white disabled:opacity-20 disabled:cursor-not-allowed"
                          >NEXT</button>
                        </div>
                      </div>
                    )}
                  </>
                ) : (
                  <div className="pixel-panel p-8 text-center">
                    <div className="text-[16px] text-pixel-gray-light tracking-wider mb-2">NO DATA</div>
                    <div className="text-[15px] text-pixel-gray">
                      {isStrategyTab ? "NO STRATEGY DATA AVAILABLE" : "NO TRADER DATA AVAILABLE"}
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

export default function TradersPage() {
  return (
    <Suspense>
      <TradersInner />
    </Suspense>
  );
}
