import { PolymarketMarket, PolymarketTrade, PolymarketPosition } from "./types";
import {
  getCached, setCache,
  getTradeCache, setTradeCache,
  getMarketCache, setMarketCache,
} from "./cache";

// ── Top Trader type ─────────────────────────────────────────────
export interface TopTrader {
  address: string;
  volume: number;        // total in-window USDC traded
  buyVolume: number;     // in-window USDC on BUYs
  sellVolume: number;    // in-window USDC on SELLs
  pnl: number;
  winRate: number;
  positions: number;
  marketTitles: string[];
  recentTrades: number;
  /** Trades in the last 24h — distinct from `recentTrades` (whole window).
      Lets the UI flag dormant traders even on a 30d-window leaderboard. */
  trades24h?: number;
  /** Unix-seconds timestamp of this trader's most recent in-window trade.
      Surfaces "last trade Xs ago" in the leaderboard so the user can tell
      whether a high-PnL trader is firing right now vs. went silent days ago. */
  lastTradeTs?: number;
  pnlCurve?: number[];   // ~12-point cumulative PnL over the window
}

// ── Formatting helpers ──────────────────────────────────────────

export function formatVolume(vol: number): string {
  if (vol >= 1_000_000) return `$${(vol / 1_000_000).toFixed(1)}M`;
  if (vol >= 1_000) return `$${(vol / 1_000).toFixed(1)}K`;
  return `$${vol.toFixed(0)}`;
}

export function formatPnl(pnl: number): string {
  const prefix = pnl >= 0 ? "+$" : "-$";
  const abs = Math.abs(pnl);
  if (abs >= 1_000_000) return `${prefix}${(abs / 1_000_000).toFixed(1)}M`;
  if (abs >= 1_000) return `${prefix}${(abs / 1_000).toFixed(1)}K`;
  return `${prefix}${abs.toFixed(2)}`;
}

