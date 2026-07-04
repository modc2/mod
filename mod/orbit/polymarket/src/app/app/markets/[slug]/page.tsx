"use client";

import { useEffect, useState, useMemo } from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import {
  fetchMarketBySlug, fetchPriceHistory, PricePoint,
  fetchMarketTrades, bucketTradeVolume, MarketTrade,
  formatVolume,
} from "../../lib/polymarket";
import { PolymarketMarket } from "../../lib/types";
import TopBar from "../../components/TopBar";
import TradePanel from "../../components/TradePanel";
import { useTheme } from "../../context/ThemeContext";
import {
  AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid,
  Line, LineChart, BarChart, Bar,
} from "recharts";

// Visually-distinct colors for outcome lines. Multi-outcome markets (e.g.
// election candidates) can have many outcomes — these cycle for the rest.
// Index 0 (primary/Yes outcome) is theme-dependent — pure white reads great
// on the dark canvas but vanishes on a light background, so it's swapped
// per-render in the component below instead of hardcoded here.
const OUTCOME_COLORS_DARK = [
  "#ffffff", // primary outcome (Yes)
  "#f87171", // No / second
  "#4ade80",
  "#60a5fa",
  "#fbbf24",
  "#a78bfa",
  "#f472b6",
  "#34d399",
];
const OUTCOME_COLORS_LIGHT = ["#111827", ...OUTCOME_COLORS_DARK.slice(1)];

type IntervalKey = "1h" | "6h" | "1d" | "1w" | "1m" | "max";
const INTERVALS: { key: IntervalKey; label: string; fidelity: number }[] = [
  { key: "1h",  label: "1H",  fidelity: 1 },
  { key: "6h",  label: "6H",  fidelity: 5 },
  { key: "1d",  label: "1D",  fidelity: 15 },
  { key: "1w",  label: "1W",  fidelity: 60 },
  { key: "1m",  label: "1M",  fidelity: 240 },
  { key: "max", label: "ALL", fidelity: 1440 },
];

