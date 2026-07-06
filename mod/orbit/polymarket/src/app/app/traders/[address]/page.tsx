"use client";

import { Suspense, useEffect, useRef, useState, useMemo } from "react";
import { useParams, useRouter } from "next/navigation";
import {
  fetchWalletTradesUntil, fetchPositions, TopTrader,
} from "../../lib/polymarket";
import { PolymarketTrade, PolymarketPosition } from "../../lib/types";
import TraderProfile from "../../components/TraderProfile";
import TopBar from "../../components/TopBar";
import { useFilters, useUrlSync } from "../../context/FiltersContext";

interface FetchProgress {
  pages: number;
  totalTrades: number;
  oldestMs: number;        // 0 = not yet known
  done: boolean;
}

function TraderPageInner() {
  useUrlSync();
  const params = useParams();
  const router = useRouter();
  const { daysAgo, search, setSearch, category, reloadKey } = useFilters();
  const days = Number(daysAgo) > 0 ? Number(daysAgo) : 7;

  // Clear search on mount — on the traders list, search finds traders by
  // address/market. Here it filters trades by market name, so carrying
  // over a previous query (like an address) would match zero trades.
  const cleared = useRef(false);
  useEffect(() => {
    if (!cleared.current) {
      cleared.current = true;
      setSearch("");
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const address = String(
    Array.isArray(params.address) ? params.address[0] : params.address || "",
  ).toLowerCase();

  const [trades, setTrades] = useState<PolymarketTrade[]>([]);
  const [positions, setPositions] = useState<PolymarketPosition[]>([]);
  const [loading, setLoading] = useState(true);
  const [progress, setProgress] = useState<FetchProgress>({
    pages: 0, totalTrades: 0, oldestMs: 0, done: false,
  });

  // Start empty on both SSR and CSR; hydrate from localStorage post-mount
  // so we don't trigger a React hydration mismatch.
  const [watchlist, setWatchlist] = useState<Set<string>>(() => new Set());
  useEffect(() => {
    try {
      const saved = localStorage.getItem("poly8bit_watchlist");
      if (saved) setWatchlist(new Set(JSON.parse(saved) as string[]));
    } catch {}
  }, []);

  // Paginate /activity for this trader, streaming partial results so
  // the curve + table populate incrementally. Positions are fetched
  // independently so a positions-API failure can't wipe trade data.
  const prevAddress = useRef("");
  useEffect(() => {
    if (!address) return;
    let cancelled = false;

    // Only wipe state when the address actually changes — avoids
    // blanking the UI on React Strict Mode double-mounts or other
    // spurious re-renders.
    if (prevAddress.current !== address) {
      prevAddress.current = address;
      setTrades([]);
      setPositions([]);
      setProgress({ pages: 0, totalTrades: 0, oldestMs: 0, done: false });
    }
    setLoading(true);

    // cutoffSec=0 → fetch ALL available history (not just the window).
    // The display window (days) only controls which trades are shown.
    const cutoffSec = 0;

    // Fetch trades — streams partial results via callback
    fetchWalletTradesUntil(address, cutoffSec, (info) => {
      if (cancelled) return;
      setProgress(info);
      setTrades((prev) => {
        if (info.totalTrades <= prev.length) return prev;
        return info.partial;
      });
    })
      .then((t) => {
        if (!cancelled) setTrades(t);
      })
      .catch(() => {
        // Keep whatever partial trades were already streamed in —
        // don't wipe them on a late-page failure.
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    // Fetch positions independently — failure here won't touch trades
    fetchPositions(address)
      .then((p) => {
        if (!cancelled) setPositions(p);
      })
      .catch(() => {
        // Positions failed — keep existing (empty is fine, user still
        // sees trades/curve).
      });

    return () => {
      cancelled = true;
    };
  }, [address, reloadKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const trader: TopTrader = useMemo(() => {
    const cutoffMs = Date.now() - days * 86400_000;
    const recent = trades.filter((t) => t.timestamp >= cutoffMs);
    const volume = recent.reduce((s, t) => s + t.size * t.price, 0);
    const buyVolume = recent.filter((t) => t.side === "BUY").reduce((s, t) => s + t.size * t.price, 0);
    const sellVolume = recent.filter((t) => t.side === "SELL").reduce((s, t) => s + t.size * t.price, 0);
    return {
      address,
      volume,
      buyVolume,
      sellVolume,
      pnl: 0,
      winRate: -1,
      positions: positions.length,
      marketTitles: positions.map((p) => p.market).slice(0, 20),
      recentTrades: recent.length,
    };
  }, [address, days, trades, positions]);

  const watching = watchlist.has(address);
  const toggleWatch = () => {
    const next = new Set(Array.from(watchlist));
    if (next.has(address)) next.delete(address);
    else next.add(address);
    setWatchlist(next);
    try {
      localStorage.setItem("poly8bit_watchlist", JSON.stringify(Array.from(next)));
    } catch {}
  };

  // Progress: show total trades fetched and how far back we've paginated.
  const hoursCovered =
    progress.oldestMs > 0
      ? Math.max(0, (Date.now() - progress.oldestMs) / 3_600_000)
      : 0;
  const daysCovered = hoursCovered / 24;
  // Indeterminate progress — we don't know the total, so use trade count.
  const pct = progress.done ? 100 : Math.min(95, progress.pages * 10);

  return (
    <div className="max-w-[1920px] mx-auto">
      <TopBar searchPlaceholder="FILTER TRADES BY MARKET..." />

      {/* Sticky load-progress strip — sits right below the nav tabs so
          it's always visible while we're paginating /activity. Shows
          hours synced out of the total hours in the requested window. */}
      {loading && progress.pages > 0 && (
        <div className="sticky top-14 z-30 border-b-2 border-pixel-border bg-pixel-black/95 px-4 py-2">
          <div className="max-w-[1920px] mx-auto flex items-center gap-3 font-mono text-[14px]">
            <span className="text-pixel-green glow-green shrink-0">SYNCING</span>
            <span className="text-pixel-white shrink-0">
              {daysCovered.toFixed(0)}D BACK
            </span>
            <div className="pixel-bar flex-1 h-2.5">
              <div
                className="pixel-bar-fill bg-pixel-green/70 transition-all"
                style={{ width: `${pct}%` }}
              />
            </div>
            <span className="text-pixel-gray shrink-0">
              {progress.totalTrades} TRADES · PG {progress.pages}
            </span>
            <span className="text-pixel-gray-light shrink-0">{pct}%</span>
          </div>
        </div>
      )}

      <div className="p-4 space-y-4">
        <TraderProfile
          trader={trader}
          trades={trades}
          positions={positions}
          loading={loading && trades.length === 0}
          watching={watching}
          onToggleWatch={toggleWatch}
          onBack={() => router.back()}
          days={days}
          searchFilter={search}
          categoryFilter={category}
        />
      </div>
    </div>
  );
}

export default function TraderPage() {
  return (
    <Suspense>
      <TraderPageInner />
    </Suspense>
  );
}
