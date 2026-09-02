"use client";

// TRADES — a live tape of recent fills. Two feeds behind one toggle:
//   ALL  — every recent fill across Polymarket (cached global tape).
//   MINE — every fill actually executed by YOUR trading wallet (deposit
//          wallet + signed-in EOA), near-live (60s TTL). This is the ground
//          truth of what the live engine really traded — straight from
//          Polymarket's data-api, not the engine's in-memory log.
// Auto-refreshes, and reuses the global search/category filters plus a
// per-trade FILTERS bar (side / price band / size band / keywords) — the
// side/price/size gate is the exact same one strats use to slice flow
// (lib/tradeFilters.ts); keywords are a tape-only OR-match on market title
// + outcome (UI convenience, deliberately NOT part of the Rust-mirrored
// TradeFilters gate).

import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import TopBar from "../components/TopBar";
import { useAuth } from "../context/AuthContext";
import { useFilters, useUrlSync } from "../context/FiltersContext";
import { API_BASE, fetchGlobalTrades, fetchUserTrades, timeAgo, formatVolume, matchMarketCategory, type GlobalTrade } from "../lib/polymarket";
import TradeFilterBar, { TradeFilterToggle, useTradeFilterBar } from "../components/TradeFilterBar";
import { shortAddress } from "../lib/auth";
import { describeSession, modeOf } from "../lib/tradingMode";

// The proxy caches `trades` aggressively (it's a persistent endpoint), so the
// feed is cache-served — polling fast just re-reads the same snapshot and a
// cache miss would hammer data-api's rate limit. Refresh slowly; the cache
// does the real work.
const REFRESH_MS = 60_000;
const PAGE_SIZE = 50;   // rows per page
const CHUNK = 200;      // trades pulled per "scan deeper" fetch
const POOL_CAP = 10_000; // hard safety cap on the in-memory pool

