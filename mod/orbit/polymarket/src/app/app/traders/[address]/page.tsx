"use client";

import { Suspense, useEffect, useRef, useState, useMemo } from "react";
import { useParams, useRouter } from "next/navigation";
import {
  fetchWalletTradesUntil, fetchPositions, TopTrader, MAX_LOOKBACK_DAYS,
} from "../../lib/polymarket";
import { PolymarketTrade, PolymarketPosition, TradeFilters } from "../../lib/types";
import TraderProfile from "../../components/TraderProfile";
import TopBar from "../../components/TopBar";
import { useFilters, useUrlSync } from "../../context/FiltersContext";
import { fetchCopyBook, upsertAllocation } from "../../lib/copyBook";
import { getOwnerAddress } from "../../lib/access";
import { useAuth } from "../../context/AuthContext";

interface FetchProgress {
  pages: number;
  totalTrades: number;
  oldestMs: number;        // 0 = not yet known
  done: boolean;
  /** Upstream stopped serving pages before the window was covered — see
   *  MAX_ACTIVITY_ROWS. Not a failure, but not a whole answer either. */
  depthCapped?: boolean;
}

// Per-trader lookback overrides: { [address]: days }, 1..MAX_LOOKBACK_DAYS.
// Shared-origin localStorage — keep the map pruned and every write try/caught.
const TRADER_DAYS_KEY = "poly8bit_trader_days";

function loadTraderDaysMap(): Record<string, number> {
  try {
    const raw = JSON.parse(localStorage.getItem(TRADER_DAYS_KEY) || "{}");
    return raw && typeof raw === "object" ? (raw as Record<string, number>) : {};
  } catch {
    return {};
  }
}