export function timeAgo(ts: number): string {
  const diff = Date.now() - ts;
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "JUST NOW";
  if (mins < 60) return `${mins}M AGO`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}H AGO`;
  const days = Math.floor(hours / 24);
  return `${days}D AGO`;
}

// ── API helpers ─────────────────────────────────────────────────

const API_URL = process.env.NEXT_PUBLIC_API_URL || "/api/polymarket";

async function polyApi(endpoint: string, params: Record<string, string> = {}): Promise<unknown> {
  const qs = new URLSearchParams({ endpoint, ...params });
  const res = await fetch(`${API_URL}?${qs.toString()}`);
  if (!res.ok) throw new Error(`API ${res.status}`);
  return res.json();
}

// ── Categories ──────────────────────────────────────────────────

export const CATEGORIES = [
  { slug: "", label: "ALL" },
  { slug: "politics", label: "POLITICS" },
  { slug: "sports", label: "SPORTS" },
  { slug: "crypto", label: "CRYPTO" },
  { slug: "pop-culture", label: "CULTURE" },
  { slug: "business", label: "BUSINESS" },
  { slug: "science", label: "SCIENCE" },
  { slug: "tech", label: "TECH" },
  { slug: "ai", label: "AI" },
] as const;

export type CategorySlug = (typeof CATEGORIES)[number]["slug"];

const CATEGORY_KEYWORDS: Record<string, string[]> = {
  politics: ["election", "president", "congress", "senate", "party", "trump", "biden", "vote", "governor", "republican", "democrat", "midterm", "political"],
  sports: ["nba", "nfl", "mlb", "nhl", "soccer", "football", "basketball", "baseball", "tennis", "ufc", "championship", "playoffs", "super bowl", "world cup", "winner:", "score", "game handicap", "match", "beat the", "grand prix", "f1"],
  crypto: ["bitcoin", "btc", "eth", "ethereum", "solana", "sol", "crypto", "token", "altcoin", "defi", "nft", "bnb", "dogecoin", "xrp", "memecoin"],
  "pop-culture": ["movie", "album", "oscar", "grammy", "emmy", "celebrity", "kardashian", "taylor swift", "drake", "rihanna", "box office", "tv show", "streaming"],
  business: ["stock", "market cap", "revenue", "ipo", "company", "ceo", "acquisition", "earnings", "nasdaq", "s&p", "dow"],
  science: ["nasa", "space", "climate", "temperature", "earthquake", "hurricane", "sea ice", "starship", "asteroid", "disease"],
  tech: ["apple", "google", "meta", "microsoft", "openai", "ai model", "launch", "release date", "tesla"],
  ai: ["ai", "gpt", "claude", "openai", "llm", "artificial intelligence", "chatgpt", "gemini", "machine learning"],
};

export function matchTraderCategory(marketTitles: string[], category: string): boolean {
  const keywords = CATEGORY_KEYWORDS[category];
  if (!keywords) return false;
  const joined = marketTitles.join(" ").toLowerCase();
  return keywords.some((kw) => joined.includes(kw));
}

/** Match a single market title against a category's keywords. Empty/unknown
 *  category returns true (no filter). */
export function matchMarketCategory(marketTitle: string, category: string): boolean {
  if (!category) return true;
  const keywords = CATEGORY_KEYWORDS[category];
  if (!keywords) return true;
  const t = marketTitle.toLowerCase();
  return keywords.some((kw) => t.includes(kw));
}

/** Match a trader against a free-text search query (address OR market titles). */
export function matchTraderSearch(t: TopTrader, query: string): boolean {
  if (!query.trim()) return true;
  const q = query.toLowerCase();
  if (t.address.toLowerCase().includes(q)) return true;
  return t.marketTitles.some((title) => title.toLowerCase().includes(q));
}

// ── Market fetching ─────────────────────────────────────────────

export async function fetchMarkets(
  limit: number = 40,
  order: string = "volume",
  fromDate?: string,
  toDate?: string,
): Promise<PolymarketMarket[]> {
  // Always filter to current markets: end_date >= now
  const effectiveFrom = fromDate || new Date().toISOString().split("T")[0];
  const cacheKey = `markets_${order}_${limit}_${effectiveFrom}_${toDate || ""}`;
  const cached = getMarketCache(cacheKey);
  if (cached) return normalizeMarkets(cached);

  const params: Record<string, string> = {
    _limit: limit.toString(),
    active: "true",
    closed: "false",
    order,
    ascending: "false",
    end_date_min: new Date(effectiveFrom).toISOString(),
  };
  if (toDate) params.end_date_max = new Date(toDate + "T23:59:59").toISOString();

  const raw = await polyApi("markets", params) as unknown[];
  const result = normalizeMarkets(raw);
  setMarketCache(cacheKey, raw);
  return result;
}

export async function fetchMarketsByCategory(
  category: string,
  limit: number = 100,
  offset: number = 0,
): Promise<PolymarketMarket[]> {
  const cacheKey = `cat_${category}_${limit}_${offset}`;
  const cached = getMarketCache(cacheKey);
  if (cached) return normalizeMarkets(cached);

  // Events API supports tag_slug filtering and contains embedded markets
  const raw = await polyApi("events", {
    tag_slug: category,
    _limit: limit.toString(),
    _offset: offset.toString(),
    active: "true",
    closed: "false",
    end_date_min: new Date().toISOString(),
  }) as unknown;

  const events = Array.isArray(raw) ? raw : [];
  const allMarkets: unknown[] = [];
  for (const evt of events) {
    const e = evt as Record<string, unknown>;
    const markets = e.markets as unknown[] | undefined;
    if (Array.isArray(markets)) {
      allMarkets.push(...markets);
    } else {
      allMarkets.push(e);
    }
  }

  const result = normalizeMarkets(allMarkets);
  setMarketCache(cacheKey, allMarkets);
  return result;
}

export async function searchMarkets(query: string, limit: number = 40): Promise<PolymarketMarket[]> {
  const cacheKey = `search_${query.trim().toLowerCase()}_${limit}`;
  const cached = getMarketCache(cacheKey);
  if (cached) return normalizeMarkets(cached).slice(0, limit);

  const raw = await polyApi(`public-search`, {
    q: query,
    _limit: limit.toString(),
  }) as Record<string, unknown>;

  // Search returns {events: [{..., markets: [...]}]}  — flatten to market list
  const events = Array.isArray(raw) ? raw : (raw?.events as unknown[] || []);
  const allMarkets: unknown[] = [];
  for (const evt of events) {
    const e = evt as Record<string, unknown>;
    const markets = e.markets as unknown[] | undefined;
    if (Array.isArray(markets)) {
      allMarkets.push(...markets);
    } else {
      // Event itself might be a market-like object
      allMarkets.push(e);
    }
  }

  setMarketCache(cacheKey, allMarkets);
  return normalizeMarkets(allMarkets).slice(0, limit);
}

function normalizeMarkets(raw: unknown): PolymarketMarket[] {
  const items = Array.isArray(raw) ? raw : (raw && typeof raw === "object" && "id" in (raw as Record<string, unknown>)) ? [raw] : [];
  return items.filter((m: Record<string, unknown>) => {
    // Filter out incomplete/placeholder markets
    if (!m.question && !m.title) return false;
    if (!m.id && !m.condition_id && !m.conditionId) return false;
    return true;
  }).map((m: Record<string, unknown>) => {
    let outcomePrices: number[] = [0.5, 0.5];
    try {
      if (typeof m.outcomePrices === "string") {
        outcomePrices = JSON.parse(m.outcomePrices as string).map(Number);
      } else if (Array.isArray(m.outcomePrices)) {
        outcomePrices = (m.outcomePrices as unknown[]).map(Number);
      } else if (typeof m.bestAsk === "number" || typeof m.bestBid === "number") {
        const yes = Number(m.bestAsk || m.bestBid || 0.5);
        outcomePrices = [yes, 1 - yes];
      }
    } catch {}

    let outcomes: string[] = ["Yes", "No"];
    if (Array.isArray(m.outcomes)) {
      outcomes = (m.outcomes as unknown[]).map(String);
    } else if (typeof m.outcomes === "string") {
      try { outcomes = JSON.parse(m.outcomes as string).map(String); } catch {}
    }

    let clobTokenIds: string[] | undefined;
    if (Array.isArray(m.clobTokenIds)) {
      clobTokenIds = (m.clobTokenIds as unknown[]).map(String);
    } else if (typeof m.clobTokenIds === "string") {
      try { clobTokenIds = JSON.parse(m.clobTokenIds as string).map(String); } catch {}
    }

    return {
      id: String(m.id || m.condition_id || m.conditionId || ""),
      conditionId: String(m.condition_id || m.conditionId || m.id || ""),
      question: String(m.question || m.title || ""),
      category: String(m.category || m.groupItemTitle || ""),
      endDate: String(m.end_date_iso || m.endDate || m.end_date || ""),
      volume: Number(m.volume || m.volumeNum || 0),
      liquidity: Number(m.liquidity || m.liquidityNum || 0),
      outcomePrices,
      outcomes,
      active: m.active !== false,
      image: m.image as string | undefined,
      description: m.description as string | undefined,
      slug: m.slug as string | undefined,
      clobTokenIds,
    };
  });
}

// ── Single-market + price-history fetching ──────────────────────

export async function fetchMarketBySlug(slug: string): Promise<PolymarketMarket | null> {
  const cacheKey = `slug_${slug}`;
  const cached = getMarketCache(cacheKey);
  if (cached) {
    const list = normalizeMarkets(cached);
    if (list.length > 0) return list[0];
  }

  const raw = await polyApi("markets", { slug }) as unknown;
  const rawArr = Array.isArray(raw) ? raw : [raw];
  setMarketCache(cacheKey, rawArr);
  const list = normalizeMarkets(raw);
  return list[0] || null;
}

export interface PricePoint { t: number; p: number; }

export async function fetchPriceHistory(
  tokenId: string,
  interval: "1h" | "6h" | "1d" | "1w" | "1m" | "max" = "1w",
  fidelity = 60,
): Promise<PricePoint[]> {
  const cacheKey = `prices_${tokenId}_${interval}_${fidelity}`;
  const cached = getMarketCache(cacheKey);
  if (cached) return cached as unknown as PricePoint[];

  const raw = await polyApi("prices-history", {
    market: tokenId,
    interval,
    fidelity: String(fidelity),
  }) as { history?: { t: number; p: number }[] };
  const arr = Array.isArray(raw?.history) ? raw.history : [];
  const result = arr.map((x) => ({ t: Number(x.t), p: Number(x.p) }));
  if (result.length > 0) setMarketCache(cacheKey, result as unknown as unknown[]);
  return result;
}

export interface MarketTrade {
  id: string;
  price: number;
  size: number;
  side: "BUY" | "SELL";
  timestamp: number; // unix seconds
  asset_id: string;
}

/** Fetch recent trades for a market from the CLOB API.
 *  Uses the `market-trades` virtual endpoint which routes to clob.polymarket.com/trades. */
export async function fetchMarketTrades(
  tokenId: string,
): Promise<MarketTrade[]> {
  const cacheKey = `mkt_trades_${tokenId}`;
  const cached = getMarketCache(cacheKey);
  if (cached) return cached as unknown as MarketTrade[];

  const raw = await polyApi("market-trades", { asset_id: tokenId }) as unknown;
  const arr = Array.isArray(raw) ? raw : [];
  const result = arr.map((t: Record<string, unknown>) => ({
    id: String(t.id || ""),
    price: Number(t.price || 0),
    size: Number(t.size || 0),
    side: String(t.side || "BUY").toUpperCase() === "SELL" ? "SELL" as const : "BUY" as const,
    timestamp: Number(t.match_time || t.timestamp || t.created_at || 0),
    asset_id: String(t.asset_id || tokenId),
  }));
  if (result.length > 0) setMarketCache(cacheKey, result as unknown as unknown[]);
  return result;
}

/** Bucket market trades into time intervals for volume bars.
 *  Returns { t (unix seconds), buyVol, sellVol } per bucket. */
export function bucketTradeVolume(
  trades: MarketTrade[],
  bucketSec: number,
): { t: number; buyVol: number; sellVol: number }[] {
  if (!trades.length) return [];
  const buckets = new Map<number, { buyVol: number; sellVol: number }>();
  for (const tr of trades) {
    const ts = tr.timestamp > 1e12 ? tr.timestamp / 1000 : tr.timestamp; // normalize to seconds
    const bk = Math.floor(ts / bucketSec) * bucketSec;
    const entry = buckets.get(bk) || { buyVol: 0, sellVol: 0 };
    const vol = tr.size * tr.price;
    if (tr.side === "BUY") entry.buyVol += vol;
    else entry.sellVol += vol;
    buckets.set(bk, entry);
  }
  return Array.from(buckets.entries())
    .map(([t, v]) => ({ t, ...v }))
    .sort((a, b) => a.t - b.t);
}

// ── Server-side paginated trader queries ────────────────────────

export interface PagedTradersResult {
  traders: TopTrader[];
  total: number;
  page: number;
  pageSize: number;
  count: number;
  cold?: boolean;
  source: "memory" | "disk" | "fresh" | null;
  /** Unix seconds when the underlying data was last refreshed from
      Polymarket — true source age, NOT cache hit age. 0 means unknown. */
  syncedAt?: number;
}

export async function fetchTradersPage(opts: {
  days?: number;
  minPerDay?: number;
  pool?: number;
  sort?: string;
  order?: string;
  page?: number;
  pageSize?: number;
  search?: string;
  category?: string;
  minVolume?: number;
  minPnl?: number;
  minTrades?: number;
  minBuyVolume?: number;
  minSellVolume?: number;
  /** When true, server bypasses agg + per-trader caches and runs
      a full re-aggregation from Polymarket. Used by the SYNC button. */
  force?: boolean;
}): Promise<PagedTradersResult> {
  const params = new URLSearchParams({ paged: "1" });
  if (opts.force) params.set("force", "1");
  if (opts.days) params.set("days", String(opts.days));
  if (opts.minPerDay) params.set("minPerDay", String(opts.minPerDay));
  if (opts.pool) params.set("pool", String(opts.pool));
  if (opts.sort) params.set("sort", opts.sort);
  if (opts.order) params.set("order", opts.order);
  if (opts.page !== undefined) params.set("page", String(opts.page));
  if (opts.pageSize) params.set("pageSize", String(opts.pageSize));
  if (opts.search) params.set("search", opts.search);
  if (opts.category) params.set("category", opts.category);
  if (opts.minVolume && opts.minVolume > 0) params.set("minVolume", String(opts.minVolume));
  if (opts.minPnl !== undefined && Number.isFinite(opts.minPnl)) params.set("minPnl", String(opts.minPnl));
  if (opts.minTrades && opts.minTrades > 0) params.set("minTrades", String(opts.minTrades));
  if (opts.minBuyVolume && opts.minBuyVolume > 0) params.set("minBuyVolume", String(opts.minBuyVolume));
  if (opts.minSellVolume && opts.minSellVolume > 0) params.set("minSellVolume", String(opts.minSellVolume));

  const res = await fetch(`${API_URL}/active-traders?${params.toString()}`);
  if (!res.ok) throw new Error(`API ${res.status}`);
  return res.json() as Promise<PagedTradersResult>;
}

// ── Trader / User data ──────────────────────────────────────────

export type ActiveTradersProgress =
  | { phase: "leaderboard"; done: number; total: number }
  | {
      phase: "enrich";
      done: number;
      total: number;
      kept: number;
      hoursScraped: number;
      hoursTarget: number;
    };

// Streaming variant: consumes the route's NDJSON stream and reports
// per-phase progress before returning the final trader list. The route
// also serves cache HITs through this same channel as a single result
// event, so the caller doesn't need a separate cached-vs-cold path.
//
// onPartial fires whenever the server pushes an in-progress snapshot
// of the leaderboard — used to populate the table while the rest of
// the pipeline is still running.
export async function fetchTopTradersStream(
  candidatePool: number,
  options: { daysWindow?: number; minTradesPerDay?: number; force?: boolean },
  onProgress: (p: ActiveTradersProgress) => void,
  onPartial?: (traders: TopTrader[]) => void,
): Promise<{ traders: TopTrader[]; source: "memory" | "disk" | "fresh"; syncedAt: number }> {
  const { daysWindow = 7, minTradesPerDay = 1, force = false } = options;
  // force=1 bypasses both the in-memory agg cache and the per-trader
  // activity cache on the server. Without this the SYNC button looked
  // like it was working but the streaming response was a cache HIT
  // returning the same payload with the same stale syncedAt timestamp.
  const qs = new URLSearchParams({
    days: String(daysWindow),
    minPerDay: String(minTradesPerDay),
    pool: String(candidatePool),
    stream: "1",
  });
  if (force) qs.set("force", "1");
  const res = await fetch(`${API_URL}/active-traders?${qs}`);
  if (!res.ok || !res.body) throw new Error(`active-traders ${res.status}`);

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  let traders: TopTrader[] = [];
  let source: "memory" | "disk" | "fresh" = "fresh";
  let syncedAt = 0;

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let nl: number;
    while ((nl = buf.indexOf("\n")) !== -1) {
      const line = buf.slice(0, nl).trim();
      buf = buf.slice(nl + 1);
      if (!line) continue;
      let evt: Record<string, unknown>;
      try { evt = JSON.parse(line) as Record<string, unknown>; } catch { continue; }
      if (evt.type === "progress") {
        onProgress(evt as unknown as ActiveTradersProgress);
      } else if (evt.type === "partial" || evt.type === "result") {
        const arr = Array.isArray(evt.traders) ? (evt.traders as Record<string, unknown>[]) : [];
        const mapped = arr.map((t) => ({
          address: String(t.address || ""),
          volume: Number(t.volume || 0),
          buyVolume: Number(t.buyVolume || 0),
          sellVolume: Number(t.sellVolume || 0),
          pnl: Number(t.pnl || 0),
          winRate: Number(t.winRate || 0),
          positions: Number(t.positions || 0),
          marketTitles: Array.isArray(t.marketTitles) ? (t.marketTitles as string[]) : [],
          recentTrades: Number(t.recentTrades || 0),
          trades24h: Number(t.trades24h || 0),
          lastTradeTs: typeof t.lastTradeTs === "number" ? t.lastTradeTs : undefined,
          pnlCurve: Array.isArray(t.pnlCurve) ? (t.pnlCurve as number[]) : undefined,
        }));
        if (evt.type === "partial") {
          onPartial?.(mapped);
        } else {
          traders = mapped;
          if (typeof evt.source === "string") {
            source = evt.source as "memory" | "disk" | "fresh";
          }
          if (typeof evt.syncedAt === "number") syncedAt = evt.syncedAt;
        }
      } else if (evt.type === "error") {
        throw new Error(String(evt.message || "stream error"));
      }
    }
  }
  return { traders, source, syncedAt };
}

export async function fetchTopTraders(
  candidatePool: number = 1000,
  options: { daysWindow?: number; minTradesPerDay?: number } = {},
): Promise<TopTrader[]> {
  // Delegates to the server-side route which paginates the WEEK leaderboard,
  // fetches each candidate's trades + positions, and filters by activity.
  const { daysWindow = 7, minTradesPerDay = 1 } = options;
  const qs = new URLSearchParams({
    days: String(daysWindow),
    minPerDay: String(minTradesPerDay),
    pool: String(candidatePool),
  });
  const res = await fetch(`${API_URL}/active-traders?${qs}`);
  if (!res.ok) throw new Error(`active-traders ${res.status}`);
  const data = await res.json();
  const traders = Array.isArray(data?.traders) ? data.traders : [];
  return traders.map((t: Record<string, unknown>) => ({
    address: String(t.address || ""),
    volume: Number(t.volume || 0),
    buyVolume: Number(t.buyVolume || 0),
    sellVolume: Number(t.sellVolume || 0),
    pnl: Number(t.pnl || 0),
    winRate: Number(t.winRate || 0),
    positions: Number(t.positions || 0),
    marketTitles: Array.isArray(t.marketTitles) ? (t.marketTitles as string[]) : [],
    recentTrades: Number(t.recentTrades || 0),
    trades24h: Number(t.trades24h || 0),
    pnlCurve: Array.isArray(t.pnlCurve) ? (t.pnlCurve as number[]) : undefined,
  }));
}

// Paginate /activity for a user until we've got trades older than untilTs
// (unix seconds) — i.e. until we've fully covered the requested time window.
// For super-active traders we cap at MAX_TRADES so we don't fan out forever.
//
// onProgress fires after each page so the UI can stream partial results
// and show how far back the data has reached.
export interface FetchTradesProgress {
  pages: number;
  totalTrades: number;
  oldestMs: number;        // 0 if we've not seen any trade yet
  done: boolean;
  partial: PolymarketTrade[];
}

export async function fetchWalletTradesUntil(
  address: string,
  untilTs: number,
  onProgress?: (info: FetchTradesProgress) => void,
  maxTrades = 10000,
): Promise<PolymarketTrade[]> {
  // ── Check hourly cache first ──
  const cached = getTradeCache(address);
  if (cached && cached.length > 0) {
    let oldest = cached[0].timestamp;
    for (let i = 1; i < cached.length; i++) {
      if (cached[i].timestamp < oldest) oldest = cached[i].timestamp;
    }
    onProgress?.({
      pages: 0,
      totalTrades: cached.length,
      oldestMs: oldest,
      done: true,
      partial: cached,
    });
    return cached;
  }

  // ── Cache miss — paginate from API ──
  const PAGE = 1000;
  const out: PolymarketTrade[] = [];
  let oldestMs = 0;

  for (let offset = 0; offset < maxTrades; offset += PAGE) {
    const raw = await polyApi("activity", {
      user: address,
      limit: String(PAGE),
      offset: String(offset),
    }) as unknown;
    if (!Array.isArray(raw) || raw.length === 0) {
      onProgress?.({
        pages: offset / PAGE,
        totalTrades: out.length,
        oldestMs,
        done: true,
        partial: out,
      });
      break;
    }
    const items = raw as Record<string, unknown>[];

    let oldestSec = Number.POSITIVE_INFINITY;
    for (const t of items) {
      const ts = Number(t.timestamp || 0);
      if (ts > 0 && ts < oldestSec) oldestSec = ts;
      if (t.type !== "TRADE") continue;
      const price = Number(t.price || 0);
      const size = Number(t.size || 0);
      if (!Number.isFinite(price) || !Number.isFinite(size) || size <= 0) continue;
      const side = String(t.side || "BUY").toUpperCase() as "BUY" | "SELL";
      let timestamp = 0;
      if (typeof t.timestamp === "number") {
        timestamp = (t.timestamp as number) > 1e12
          ? (t.timestamp as number)
          : (t.timestamp as number) * 1000;
      }
      if (timestamp <= 0) continue;
      out.push({
        id: String(t.transactionHash || ""),
        market: String(t.title || t.slug || ""),
        conditionId: String(t.conditionId || t.asset || ""),
        side,
        price,
        size,
        pnl: Number(t.pnl || 0),
        timestamp,
        outcome: t.outcome as string | undefined,
      });
    }

    if (Number.isFinite(oldestSec)) oldestMs = oldestSec * 1000;

    const reachedCutoff =
      Number.isFinite(oldestSec) && oldestSec < untilTs;
    const exhausted = items.length < PAGE;
    const done = reachedCutoff || exhausted;

    onProgress?.({
      pages: offset / PAGE + 1,
      totalTrades: out.length,
      oldestMs,
      done,
      partial: out.slice(),
    });

    if (done) break;
  }

  // ── Store in hourly cache + build CID index ──
  if (out.length > 0) {
    setTradeCache(address, out);
  }

  return out;
}

// Parse a single /activity row into a PolymarketTrade or null. Shared by the
// full-fetch and incremental-fetch paths so they normalize identically.
function parseActivityTrade(t: Record<string, unknown>): PolymarketTrade | null {
  if (t.type !== "TRADE") return null;
  const price = Number(t.price || 0);
  const size = Number(t.size || 0);
  if (!Number.isFinite(price) || !Number.isFinite(size) || size <= 0) return null;
  const side = String(t.side || "BUY").toUpperCase() as "BUY" | "SELL";
  let timestamp = 0;
  if (typeof t.timestamp === "number") {
    timestamp = (t.timestamp as number) > 1e12
      ? (t.timestamp as number)
      : (t.timestamp as number) * 1000;
  }
  if (timestamp <= 0) return null;
  return {
    id: String(t.transactionHash || ""),
    market: String(t.title || t.slug || ""),
    conditionId: String(t.conditionId || t.asset || ""),
    side,
    price,
    size,
    pnl: Number(t.pnl || 0),
    timestamp,
    outcome: t.outcome as string | undefined,
  };
}

// Incremental refresh — paginates /activity from the newest trade backwards
// and stops as soon as it hits a trade we already have cached, then merges
// the fresh window with the existing array (dedupe by tx hash). Avoids the
// 90-day full refetch the live engine was doing every minute, and prevents
// older trades from being silently dropped if Polymarket trims the activity
// feed. `existing` is whatever's already in memory or trade cache; pass `[]`
// to force a full backfill (delegates to fetchWalletTradesUntil).
export async function fetchWalletTradesIncremental(
  address: string,
  existing: PolymarketTrade[],
  windowUntilTsSec: number,
  maxPages = 5,
): Promise<PolymarketTrade[]> {
  if (existing.length === 0) {
    // No baseline — incremental can't do better than the full fetch.
    return fetchWalletTradesUntil(address, windowUntilTsSec);
  }
  let newestMs = 0;
  for (const t of existing) if (t.timestamp > newestMs) newestMs = t.timestamp;
  const newestSec = Math.floor(newestMs / 1000);
  // If cached newest is already older than the window's left edge, we'd
  // miss data — bail to a full refetch.
  if (newestSec < windowUntilTsSec) {
    return fetchWalletTradesUntil(address, windowUntilTsSec);
  }

  const seenIds = new Set(existing.map((t) => t.id));
  const fresh: PolymarketTrade[] = [];
  const PAGE = 500;

  for (let page = 0; page < maxPages; page++) {
    const raw = await polyApi("activity", {
      user: address,
      limit: String(PAGE),
      offset: String(page * PAGE),
    }) as unknown;
    if (!Array.isArray(raw) || raw.length === 0) break;
    const items = raw as Record<string, unknown>[];

    let oldestSec = Number.POSITIVE_INFINITY;
    let hitKnown = false;
    for (const t of items) {
      const ts = Number(t.timestamp || 0);
      if (ts > 0 && ts < oldestSec) oldestSec = ts;
      const parsed = parseActivityTrade(t);
      if (!parsed) continue;
      if (seenIds.has(parsed.id)) {
        hitKnown = true;
        continue;
      }
      fresh.push(parsed);
      seenIds.add(parsed.id);
    }
    // Stop once we've paged into already-cached territory (we have everything
    // newer) or once the API returned a short page (no more data upstream).
    if (hitKnown || items.length < PAGE) break;
    // Belt-and-suspenders: if oldest item in the page is already older than
    // our newest cached trade, we're definitely in known territory.
    if (Number.isFinite(oldestSec) && oldestSec * 1000 < newestMs) break;
  }

  if (fresh.length === 0) return existing;

  // Merge + persist. Newest-first ordering matches what the rest of the app
  // expects from the bulk fetch path.
  const merged = [...fresh, ...existing].sort((a, b) => b.timestamp - a.timestamp);
  setTradeCache(address, merged);
  return merged;
}

export async function fetchWalletTrades(address: string, limit: number = 200): Promise<PolymarketTrade[]> {
  const cached = getCached<PolymarketTrade[]>(address, `trades_${limit}`);
  if (cached) return cached;

  // Polymarket data API: /activity?user=<address>&limit=<n>
  const raw = await polyApi("activity", {
    user: address,
    limit: limit.toString(),
  }) as unknown;

  const trades = Array.isArray(raw) ? raw : [];

  const result = trades
    .filter((t: Record<string, unknown>) => t.type === "TRADE")
    .map((t: Record<string, unknown>) => {
      const price = Number(t.price || 0);
      const size = Number(t.size || 0);
      const side = String(t.side || "BUY").toUpperCase() as "BUY" | "SELL";

      let timestamp = 0;
      if (typeof t.timestamp === "number") {
        timestamp = t.timestamp > 1e12 ? t.timestamp : t.timestamp * 1000;
      }

      // Extract fee if available, otherwise calculate as 2% of trade value
      const feeFromApi = Number(t.fee || t.feeAmount || t.tradeFee || 0);
      const tradeValue = price * size;
      const fee = feeFromApi > 0 ? feeFromApi : tradeValue * 0.02;

      return {
        id: String(t.transactionHash || ""),
        market: String(t.title || t.slug || ""),
        conditionId: String(t.conditionId || t.asset || ""),
        side,
        price,
        size,
        pnl: Number(t.pnl || 0),
        timestamp,
        outcome: t.outcome as string | undefined,
        fee,
      };
    })
    .filter((t) => Number.isFinite(t.price) && Number.isFinite(t.size) && t.size > 0 && t.timestamp > 0);

  setCache(address, `trades_${limit}`, result);
  return result;
}

export async function fetchPositions(address: string): Promise<PolymarketPosition[]> {
  const cached = getCached<PolymarketPosition[]>(address, "positions");
  if (cached) return cached;

  // Polymarket data API: /positions?user=<address>&sizeThreshold=.1
  const raw = await polyApi("positions", {
    user: address,
    sizeThreshold: ".1",
    limit: "100",
  }) as unknown;

  const positions = Array.isArray(raw) ? raw : [];

  const safe = (n: unknown, fallback = 0): number => {
    const v = Number(n);
    return Number.isFinite(v) ? v : fallback;
  };

  const result = positions
    .map((p: Record<string, unknown>) => {
      const size = safe(p.size);
      const avgPrice = safe(p.avgPrice);
      const currentPrice = safe(p.curPrice, avgPrice);
      const value = safe(p.currentValue, size * currentPrice);
      const pnlUsd = safe(p.cashPnl, (currentPrice - avgPrice) * size);

      return {
        conditionId: String(p.conditionId || ""),
        // CTF outcome token id — what /order needs as tokenID. The
        // Polymarket data-api returns this in the `asset` field; we used
        // to fall back to it for conditionId, which clobbered the actual
        // conditionId. Carry both as distinct fields now.
        tokenId: String(p.asset || ""),
        market: String(p.title || p.slug || ""),
        outcome: String(p.outcome || "Yes"),
        size,
        avgPrice,
        currentPrice,
        value,
        pnlUsd,
      };
    })
    .filter((p) => p.conditionId && p.size > 0);

  setCache(address, "positions", result);
  return result;
}