export default function MarketPage() {
  const params = useParams();
  const router = useRouter();
  const searchParams = useSearchParams();
  const { theme } = useTheme();
  const OUTCOME_COLORS = theme === "light" ? OUTCOME_COLORS_LIGHT : OUTCOME_COLORS_DARK;
  const slug = String(
    Array.isArray(params.slug) ? params.slug[0] : params.slug || "",
  );
  const sideParam = searchParams.get("side") || "";

  const [market, setMarket] = useState<PolymarketMarket | null>(null);
  const [marketLoading, setMarketLoading] = useState(true);
  const [marketError, setMarketError] = useState<string | null>(null);

  const [interval, setIntervalState] = useState<IntervalKey>("1w");
  const [series, setSeries] = useState<PricePoint[][]>([]);
  const [chartLoading, setChartLoading] = useState(true);
  const [marketTrades, setMarketTrades] = useState<MarketTrade[]>([]);

  // 1. Fetch the market metadata (outcomes, clobTokenIds, etc).
  useEffect(() => {
    if (!slug) return;
    let cancelled = false;
    setMarketLoading(true);
    setMarketError(null);
    fetchMarketBySlug(slug)
      .then((m) => {
        if (cancelled) return;
        if (!m) setMarketError("MARKET NOT FOUND");
        setMarket(m);
      })
      .catch((e) => {
        if (cancelled) return;
        setMarketError(e instanceof Error ? e.message : "LOAD FAILED");
      })
      .finally(() => {
        if (!cancelled) setMarketLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [slug]);

  // 2. When we know the tokenIds, fetch a price-history series for each
  //    outcome at the selected interval. Refetched whenever the user
  //    changes the time-window selector.
  useEffect(() => {
    const ids = market?.clobTokenIds;
    if (!ids || ids.length === 0) return;
    let cancelled = false;
    setChartLoading(true);
    const sel = INTERVALS.find((i) => i.key === interval) || INTERVALS[3];
    Promise.all(ids.map((tok) => fetchPriceHistory(tok, sel.key, sel.fidelity).catch(() => [])))
      .then((all) => {
        if (cancelled) return;
        setSeries(all);
      })
      .finally(() => {
        if (!cancelled) setChartLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [market, interval]);

  // 3. Fetch market-level trades for volume bars.
  useEffect(() => {
    const conditionId = market?.conditionId;
    if (!conditionId) return;
    let cancelled = false;
    fetchMarketTrades(conditionId)
      .then((trades) => {
        if (!cancelled) setMarketTrades(trades);
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [market]);

  // Bucket interval (seconds) matching chart resolution
  const BUCKET_MAP: Record<IntervalKey, number> = {
    "1h": 60, "6h": 300, "1d": 900,
    "1w": 3600, "1m": 14400, "max": 86400,
  };

  const volumeData = useMemo(() => {
    if (!marketTrades.length) return [];
    return bucketTradeVolume(marketTrades, BUCKET_MAP[interval]);
  }, [marketTrades, interval]);

  // Volume stats
  const volStats = useMemo(() => {
    let totalBuy = 0, totalSell = 0;
    for (const v of volumeData) { totalBuy += v.buyVol; totalSell += v.sellVol; }
    return { totalBuy, totalSell, total: totalBuy + totalSell };
  }, [volumeData]);

  // Merge per-outcome series into a single dataset keyed by timestamp so
  // recharts can draw multiple lines on a shared X axis. We use union of
  // all timestamps and forward-fill missing values within an outcome so
  // gaps don't drop a line down to 0.
  const chartData = useMemo(() => {
    if (!series.length) return [];
    const allTs = new Set<number>();
    for (const s of series) for (const pt of s) allTs.add(pt.t);
    const sorted = Array.from(allTs).sort((a, b) => a - b);
    const cursors = series.map(() => 0);
    const lastVal: (number | null)[] = series.map(() => null);
    return sorted.map((t) => {
      const row: Record<string, number | string | null> = { t };
      for (let i = 0; i < series.length; i++) {
        const s = series[i];
        while (cursors[i] < s.length && s[cursors[i]].t <= t) {
          lastVal[i] = s[cursors[i]].p;
          cursors[i]++;
        }
        row[`o${i}`] = lastVal[i];
      }
      return row;
    });
  }, [series]);

  const fmtX = (t: number) => {
    if (interval === "1h" || interval === "6h" || interval === "1d") {
      return new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    }
    return new Date(t * 1000).toLocaleDateString([], { month: "short", day: "numeric" });
  };

  return (
    <div className="max-w-[1920px] mx-auto">
      <TopBar searchPlaceholder="SEARCH MARKETS..." />
      <div className="p-4 space-y-4">
        {/* Back + title bar */}
        <div className="flex items-center gap-3">
          <button
            onClick={() => router.push("/markets")}
            className="pixel-btn border-pixel-border text-pixel-gray hover:text-pixel-white hover:border-pixel-white text-[15px] px-2.5 py-1.5 shrink-0"
          >
            ←
          </button>
          {market && (
            <div className="flex-1 min-w-0 text-pixel-white text-[16px] leading-relaxed truncate">
              {market.question}
            </div>
          )}
        </div>

        {marketLoading ? (
          <div className="pixel-panel p-12 text-center">
            <div className="text-sm text-pixel-white animate-pulse glow-green">
              LOADING MARKET...
            </div>
          </div>
        ) : marketError || !market ? (
          <div className="pixel-panel-red p-8 text-center">
            <div className="text-[16px] text-pixel-red">{marketError || "MARKET NOT FOUND"}</div>
          </div>
        ) : (
          <>
            {/* Stats bar */}
            <div className="pixel-panel px-4 py-3">
              <div className="flex items-center gap-5 flex-wrap text-[14px] font-mono">
                {/* Outcome odds */}
                {market.outcomes.map((outcome, i) => {
                  const px = market.outcomePrices[i] ?? 0;
                  const pct = Math.round(px * 100);
                  const color = OUTCOME_COLORS[i % OUTCOME_COLORS.length];
                  return (
                    <div key={outcome + i} className="flex items-center gap-2">
                      <div className="w-2.5 h-2.5 shrink-0" style={{ background: color }} />
                      <span className="text-pixel-gray-light">{outcome}</span>
                      <span className="text-pixel-white text-[15px]">{pct}¢</span>
                    </div>
                  );
                })}
                <div className="ml-auto flex items-center gap-5">
                  {market.category && (
                    <span className="pixel-badge border-pixel-border text-pixel-gray-light text-[13px]">
                      {market.category.toUpperCase()}
                    </span>
                  )}
                  <div className="flex items-center gap-1.5">
                    <span className="text-pixel-gray">VOL</span>
                    <span className="text-pixel-white">{formatVolume(market.volume)}</span>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <span className="text-pixel-gray">LIQ</span>
                    <span className="text-pixel-gray-light">{formatVolume(market.liquidity)}</span>
                  </div>
                  {market.endDate && (
                    <div className="flex items-center gap-1.5">
                      <span className="text-pixel-gray">ENDS</span>
                      <span className="text-pixel-gray-light">
                        {new Date(market.endDate).toLocaleDateString([], {
                          month: "short", day: "numeric",
                        })}
                      </span>
                    </div>
                  )}
                </div>
              </div>
            </div>

            {/* Main content: chart left, trade panel right */}
            <div className="flex gap-4 items-start flex-col lg:flex-row">
              {/* Chart */}
              <div className="pixel-panel p-4 flex-1 min-w-0">
                <div className="flex items-center justify-between mb-3 gap-2">
                  <div className="text-[15px] text-pixel-gray-light tracking-wider">
                    PRICE HISTORY
                  </div>
                  <div className="flex items-center gap-1">
                    {INTERVALS.map((iv) => (
                      <button
                        key={iv.key}
                        onClick={() => setIntervalState(iv.key)}
                        className={`pixel-btn text-[12px] px-2 py-0.5 ${
                          interval === iv.key
                            ? "border-pixel-green text-pixel-green bg-pixel-green/10"
                            : "border-pixel-border text-pixel-gray hover:text-pixel-white"
                        }`}
                      >
                        {iv.label}
                      </button>
                    ))}
                  </div>
                </div>

                {chartLoading ? (
                  <div className="h-[280px] flex items-center justify-center">
                    <div className="text-[14px] text-pixel-gray animate-pulse">
                      LOADING PRICE HISTORY...
                    </div>
                  </div>
                ) : chartData.length === 0 ? (
                  <div className="h-[280px] flex items-center justify-center">
                    <div className="text-[14px] text-pixel-gray">NO PRICE DATA</div>
                  </div>
                ) : (
                  <ResponsiveContainer width="100%" height={280}>
                    {market.outcomes.length === 2 ? (
                      <AreaChart data={chartData} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
                        <defs>
                          <linearGradient id="yesGrad" x1="0" y1="0" x2="0" y2="1">
                            <stop offset="0%" stopColor={OUTCOME_COLORS[0]} stopOpacity={0.25} />
                            <stop offset="100%" stopColor={OUTCOME_COLORS[0]} stopOpacity={0} />
                          </linearGradient>
                        </defs>
                        <CartesianGrid stroke="#222" vertical={false} />
                        <XAxis
                          dataKey="t"
                          type="number"
                          domain={["dataMin", "dataMax"]}
                          scale="time"
                          tick={{ fontSize: 9, fill: "#666" }}
                          axisLine={{ stroke: "#333" }}
                          tickLine={false}
                          tickFormatter={fmtX}
                          minTickGap={40}
                        />
                        <YAxis
                          domain={[0, 1]}
                          tick={{ fontSize: 9, fill: "#666" }}
                          axisLine={{ stroke: "#333" }}
                          tickLine={false}
                          tickFormatter={(v: number) => `${Math.round(v * 100)}¢`}
                        />
                        <Tooltip
                          contentStyle={{
                            background: "var(--panel-from)",
                            border: "1px solid var(--border-strong)",
                            fontSize: 10,
                            color: "var(--fg)",
                            fontFamily: "'JetBrains Mono', monospace",
                          }}
                          labelFormatter={(t: number) =>
                            new Date(t * 1000).toLocaleString([], {
                              month: "short", day: "numeric",
                              hour: "2-digit", minute: "2-digit",
                            })
                          }
                          formatter={(v: number, name: string) => [
                            `${Math.round(v * 100)}¢`,
                            market.outcomes[Number(name.replace("o", ""))] || name,
                          ]}
                        />
                        <Area
                          type="monotone"
                          dataKey="o0"
                          stroke={OUTCOME_COLORS[0]}
                          fill="url(#yesGrad)"
                          strokeWidth={2}
                          isAnimationActive={false}
                          connectNulls
                        />
                      </AreaChart>
                    ) : (
                      <LineChart data={chartData} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
                        <CartesianGrid stroke="#222" vertical={false} />
                        <XAxis
                          dataKey="t"
                          type="number"
                          domain={["dataMin", "dataMax"]}
                          scale="time"
                          tick={{ fontSize: 9, fill: "#666" }}
                          axisLine={{ stroke: "#333" }}
                          tickLine={false}
                          tickFormatter={fmtX}
                          minTickGap={40}
                        />
                        <YAxis
                          domain={[0, 1]}
                          tick={{ fontSize: 9, fill: "#666" }}
                          axisLine={{ stroke: "#333" }}
                          tickLine={false}
                          tickFormatter={(v: number) => `${Math.round(v * 100)}¢`}
                        />
                        <Tooltip
                          contentStyle={{
                            background: "var(--panel-from)",
                            border: "1px solid var(--border-strong)",
                            fontSize: 10,
                            color: "var(--fg)",
                            fontFamily: "'JetBrains Mono', monospace",
                          }}
                          labelFormatter={(t: number) =>
                            new Date(t * 1000).toLocaleString([], {
                              month: "short", day: "numeric",
                              hour: "2-digit", minute: "2-digit",
                            })
                          }
                          formatter={(v: number, name: string) => [
                            `${Math.round(v * 100)}¢`,
                            market.outcomes[Number(name.replace("o", ""))] || name,
                          ]}
                        />
                        {market.outcomes.map((_, i) => (
                          <Line
                            key={i}
                            type="monotone"
                            dataKey={`o${i}`}
                            stroke={OUTCOME_COLORS[i % OUTCOME_COLORS.length]}
                            strokeWidth={2}
                            dot={false}
                            isAnimationActive={false}
                            connectNulls
                          />
                        ))}
                      </LineChart>
                    )}
                  </ResponsiveContainer>
                )}

                {/* Outcome legend */}
                <div className="mt-3 flex items-center gap-4 text-[14px] text-pixel-gray flex-wrap">
                  {market.outcomes.map((outcome, i) => (
                    <div key={outcome + i} className="flex items-center gap-1.5">
                      <div
                        className="w-2 h-2"
                        style={{ background: OUTCOME_COLORS[i % OUTCOME_COLORS.length] }}
                      />
                      <span>{outcome}</span>
                    </div>
                  ))}
                </div>

                {/* Volume bars */}
                {volumeData.length > 0 && (
                  <>
                    <div className="mt-4 border-t border-pixel-border pt-3">
                      <div className="flex items-center justify-between mb-2">
                        <div className="text-[14px] text-pixel-gray-light tracking-wider">VOLUME</div>
                        <div className="flex items-center gap-4 text-[13px] font-mono">
                          <div className="flex items-center gap-1.5">
                            <div className="w-2 h-2 bg-green-500" />
                            <span className="text-green-400">BUY {formatVolume(volStats.totalBuy)}</span>
                          </div>
                          <div className="flex items-center gap-1.5">
                            <div className="w-2 h-2 bg-red-500" />
                            <span className="text-red-400">SELL {formatVolume(volStats.totalSell)}</span>
                          </div>
                          <span className="text-pixel-gray">
                            {volStats.totalBuy + volStats.totalSell > 0
                              ? `${Math.round((volStats.totalBuy / (volStats.totalBuy + volStats.totalSell)) * 100)}% BUY`
                              : ""}
                          </span>
                        </div>
                      </div>
                      <ResponsiveContainer width="100%" height={80}>
                        <BarChart
                          data={volumeData}
                          margin={{ top: 2, right: 12, left: 0, bottom: 0 }}
                        >
                          <XAxis
                            dataKey="t"
                            type="number"
                            domain={["dataMin", "dataMax"]}
                            scale="time"
                            tick={{ fontSize: 8, fill: "#555" }}
                            axisLine={{ stroke: "#333" }}
                            tickLine={false}
                            tickFormatter={fmtX}
                            minTickGap={40}
                          />
                          <YAxis
                            tick={{ fontSize: 8, fill: "#555" }}
                            axisLine={false}
                            tickLine={false}
                            tickFormatter={(v: number) => formatVolume(v)}
                            width={40}
                          />
                          <Tooltip
                            contentStyle={{
                              background: "var(--panel-from)",
                              border: "1px solid var(--border-strong)",
                              fontSize: 9,
                              color: "var(--fg)",
                              fontFamily: "'JetBrains Mono', monospace",
                            }}
                            labelFormatter={(t: number) =>
                              new Date(t * 1000).toLocaleString([], {
                                month: "short", day: "numeric",
                                hour: "2-digit", minute: "2-digit",
                              })
                            }
                            formatter={(v: number, name: string) => [
                              formatVolume(v),
                              name === "buyVol" ? "BUY" : "SELL",
                            ]}
                          />
                          <Bar dataKey="buyVol" stackId="vol" fill="#22c55e" isAnimationActive={false} />
                          <Bar dataKey="sellVol" stackId="vol" fill="#ef4444" isAnimationActive={false} />
                        </BarChart>
                      </ResponsiveContainer>
                    </div>
                  </>
                )}
              </div>

              {/* Trade panel — sidebar on large screens */}
              <div className="w-full lg:w-[320px] shrink-0">
                <TradePanel market={market} initialSide={sideParam === "no" ? "NO" : sideParam === "yes" ? "YES" : undefined} />
              </div>
            </div>

            {/* Description */}
            {market.description && (
              <div className="pixel-panel p-4">
                <div className="text-[15px] text-pixel-gray-light tracking-wider mb-3">
                  DESCRIPTION
                </div>
                <div className="text-[15px] text-pixel-gray leading-[1.8] whitespace-pre-wrap">
                  {market.description}
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
