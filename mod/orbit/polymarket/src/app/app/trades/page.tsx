"use client";

// TRADES — a live tape of every recent fill across Polymarket. One of the
// sidebar's data views (Markets · Traders · Trades). Auto-refreshes, and reuses
// the global search/category filters so the sidebar search narrows it too.

import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import TopBar from "../components/TopBar";
import { useFilters, useUrlSync } from "../context/FiltersContext";
import { fetchGlobalTrades, timeAgo, formatVolume, matchMarketCategory, type GlobalTrade } from "../lib/polymarket";
import { shortAddress } from "../lib/auth";

// The proxy caches `trades` aggressively (it's a persistent endpoint), so the
// feed is cache-served — polling fast just re-reads the same snapshot and a
// cache miss would hammer data-api's rate limit. Refresh slowly; the cache
// does the real work.
const REFRESH_MS = 60_000;

function TradesInner() {
  useUrlSync();
  const router = useRouter();
  const { search, category } = useFilters();
  const [trades, setTrades] = useState<GlobalTrade[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [paused, setPaused] = useState(false);
  const seen = useRef<Set<string>>(new Set());

  const load = useCallback(async () => {
    try {
      const fresh = await fetchGlobalTrades(100);
      setError(null);
      setTrades((prev) => {
        // Merge newest-first, de-dupe by tx, cap the tape.
        const merged = [...fresh, ...prev];
        const out: GlobalTrade[] = [];
        const ids = new Set<string>();
        for (const t of merged.sort((a, b) => b.timestamp - a.timestamp)) {
          if (ids.has(t.id)) continue;
          ids.add(t.id);
          out.push(t);
          if (out.length >= 300) break;
        }
        return out;
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    if (paused) return;
    const id = setInterval(load, REFRESH_MS);
    return () => clearInterval(id);
  }, [load, paused]);

  const q = search.trim().toLowerCase();
  const rows = trades.filter((t) => {
    if (category && !matchMarketCategory(t.market, category)) return false;
    if (!q) return true;
    return (
      t.market.toLowerCase().includes(q) ||
      t.pseudonym.toLowerCase().includes(q) ||
      t.trader.toLowerCase().includes(q)
    );
  });

  // Flash a row green/red for the first render after it arrives.
  const isNew = (id: string) => {
    if (seen.current.has(id)) return false;
    seen.current.add(id);
    return true;
  };

  return (
    <div className="max-w-[1920px] mx-auto">
      <TopBar searchPlaceholder="SEARCH TRADES — MARKET OR TRADER…" />
      <div className="p-4 space-y-3">
        {/* Header */}
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <div className="flex items-center gap-2.5">
            <span className={`w-2 h-2 rounded-full ${paused ? "bg-pixel-gray" : "bg-green-400 animate-pulse"} shadow-[0_0_8px_rgba(74,222,128,0.7)]`} />
            <span
              className="text-[15px] font-bold text-pixel-white uppercase tracking-[0.18em]"
              style={{ fontFamily: '"Space Grotesk", system-ui, sans-serif' }}
            >
              Recent Trades
            </span>
            <span className="text-[11px] text-pixel-gray tracking-wide hidden sm:inline">
              recent fills across Polymarket · cached feed
            </span>
          </div>
          <div className="flex items-center gap-2 text-[12px] font-mono">
            <span className="text-pixel-gray">{rows.length} shown</span>
            <button
              onClick={() => setPaused((p) => !p)}
              className={`pixel-btn text-[12px] px-2.5 py-1 ${paused ? "border-green-400 text-green-400" : "border-pixel-border text-pixel-gray hover:text-pixel-white"}`}
            >
              {paused ? "▶ RESUME" : "❚❚ PAUSE"}
            </button>
          </div>
        </div>

        {error && (
          <div className="pixel-panel px-3 py-2 border-red-400/40 text-[12px] text-red-400 font-mono">
            FEED ERROR — {error}
          </div>
        )}

        {loading && trades.length === 0 ? (
          <div className="pixel-panel p-8 text-center text-[14px] text-pixel-white animate-pulse">
            LOADING LIVE TRADES…
          </div>
        ) : rows.length === 0 ? (
          <div className="pixel-panel p-8 text-center">
            <div className="text-[14px] text-pixel-gray-light tracking-wider mb-1">NO TRADES MATCH</div>
            <div className="text-[12px] text-pixel-gray">
              {q || category ? "Clear the search / category filter to see the full tape." : "Waiting for fills…"}
            </div>
          </div>
        ) : (
          <div className="pixel-panel overflow-hidden">
            <div className="overflow-x-auto">
              <table className="pixel-table" style={{ minWidth: "920px", tableLayout: "fixed" }}>
                <colgroup>
                  <col style={{ width: "80px" }} />
                  <col style={{ width: "170px" }} />
                  <col style={{ width: "40%" }} />
                  <col style={{ width: "90px" }} />
                  <col style={{ width: "84px" }} />
                  <col style={{ width: "80px" }} />
                  <col style={{ width: "100px" }} />
                </colgroup>
                <thead>
                  <tr>
                    <th>TIME</th>
                    <th>TRADER</th>
                    <th>MARKET</th>
                    <th>OUTCOME</th>
                    <th>SIDE</th>
                    <th className="text-right">PRICE</th>
                    <th className="text-right">SIZE</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((t) => {
                    const fresh = isNew(t.id);
                    const isBuy = t.side === "BUY";
                    return (
                      <tr
                        key={t.id}
                        className={`transition-colors ${fresh ? (isBuy ? "bg-green-400/[0.07]" : "bg-red-400/[0.07]") : ""}`}
                      >
                        <td className="text-pixel-gray-light font-mono text-[12px]">{timeAgo(t.timestamp)}</td>
                        <td>
                          <button
                            onClick={() => router.push(`/traders/${t.trader.toLowerCase()}`)}
                            className="text-left min-w-0 max-w-full group"
                            title={t.trader}
                          >
                            {t.pseudonym && (
                              <div className="text-[12px] text-pixel-white truncate group-hover:text-green-400 transition-colors">{t.pseudonym}</div>
                            )}
                            <div className="font-mono text-[11px] text-pixel-gray group-hover:text-green-400 truncate">{shortAddress(t.trader)}</div>
                          </button>
                        </td>
                        <td className="truncate">
                          <button
                            onClick={() => t.slug && router.push(`/markets/${t.slug}`)}
                            className="text-[13px] text-pixel-white hover:text-green-400 transition-colors truncate max-w-full text-left"
                            title={t.market}
                          >
                            {t.market || "—"}
                          </button>
                        </td>
                        <td className="text-[12px] text-pixel-gray-light truncate" title={t.outcome}>{t.outcome || "—"}</td>
                        <td>
                          <span className={`pixel-badge ${isBuy ? "border-green-400 text-green-400" : "border-red-400 text-red-400"}`}>
                            {t.side}
                          </span>
                        </td>
                        <td className="num text-right text-pixel-white font-mono">{Math.round(t.price * 100)}¢</td>
                        <td className="num text-right text-pixel-white font-mono">{formatVolume(t.size * t.price)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

export default function TradesPage() {
  return (
    <Suspense>
      <TradesInner />
    </Suspense>
  );
}
