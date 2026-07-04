"use client";

import { useState, useEffect, useCallback } from "react";
import { PolymarketMarket } from "../lib/types";
import {
  fetchMarkets,
  fetchMarketsByCategory,
  searchMarkets,
  CategorySlug,
  matchMarketCategory,
} from "../lib/polymarket";
import MarketCard from "./MarketCard";

type SortMode = "volume" | "liquidity" | "end_date_min";
const SORT_OPTIONS: { value: SortMode; label: string }[] = [
  { value: "volume", label: "VOL" },
  { value: "liquidity", label: "LIQ" },
  { value: "end_date_min", label: "ENDING" },
];
const PAGE_SIZE = 18;

interface Props {
  onSelectMarket?: (market: PolymarketMarket) => void;
  selectedMarket?: PolymarketMarket | null;
  search: string;
  sort: SortMode;
  setSort?: (v: SortMode) => void;
  category: CategorySlug;
  daysAgo: string;
  reloadKey: number;
  reload?: () => void;
}

export default function MarketsGrid({
  onSelectMarket, selectedMarket,
  search, sort, setSort, category, daysAgo, reloadKey, reload,
}: Props) {
  const [allMarkets, setAllMarkets] = useState<PolymarketMarket[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(0);

  const load = useCallback(async ({ silent = false }: { silent?: boolean } = {}) => {
    if (!silent) setLoading(true);
    setError(null);
    try {
      let data: PolymarketMarket[];
      if (search.trim()) {
        data = await searchMarkets(search.trim(), 100);
      } else if (category) {
        data = await fetchMarketsByCategory(category, 100);
      } else {
        data = await fetchMarkets(100, sort);
      }
      // Filter out expired markets client-side as a safety net
      const now = new Date().toISOString();
      data = data.filter((m) => !m.endDate || m.endDate >= now);
      // When both search and category are active, narrow the search results
      // by the semantic category so all visible stats (count, pagination)
      // reflect both filters at once.
      if (search.trim() && category) {
        data = data.filter((m) =>
          matchMarketCategory(m.question || "", category) ||
          matchMarketCategory(m.category || "", category),
        );
      }
      // Preserve page on silent refresh so the user doesn't lose their spot.
      setAllMarkets((prev) => {
        if (silent && prev.length > 0) return data;
        return data;
      });
      if (!silent) setPage(0);
    } catch (e: unknown) {
      if (!silent) setError(e instanceof Error ? e.message : "LOAD FAILED");
    }
    if (!silent) setLoading(false);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search, sort, category, reloadKey]);

  useEffect(() => {
    load();
  }, [load]);

  // Quietly re-poll every 15s so price-flashes on MarketCard have data to react to.
  // Skipped during searches (server-side filter) to avoid clobbering input UX.
  useEffect(() => {
    if (search.trim()) return;
    const t = setInterval(() => {
      if (document.hidden) return;
      void load({ silent: true });
    }, 15000);
    return () => clearInterval(t);
  }, [load, search]);

  const totalPages = Math.ceil(allMarkets.length / PAGE_SIZE);
  const pageMarkets = allMarkets.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

  return (
    <div className="space-y-4">
      {error && (
        <div className="pixel-panel-red p-4 text-[15px] text-pixel-red text-center">
          ERROR: {error}
        </div>
      )}

      {loading ? (
        <div className="pixel-panel p-12 text-center">
          <div className="text-[14px] font-semibold tracking-[0.2em] text-pixel-white animate-pulse glow-green">
            LOADING MARKETS
          </div>
          <div className="mt-4 flex justify-center gap-1.5">
            {[...Array(8)].map((_, i) => (
              <div
                key={i}
                className="w-2 h-2 rounded-full bg-pixel-white/30"
                style={{ animationDelay: `${i * 100}ms`, animation: "blink 1.5s step-end infinite" }}
              />
            ))}
          </div>
        </div>
      ) : (
        <>
          <div className="flex items-center justify-between px-1 gap-3">
            <span className="font-display text-[15px] font-semibold text-pixel-white tracking-[0.18em] shrink-0">
              PREDICTION MARKETS
            </span>
            <div className="flex items-center gap-1.5 ml-auto">
              {setSort && SORT_OPTIONS.map((opt) => (
                <button
                  key={opt.value}
                  onClick={() => setSort(opt.value)}
                  className={`text-[11.5px] font-mono font-semibold tracking-[0.08em] px-2.5 py-1 rounded-full border transition-colors ${
                    sort === opt.value
                      ? "border-[rgb(var(--accent)/0.55)] text-green-400 bg-green-400/10"
                      : "border-pixel-border text-pixel-gray hover:text-pixel-white hover:border-pixel-white/40"
                  }`}
                >
                  {opt.label}
                </button>
              ))}
              {reload && (
                <button
                  onClick={reload}
                  className="text-[13px] text-pixel-gray hover:text-pixel-green transition-colors ml-1"
                  title="Reload"
                >
                  ↻
                </button>
              )}
              <span className="text-[14px] text-pixel-gray font-mono ml-2 shrink-0">
                {allMarkets.length}
                {totalPages > 1 && ` · ${page + 1}/${totalPages}`}
              </span>
            </div>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
            {pageMarkets.map((market) => (
              <MarketCard
                key={market.id}
                market={market}
                onSelect={onSelectMarket}
                selected={selectedMarket?.id === market.id}
              />
            ))}
          </div>

          {/* Pagination */}
          {totalPages > 1 && (
            <div className="flex items-center justify-center gap-2 pt-4">
              <button
                onClick={() => setPage((p) => Math.max(0, p - 1))}
                disabled={page === 0}
                className="pixel-btn text-[14px] border-pixel-border text-pixel-gray hover:text-pixel-white disabled:opacity-30 disabled:cursor-not-allowed"
              >
                PREV
              </button>
              {Array.from({ length: totalPages }, (_, i) => (
                <button
                  key={i}
                  onClick={() => setPage(i)}
                  className={`pixel-btn text-[14px] w-8 ${
                    page === i
                      ? "border-pixel-white text-pixel-white"
                      : "border-pixel-border text-pixel-gray hover:text-pixel-white"
                  }`}
                >
                  {i + 1}
                </button>
              ))}
              <button
                onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
                disabled={page === totalPages - 1}
                className="pixel-btn text-[14px] border-pixel-border text-pixel-gray hover:text-pixel-white disabled:opacity-30 disabled:cursor-not-allowed"
              >
                NEXT
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