function TradesInner() {
  useUrlSync();
  const router = useRouter();
  const searchParams = useSearchParams();
  const { search, category, setCategory } = useFilters();
  const { auth } = useAuth();
  const address = auth.address;
  const [trades, setTrades] = useState<GlobalTrade[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [paused, setPaused] = useState(false);
  const [page, setPage] = useState(1);
  const [exhausted, setExhausted] = useState(false);
  // ALL = global tape · MINE = fills by my trading wallet(s). ?feed=mine
  // deep-links straight to the personal tape (init-only, not URL-synced).
  const [feed, setFeed] = useState<"all" | "mine">(
    searchParams.get("feed") === "mine" ? "mine" : "all",
  );
  // The wallets whose fills count as "mine": the derived deposit wallet
  // (what the engine actually trades through) + the signed-in EOA.
  const [myWallets, setMyWallets] = useState<string[]>([]);
  // Engine state for the MINE header badge, in the console's two words
  // (lib/tradingMode.ts): STOPPED / TEST / LIVE.
  const [engine, setEngine] = useState<{ running: boolean; auto: boolean } | null>(null);
  const seen = useRef<Set<string>>(new Set());
  // How deep into data-api's trade history we've fetched. Offsets drift as
  // new fills push old ones deeper — the de-dupe merge absorbs the overlap.
  const deepestOffset = useRef(0);
  // ── Per-trade FILTERS bar — side / price band / size band / keywords.
  // Shared with the trader profile (components/TradeFilterBar). ?kw=bitcoin,eth
  // seeds the keyword chips (init-only, not URL-synced).
  const [showFilters, setShowFilters] = useState(false);
  const bar = useTradeFilterBar(
    useMemo(
      () => (searchParams.get("kw") ?? "").split(",").map((k) => k.trim().toLowerCase()).filter(Boolean),
      [], // eslint-disable-line react-hooks/exhaustive-deps
    ),
  );
  const activeFilterCount = bar.count + (category ? 1 : 0);

  // Resolve EOA → deposit wallet once per sign-in. The deposit wallet is the
  // address that actually appears as the trader on engine-executed fills.
  useEffect(() => {
    if (!address) { setMyWallets([]); return; }
    let cancelled = false;
    (async () => {
      const list = [address.toLowerCase()];
      try {
        const r = await fetch(`${API_BASE}/deposit-wallet/info?eoa=${address}`);
        if (r.ok) {
          const j = (await r.json()) as { depositWallet?: string };
          if (j.depositWallet) list.unshift(j.depositWallet.toLowerCase());
        }
      } catch { /* EOA-only fallback still works */ }
      if (!cancelled) setMyWallets(Array.from(new Set(list)));
    })();
    return () => { cancelled = true; };
  }, [address]);

  // Engine badge poll — only while the MINE tape is showing.
  useEffect(() => {
    if (feed !== "mine" || !address) { setEngine(null); return; }
    let stop = false;
    const poll = async () => {
      try {
        const r = await fetch(`${API_BASE}/live/status?eoa=${address}`);
        const j = (await r.json()) as { running?: boolean; config?: { autoExecute?: boolean } };
        if (!stop) setEngine({ running: !!j.running, auto: !!j.config?.autoExecute });
      } catch { if (!stop) setEngine(null); }
    };
    poll();
    const id = setInterval(poll, 30_000);
    return () => { stop = true; clearInterval(id); };
  }, [feed, address]);

  const merge = useCallback((fresh: GlobalTrade[]) => {
    setTrades((prev) => {
      const merged = [...fresh, ...prev];
      const out: GlobalTrade[] = [];
      const ids = new Set<string>();
      for (const t of merged.sort((a, b) => b.timestamp - a.timestamp)) {
        if (ids.has(t.id)) continue;
        ids.add(t.id);
        out.push(t);
        if (out.length >= POOL_CAP) break;
      }
      return out;
    });
  }, []);

  // One fetch of the current feed at a given depth. MINE queries every
  // owned wallet and flattens — usually only the deposit wallet has fills.
  const fetchFeed = useCallback(async (limit: number, offset: number): Promise<GlobalTrade[]> => {
    if (feed === "mine") {
      if (myWallets.length === 0) return [];
      const results = await Promise.all(myWallets.map((w) => fetchUserTrades(w, limit, offset)));
      return results.flat();
    }
    return fetchGlobalTrades(limit, offset);
  }, [feed, myWallets]);

  const load = useCallback(async () => {
    try {
      const fresh = await fetchFeed(100, 0);
      setError(null);
      merge(fresh);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [merge, fetchFeed]);

  // Pull the next CHUNK of older history into the pool. Pagination and
  // search both read from the pool, so this deepens them together.
  const loadMore = useCallback(async () => {
    if (loadingMore || exhausted) return;
    setLoadingMore(true);
    try {
      const off = deepestOffset.current === 0 ? 100 : deepestOffset.current;
      const fresh = await fetchFeed(CHUNK, off);
      deepestOffset.current = off + CHUNK;
      if (fresh.length < CHUNK || off + CHUNK >= POOL_CAP) setExhausted(true);
      setError(null);
      merge(fresh);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoadingMore(false);
    }
  }, [loadingMore, exhausted, merge, fetchFeed]);

  // Feed switch (or wallet resolution) invalidates the whole pool — reset
  // and refetch from depth 0. The `load` effect below fires on the new feed.
  useEffect(() => {
    setTrades([]);
    setPage(1);
    setExhausted(false);
    setLoading(true);
    seen.current = new Set();
    deepestOffset.current = 0;
  }, [feed, myWallets]);

  useEffect(() => {
    load();
    if (paused) return;
    const id = setInterval(load, REFRESH_MS);
    return () => clearInterval(id);
  }, [load, paused]);

  const q = search.trim().toLowerCase();
  // True when ANYTHING is narrowing the tape — search, category, or the
  // per-trade FILTERS bar. Drives the match counter + empty-state copy.
  const narrowing = !!q || !!category || bar.active;
  const filtered = trades.filter((t) => {
    if (category && !matchMarketCategory(t.market, category)) return false;
    if (!bar.matches(t)) return false;
    if (!q) return true;
    return (
      t.market.toLowerCase().includes(q) ||
      t.outcome.toLowerCase().includes(q) ||
      t.pseudonym.toLowerCase().includes(q) ||
      t.trader.toLowerCase().includes(q)
    );
  });

  // Jump back to page 1 whenever the query/category/filters change — the old
  // page number is meaningless against a different result set.
  useEffect(() => { setPage(1); }, [q, category, bar.tf, bar.keywords]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const curPage = Math.min(page, totalPages);
  const rows = filtered.slice((curPage - 1) * PAGE_SIZE, curPage * PAGE_SIZE);

  const goNext = () => {
    if (curPage < totalPages) setPage(curPage + 1);
    else if (!exhausted) loadMore().then(() => setPage(curPage + 1));
  };

  // Flash a row green/red for the first render after it arrives — only on
  // page 1, where fresh fills actually land; deeper pages are old history.
  const isNew = (id: string) => {
    if (seen.current.has(id)) return false;
    seen.current.add(id);
    return curPage === 1;
  };

  return (
    <div className="max-w-[1920px] mx-auto">
      <TopBar searchPlaceholder="SEARCH TRADES — MARKET OR TRADER…" />
      <div className="p-4 space-y-3">
        {/* Header */}
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <div className="flex items-center gap-2.5 flex-wrap">
            <span className={`w-2 h-2 rounded-full ${paused ? "bg-pixel-gray" : "bg-green-400 animate-pulse"} shadow-[0_0_8px_rgba(74,222,128,0.7)]`} />
            <span
              className="text-[15px] font-bold text-pixel-white uppercase tracking-[0.18em]"
              style={{ fontFamily: '"Space Grotesk", system-ui, sans-serif' }}
            >
              {feed === "mine" ? "My Trades" : "Recent Trades"}
            </span>
            {/* Feed toggle — the whole point of this page: whose fills am I looking
                at. A joined segmented pill (not two pixel-btns) so it reads as one
                control and doesn't fight the title for weight. */}
            <div className="inline-flex items-stretch rounded-full border border-pixel-border overflow-hidden font-mono text-[11px] font-bold tracking-[0.1em]">
              <button
                onClick={() => setFeed("all")}
                className={`px-3 py-1 transition-colors ${feed === "all" ? "bg-green-400/10 text-green-400" : "text-pixel-gray hover:text-pixel-white"}`}
              >
                ALL
              </button>
              <span className="w-px bg-pixel-border" />
              <button
                onClick={() => setFeed("mine")}
                title="Every fill executed by your trading wallet — the on-chain truth of what the engine traded"
                className={`px-3 py-1 transition-colors ${feed === "mine" ? "bg-green-400/10 text-green-400" : "text-pixel-gray hover:text-pixel-white"}`}
              >
                MINE
              </button>
            </div>
            {feed === "mine" && engine && (
              <span
                title={describeSession(engine.running ? "RUNNING" : "STOPPED", modeOf(engine.auto)).title}
                className={`pixel-badge ${
                  engine.running
                    ? engine.auto
                      ? "border-green-400 text-green-400"
                      : "border-amber-400 text-amber-400"
                    : "border-pixel-border text-pixel-gray"
                }`}
              >
                ENGINE {engine.running ? describeSession("RUNNING", modeOf(engine.auto)).text : "STOPPED"}
              </span>
            )}
            <span className="text-[11px] text-pixel-gray tracking-wide hidden sm:inline">
              {feed === "mine"
                ? "fills executed by your trading wallet · refreshes ~1 min"
                : "recent fills across Polymarket · cached feed"}
            </span>
          </div>
          <div className="flex items-center gap-2 text-[12px] font-mono">
            <span className="text-pixel-gray">
              {narrowing ? `${filtered.length} matches · ` : ""}{trades.length} loaded
            </span>
            <TradeFilterToggle
              open={showFilters}
              onToggle={() => setShowFilters((s) => !s)}
              count={activeFilterCount}
            />
            <button
              onClick={() => setPaused((p) => !p)}
              className={`pixel-btn text-[12px] px-2.5 py-1 ${paused ? "border-green-400 text-green-400" : "border-pixel-border text-pixel-gray hover:text-pixel-white"}`}
            >
              {paused ? "▶ RESUME" : "❚❚ PAUSE"}
            </button>
          </div>
        </div>

        {/* ── FILTERS bar — same dimensions strats gate on (side / entry-price
            band / USD size band) + the global category buckets. Collapsed, an
            active-filter summary chip keeps what's narrowing the tape visible. */}
        <TradeFilterBar bar={bar} open={showFilters} category={category} onCategoryChange={setCategory} />

        {error && (
          <div className="pixel-panel px-3 py-2 border-red-400/40 text-[12px] text-red-400 font-mono">
            FEED ERROR — {error}
          </div>
        )}

        {feed === "mine" && !address ? (
          <div className="pixel-panel p-8 text-center">
            <div className="text-[14px] text-pixel-gray-light tracking-wider mb-1">NOT SIGNED IN</div>
            <div className="text-[12px] text-pixel-gray">
              Sign in (top right) to see the fills executed by your trading wallet.
            </div>
          </div>
        ) : loading && trades.length === 0 ? (
          <div className="pixel-panel p-8 text-center text-[14px] text-pixel-white animate-pulse">
            {feed === "mine" ? "LOADING YOUR TRADES…" : "LOADING LIVE TRADES…"}
          </div>
        ) : rows.length === 0 ? (
          <div className="pixel-panel p-8 text-center">
            <div className="text-[14px] text-pixel-gray-light tracking-wider mb-1">
              {feed === "mine" && !narrowing ? "NO FILLS YET" : "NO TRADES MATCH"}
            </div>
            <div className="text-[12px] text-pixel-gray mb-3">
              {narrowing
                ? "No hits in the loaded tape — scan deeper into history, or clear the filters."
                : feed === "mine"
                  ? engine?.running && !engine.auto
                    ? "The engine is on PAPER — it computes mirrors but places NO real orders, so no fills exist. Flip the PAPER|REAL switch to REAL in the LIVE tab's COPY ENGINE header to trade for real."
                    : engine?.running
                      ? "Engine is LIVE but nothing has filled yet — fills appear here within ~1 min of executing."
                      : "No engine session and no past fills for this wallet. Start the engine under STRAT → TRADE, with the switch on LIVE, to see trades land here."
                  : "Waiting for fills…"}
            </div>
            {narrowing && !exhausted && (
              <button
                onClick={loadMore}
                disabled={loadingMore}
                className="pixel-btn text-[12px] px-3 py-1.5 border-green-400 text-green-400 disabled:opacity-50"
              >
                {loadingMore ? "SCANNING…" : `⌄ SCAN ${CHUNK} OLDER TRADES`}
              </button>
            )}
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

            {/* ── Pagination — pool-backed: NEXT past the last loaded page
                   pulls the next CHUNK of history from data-api. ── */}
            <div className="flex items-center justify-between gap-3 flex-wrap px-3 py-2 border-t border-pixel-border font-mono text-[12px]">
              <div className="flex items-center gap-1.5">
                <button
                  onClick={() => setPage(1)}
                  disabled={curPage === 1}
                  className="pixel-btn px-2 py-1 border-pixel-border text-pixel-gray hover:text-pixel-white disabled:opacity-40"
                >
                  «
                </button>
                <button
                  onClick={() => setPage(Math.max(1, curPage - 1))}
                  disabled={curPage === 1}
                  className="pixel-btn px-2.5 py-1 border-pixel-border text-pixel-gray hover:text-pixel-white disabled:opacity-40"
                >
                  ‹ PREV
                </button>
                {/* windowed page numbers around the current page */}
                {Array.from({ length: totalPages }, (_, i) => i + 1)
                  .filter((n) => n === 1 || n === totalPages || Math.abs(n - curPage) <= 2)
                  .map((n, i, arr) => (
                    <span key={n} className="flex items-center gap-1.5">
                      {i > 0 && arr[i - 1] !== n - 1 && <span className="text-pixel-gray">…</span>}
                      <button
                        onClick={() => setPage(n)}
                        className={`pixel-btn px-2.5 py-1 ${n === curPage ? "border-green-400 text-green-400" : "border-pixel-border text-pixel-gray hover:text-pixel-white"}`}
                      >
                        {n}
                      </button>
                    </span>
                  ))}
                <button
                  onClick={goNext}
                  disabled={loadingMore || (curPage >= totalPages && exhausted)}
                  className="pixel-btn px-2.5 py-1 border-pixel-border text-pixel-gray hover:text-pixel-white disabled:opacity-40"
                >
                  {loadingMore ? "LOADING…" : "NEXT ›"}
                </button>
              </div>
              <div className="flex items-center gap-2.5">
                <span className="text-pixel-gray">
                  page {curPage}/{totalPages}{exhausted ? " · end of history" : ""}
                </span>
                {!exhausted && (
                  <button
                    onClick={loadMore}
                    disabled={loadingMore}
                    className="pixel-btn px-2.5 py-1 border-pixel-border text-pixel-gray hover:text-green-400 disabled:opacity-50"
                    title="Fetch older trades into the tape — widens search + adds pages"
                  >
                    {loadingMore ? "SCANNING…" : `⌄ SCAN ${CHUNK} OLDER`}
                  </button>
                )}
              </div>
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