function TraderPageInner() {
  useUrlSync();
  const params = useParams();
  const router = useRouter();
  const {
    daysAgo, setSearch, category, setCategory, marketQuery, setMarketQuery, reloadKey,
  } = useFilters();
  const { auth } = useAuth();
  const eoa = getOwnerAddress() ?? auth.address ?? null;
  // Are they already on the copy desk, and for how much? Read once per visit
  // so the header can say "ON DESK $250" instead of offering to add someone
  // who is already being copied.
  const [deskAllocationUsd, setDeskAllocationUsd] = useState<number | null>(null);
  const globalDays = Math.min(
    Number(daysAgo) > 0 ? Number(daysAgo) : 7,
    MAX_LOOKBACK_DAYS,
  );

  // Clear the global search on mount — this page has no search box (trade
  // filtering is click-a-market instead), so a query carried over from the
  // traders list would otherwise linger invisibly in the shared context.
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

  // Is this trader already on the COPY DESK? One read per visit; the desk
  // itself owns the polling.
  useEffect(() => {
    if (!address) return;
    let cancelled = false;
    void fetchCopyBook(eoa)
      .then((book) => {
        if (cancelled) return;
        const row = book.allocations.find((a) => a.address === address);
        setDeskAllocationUsd(row ? row.allocationUsd : null);
      })
      // Gate locked or API down — the header just offers to add them, which
      // is the same thing it does for a trader who genuinely isn't copied.
      .catch(() => {});
    return () => { cancelled = true; };
  }, [address, eoa]);

  // Strat trade-filter handoff (?tf= from the strat editor's trader list):
  // when present, the profile gates every trade through the SAME per-trade
  // filter the copy engine applies, so you see only the flow the strat would
  // mirror. Read ONCE into state — useUrlSync rewrites the URL from filter
  // state and would silently drop the param on the first filter change.
  const [stratFilters, setStratFilters] = useState<TradeFilters | null>(null);
  const [stratFilterName, setStratFilterName] = useState("");
  useEffect(() => {
    try {
      const qs = new URLSearchParams(window.location.search);
      const raw = qs.get("tf");
      if (!raw) return;
      const parsed = JSON.parse(raw) as TradeFilters;
      if (parsed && typeof parsed === "object") {
        setStratFilters(parsed);
        setStratFilterName(qs.get("tfn") || "");
      }
    } catch {}
  }, [address]);

  // Per-trader lookback override — null = follow the global window. Hydrated
  // post-mount (localStorage) to avoid SSR hydration mismatch. Overrides
  // saved before the 30-day cap existed (0 = ALL, or 60/90) fall back to
  // the global window instead of promising data we no longer sync.
  const [daysOverride, setDaysOverride] = useState<number | null>(null);
  useEffect(() => {
    const v = loadTraderDaysMap()[address];
    setDaysOverride(
      typeof v === "number" && Number.isFinite(v) && v >= 1 && v <= MAX_LOOKBACK_DAYS
        ? v
        : null,
    );
  }, [address]);

  const days = daysOverride ?? globalDays;

  const changeDays = (d: number | null) => {
    setDaysOverride(d);
    try {
      const m = loadTraderDaysMap();
      if (d === null) delete m[address];
      else m[address] = d;
      // Prune oldest-inserted entries so the shared-origin quota stays safe.
      const keys = Object.keys(m);
      if (keys.length > 200) {
        for (const k of keys.slice(0, keys.length - 200)) delete m[k];
      }
      localStorage.setItem(TRADER_DAYS_KEY, JSON.stringify(m));
    } catch {}
  };

  const [trades, setTrades] = useState<PolymarketTrade[]>([]);
  const [positions, setPositions] = useState<PolymarketPosition[]>([]);
  const [loading, setLoading] = useState(true);
  // Non-null when the /activity sync ultimately failed (e.g. upstream
  // rate-limit) — the UI must say so instead of rendering $0 stats that
  // look like a real "no trades" answer.
  const [tradesError, setTradesError] = useState<string | null>(null);
  const [retryKey, setRetryKey] = useState(0);
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
    setTradesError(null);

    // Sync back at most MAX_LOOKBACK_DAYS (30) — old accounts have 100+
    // days of pages and pulling them all blew the localStorage quota, so
    // the sync was never cached. The display window (days) only controls
    // which of the synced trades are shown.
    const cutoffSec =
      Math.floor(Date.now() / 1000) - MAX_LOOKBACK_DAYS * 86400;

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
      .catch((e: unknown) => {
        // Keep whatever partial trades were already streamed in — don't
        // wipe them on a late-page failure. But DO record the failure so
        // the page can say "sync failed" instead of a misleading 0-trades.
        if (!cancelled) {
          const msg = e instanceof Error ? e.message : String(e);
          setTradesError(
            msg === "API 429"
              ? "RATE-LIMITED BY POLYMARKET DATA API"
              : `TRADE SYNC FAILED (${msg})`,
          );
        }
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
  }, [address, reloadKey, retryKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const trader: TopTrader = useMemo(() => {
    const cutoffMs = days > 0 ? Date.now() - days * 86400_000 : 0;
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
      sharpe: 0,
      exitEntry: -1,
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
      {/* No search box here — trade filtering happens by clicking a market
          in the tables below (TraderProfile shows a clearable chip). */}
      <TopBar showSearch={false} />

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
          tradesError={tradesError}
          feedDepthCapped={progress.depthCapped === true}
          onRetrySync={() => setRetryKey((k) => k + 1)}
          watching={watching}
          onToggleWatch={toggleWatch}
          onBack={() => router.back()}
          days={days}
          daysOverride={daysOverride}
          globalDays={globalDays}
          onDaysChange={changeDays}
          categoryFilter={category}
          onCategoryChange={setCategory}
          // The topic keyword the leaderboard was filtered by (?mq=, carried
          // over by CopyTrading's row click). The list scored this trader on
          // matching markets only; the profile now shows the same slice, so
          // the two screens agree about what this trader's record is.
          marketQuery={marketQuery}
          onClearMarketQuery={() => setMarketQuery("")}
          // The rail can re-aim the gate from here — pick a different market
          // type without going back to the board. `useUrlSync` writes it to
          // ?mq=, so the screen is shareable and a reload keeps the slice.
          onMarketQueryChange={setMarketQuery}
          // Park the sticky rail below the sync strip while that's on screen,
          // under the nav bar otherwise.
          stickyTopPx={loading && progress.pages > 0 ? 94 : 56}
          stratFilters={stratFilters}
          stratFilterName={stratFilterName}
          onClearStratFilters={() => {
            setStratFilters(null);
            setStratFilterName("");
          }}
          deskAllocationUsd={deskAllocationUsd}
          onCopyToDesk={async (allocationUsd, params) => {
            // `params` is what the simulator actually replayed — the market
            // gate and the engine knobs. Posting them with the amount is what
            // makes the row the engine runs the same configuration whose
            // numbers were on screen when the decision was made. Server-side
            // `params` is a PATCH, so omitted knobs keep their template value.
            const res = await upsertAllocation({ address, allocationUsd, params }, eoa);
            setDeskAllocationUsd(res.allocation.allocationUsd);
            // Straight into THIS leader's workspace rather than the desk. You
            // just decided to copy one person; the next question is about them
            // (what does the replay say, do I start them, is the wallet
            // funded?) and every answer is one route away.
            router.push(`/copy/${address.toLowerCase()}`);
          }}
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
