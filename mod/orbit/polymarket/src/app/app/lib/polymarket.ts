import { PolymarketMarket, PolymarketTrade, PolymarketPosition, TraderRoiStats } from "./types";
import {
  getCached, setCache,
  getTradeCache, setTradeCache,
  getMarketCache, setMarketCache,
} from "./cache";
import { computeFifoTrades } from "./pnlEngine";
import { statsFromReturns } from "./strats/strat";

// ── Top Trader type ─────────────────────────────────────────────
export interface TopTrader {
  address: string;
  volume: number;        // total in-window USDC traded
  buyVolume: number;     // in-window USDC on BUYs
  sellVolume: number;    // in-window USDC on SELLs
  pnl: number;
  winRate: number;
  /** Sharpe ratio over the window (per-closed-trade returns), computed
      server-side by the same `stats_from_returns` formula the live engine
      uses. Default SCORE metric in the leaderboard. */
  sharpe: number;
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

// Thresholds sit just below each unit so the ROUNDED value picks the bucket,
// not the raw one: at `>= 1_000` a volume of 999.6 fell through to the plain
// branch and rendered "$1000" — four digits sitting in a column of "$10.0K"s,
// which reads as a broken formatter rather than as a number.
export function formatVolume(vol: number): string {
  if (vol >= 999_950) return `$${(vol / 1_000_000).toFixed(1)}M`;
  if (vol >= 999.5) return `$${(vol / 1_000).toFixed(1)}K`;
  return `$${vol.toFixed(0)}`;
}

export function formatPnl(pnl: number): string {
  const prefix = pnl >= 0 ? "+$" : "-$";
  const abs = Math.abs(pnl);
  // Same rounding seam as formatVolume — 999.996 must be "+$1.0K", not
  // "+$1000.00". The cent branch below rounds to 2dp, hence 999.995.
  if (abs >= 999_950) return `${prefix}${(abs / 1_000_000).toFixed(1)}M`;
  if (abs >= 999.995) return `${prefix}${(abs / 1_000).toFixed(1)}K`;
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

// In the browser this is the same-origin proxy path the gateway rewrites. On
// the SERVER (the background backtest worker) a relative path has nothing to
// resolve against, so calls go straight to the Rust API — which is also one
// less hop for a process that lives on the same box.
const API_URL =
  typeof window === "undefined"
    ? process.env.POLYMARKET_API_URL || "http://127.0.0.1:50091"
    : process.env.NEXT_PUBLIC_API_URL || "/api/polymarket";
/** Base URL of the module API — for callers that need non-proxy routes
 *  (deposit-wallet info, live engine status) without hardcoding the path. */
export const API_BASE = API_URL;

// The API is owner-gated. In the browser access.ts patches `fetch` once and
// stamps every API-bound request with the session's Bearer token; there is no
// such patch in Node, so the worker hands its minted owner token to this
// module and the fetch helpers below attach it. Unset (the browser case) ⇒ no
// header, and the patch does its job.
let serverToken: string | null = null;
export function setServerAuthToken(token: string | null): void {
  serverToken = token;
}
export function serverAuthHeaders(): Record<string, string> {
  return serverToken ? { Authorization: `Bearer ${serverToken}` } : {};
}

// Bare `fetch` surfaces any momentary blip — a wifi hiccup, a laptop wake, a
// Caddy graceful-reload — as a thrown "Failed to fetch". The live engine polls
// every enabled trader concurrently each cycle, so one blip used to spawn a
// row of identical FETCH_FAILED decisions all stamped the same second. Absorb
// transient failures with a short bounded retry + per-attempt timeout; only a
// genuine, sustained outage (or a real HTTP error) makes it out of here.
async function polyApi(endpoint: string, params: Record<string, string> = {}): Promise<unknown> {
  const qs = new URLSearchParams({ endpoint, ...params });
  // NOTE the slash before `?`. The gateway route strips the `/api/polymarket`
  // prefix; without a trailing slash the upstream request line has an empty
  // path and Caddy/Cloudflare reject it with a 400. `/api/polymarket/?…` →
  // strips to `/?…` → valid. (Direct localhost:50091 tolerates both.)
  const url = `${API_URL}/?${qs.toString()}`;
  const ATTEMPTS = 3;
  let lastErr: unknown;
  for (let attempt = 0; attempt < ATTEMPTS; attempt++) {
    try {
      const res = await fetch(url, {
        headers: serverAuthHeaders(),
        signal: AbortSignal.timeout(20_000),
      });
      // 5xx is the gateway/backend briefly unhappy — worth retrying. 429 is
      // the upstream data-api rate-limiting us — also transient, but needs a
      // LONGER pause than a blip. Any other 4xx is a real client error (bad
      // address, bad params) and won't change on retry.
      if (!res.ok) throw new Error(`API ${res.status}`);
      return await res.json();
    } catch (e) {
      lastErr = e;
      // Don't burn retries on a deterministic 4xx (429 excepted).
      const msg = e instanceof Error ? e.message : "";
      if (/^API 4\d\d$/.test(msg) && msg !== "API 429") throw e;
      if (attempt < ATTEMPTS - 1) {
        // Rate-limit: 3s, 8s. Everything else: 300ms, 900ms.
        const wait = msg === "API 429"
          ? [3_000, 8_000][attempt]
          : 300 * 3 ** attempt;
        await new Promise((r) => setTimeout(r, wait));
      }
    }
  }
  throw lastErr instanceof Error ? lastErr : new Error(String(lastErr));
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

/** Near-live price history for sub-hour candle markets. Bypasses BOTH the
 *  client market cache and the server's 24h `prices-history` persistence by
 *  reading through the `live-prices-history` alias (15s server TTL) at
 *  fidelity 1 — 1-minute bars, the CLOB minimum. A 5-minute market's whole
 *  life fits inside one regular cache generation, so the normal cached path
 *  would freeze its series at the first fetch. */
export async function fetchPriceHistoryLive(tokenId: string): Promise<PricePoint[]> {
  const raw = await polyApi("live-prices-history", {
    market: tokenId,
    interval: "1h",
    fidelity: "1",
  }) as { history?: { t: number; p: number }[] };
  const arr = Array.isArray(raw?.history) ? raw.history : [];
  return arr.map((x) => ({ t: Number(x.t), p: Number(x.p) }));
}

/** Near-live CLOB midpoint for one token (0–1), or null when the book is
 *  empty/unreadable. Used as the synthetic "now" point on candle series —
 *  fidelity-1 history can lag ~90s, which is a third of a 5-minute candle. */
export async function fetchMidpointLive(tokenId: string): Promise<number | null> {
  try {
    const raw = await polyApi("live-midpoint", { token_id: tokenId }) as { mid?: unknown };
    const mid = Number(raw?.mid);
    return Number.isFinite(mid) && mid > 0 && mid < 1 ? mid : null;
  } catch {
    return null;
  }
}

export interface MarketTrade {
  id: string;
  price: number;
  size: number;
  side: "BUY" | "SELL";
  timestamp: number; // unix seconds
  asset_id: string;
}

/** Fetch recent trades for a market from the public data-api trade feed.
 *  Uses the `market-trades` virtual endpoint which routes to
 *  data-api.polymarket.com/trades?market=<conditionId> — NOT the CLOB API's
 *  `/trades`, which is an authenticated endpoint for the caller's own fills
 *  and returns 401 for anyone else's market history. */
export async function fetchMarketTrades(
  conditionId: string,
): Promise<MarketTrade[]> {
  const cacheKey = `mkt_trades_${conditionId}`;
  const cached = getMarketCache(cacheKey);
  if (cached) return cached as unknown as MarketTrade[];

  const raw = await polyApi("market-trades", { market: conditionId }) as unknown;
  const arr = Array.isArray(raw) ? raw : [];
  const result = arr.map((t: Record<string, unknown>) => ({
    id: String(t.id || t.transactionHash || ""),
    price: Number(t.price || 0),
    size: Number(t.size || 0),
    side: String(t.side || "BUY").toUpperCase() === "SELL" ? "SELL" as const : "BUY" as const,
    timestamp: Number(t.timestamp || t.match_time || t.created_at || 0),
    asset_id: String(t.asset || t.asset_id || ""),
  }));
  if (result.length > 0) setMarketCache(cacheKey, result as unknown as unknown[]);
  return result;
}

// ── Global live trades feed ─────────────────────────────────────
// data-api `/trades` — every recent fill across Polymarket (not market- or
// user-scoped). Powers the sidebar TRADES view: a live tape of who's trading
// what, right now.
export interface GlobalTrade {
  id: string;
  trader: string;      // proxy wallet address
  pseudonym: string;   // Polymarket display handle
  market: string;      // market title
  slug: string;
  conditionId: string;
  outcome: string;     // e.g. "Yes" / "Up"
  side: "BUY" | "SELL";
  price: number;       // 0..1
  size: number;        // shares
  timestamp: number;   // ms
}

export async function fetchGlobalTrades(limit = 100, offset = 0): Promise<GlobalTrade[]> {
  const params: Record<string, string> = { limit: String(limit), takerOnly: "false" };
  if (offset > 0) params.offset = String(offset);
  const raw = await polyApi("trades", params) as unknown;
  return mapDataApiTrades(raw);
}

/** Every fill executed BY a specific wallet (the user's trading wallet), via
 *  the `user-trades` virtual endpoint → data-api `/trades?user=<wallet>`.
 *  Distinct endpoint name so the proxy gives it a 60s near-live TTL instead
 *  of the global tape's 1h freshness window. */
export async function fetchUserTrades(user: string, limit = 100, offset = 0): Promise<GlobalTrade[]> {
  const params: Record<string, string> = { user, limit: String(limit), takerOnly: "false" };
  if (offset > 0) params.offset = String(offset);
  const raw = await polyApi("user-trades", params) as unknown;
  return mapDataApiTrades(raw);
}

function mapDataApiTrades(raw: unknown): GlobalTrade[] {
  const arr = Array.isArray(raw) ? raw : [];
  return arr
    .map((t: Record<string, unknown>) => {
      const tsRaw = Number(t.timestamp || 0);
      const timestamp = tsRaw > 1e12 ? tsRaw : tsRaw * 1000;
      return {
        id: String(t.transactionHash || `${t.proxyWallet}-${t.timestamp}-${t.asset}`),
        trader: String(t.proxyWallet || ""),
        pseudonym: String(t.pseudonym || t.name || ""),
        market: String(t.title || t.slug || ""),
        slug: String(t.slug || ""),
        conditionId: String(t.conditionId || ""),
        outcome: String(t.outcome || ""),
        side: String(t.side || "BUY").toUpperCase() === "SELL" ? ("SELL" as const) : ("BUY" as const),
        price: Number(t.price || 0),
        size: Number(t.size || 0),
        timestamp,
      };
    })
    .filter((t) => t.timestamp > 0 && t.size > 0);
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
  /** Free-text market-topic filter — narrows traders to those active in
      matching markets and recomputes their stats from only those markets. */
  marketQuery?: string;
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
  if (opts.marketQuery) params.set("marketQuery", opts.marketQuery);
  if (opts.minVolume && opts.minVolume > 0) params.set("minVolume", String(opts.minVolume));
  if (opts.minPnl !== undefined && Number.isFinite(opts.minPnl)) params.set("minPnl", String(opts.minPnl));
  if (opts.minTrades && opts.minTrades > 0) params.set("minTrades", String(opts.minTrades));
  if (opts.minBuyVolume && opts.minBuyVolume > 0) params.set("minBuyVolume", String(opts.minBuyVolume));
  if (opts.minSellVolume && opts.minSellVolume > 0) params.set("minSellVolume", String(opts.minSellVolume));

  const res = await fetch(`${API_URL}/active-traders?${params.toString()}`, { headers: serverAuthHeaders() });
  if (!res.ok) throw new Error(`API ${res.status}`);
  return res.json() as Promise<PagedTradersResult>;
}

/** The candidate pool the server's hourly warmup aggregates under
    (`warmup_cycle` in pipeline.rs). A paged request for ANY other pool is a
    different cache key, and a cold paged key returns `{cold:true, traders:[]}`
    rather than computing — so asking for the default 1000 got an empty
    leaderboard every time, which is why forking a template used to seed zero
    traders. Every leaderboard read in the console asks for this pool. */
export const WARMED_CANDIDATE_POOL = 2000;

// Top-N trader addresses for the currently active filters, sorted by P&L.
// Used to seed a new/empty strat with a sensible default instead of leaving
// it at 0 traders — swallows errors and returns [] so callers can no-op.
export async function fetchTopTraderAddresses(
  opts: { days?: number; minPerDay?: number; category?: string; marketQuery?: string },
  n = 10,
): Promise<string[]> {
  try {
    const res = await fetchTradersPage({
      days: opts.days,
      minPerDay: opts.minPerDay,
      pool: WARMED_CANDIDATE_POOL,
      category: opts.category || undefined,
      marketQuery: opts.marketQuery || undefined,
      sort: "pnl",
      order: "desc",
      pageSize: n,
      page: 0,
    });
    return res.traders.slice(0, n).map((t) => t.address);
  } catch {
    return [];
  }
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
  options: { daysWindow?: number; minTradesPerDay?: number; force?: boolean; signal?: AbortSignal },
  onProgress: (p: ActiveTradersProgress) => void,
  onPartial?: (traders: TopTrader[]) => void,
): Promise<{ traders: TopTrader[]; source: "memory" | "disk" | "fresh"; syncedAt: number }> {
  const { daysWindow = 7, minTradesPerDay = 1, force = false, signal } = options;
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
  // Pass the abort signal through to fetch so a re-click on SYNC can
  // cancel the prior in-flight stream and start fresh. Without this an
  // HMR-dropped stream leaves inFlightRef stuck true on the caller and
  // every subsequent click silently no-ops.
  const res = await fetch(`${API_URL}/active-traders?${qs}`, { headers: serverAuthHeaders(), signal });
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
          sharpe: Number(t.sharpe || 0),
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
  const res = await fetch(`${API_URL}/active-traders?${qs}`, { headers: serverAuthHeaders() });
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
    sharpe: Number(t.sharpe || 0),
    positions: Number(t.positions || 0),
    marketTitles: Array.isArray(t.marketTitles) ? (t.marketTitles as string[]) : [],
    recentTrades: Number(t.recentTrades || 0),
    trades24h: Number(t.trades24h || 0),
    lastTradeTs: typeof t.lastTradeTs === "number" ? t.lastTradeTs : undefined,
    pnlCurve: Array.isArray(t.pnlCurve) ? (t.pnlCurve as number[]) : undefined,
  }));
}

// Paginate /activity for a user until we've got trades older than untilTs
// (unix seconds) — i.e. until we've fully covered the requested time window.
// For super-active traders we cap at MAX_TRADES so we don't fan out forever.
//
// onProgress fires after each page so the UI can stream partial results
// and show how far back the data has reached.

// Hard ceiling on how far back ANY trade sync reaches. Every caller's
// untilTs is clamped to this — passing 0 ("all history") now means "the
// last 30 days", not "back to the trader's first trade" (which was pulling
// 100+ days of pages for old accounts and blowing the shared-origin
// localStorage quota, so nothing got cached at all).
export const MAX_LOOKBACK_DAYS = 30;

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
  // Clamp the window to the global ceiling — see MAX_LOOKBACK_DAYS.
  const minUntilTs =
    Math.floor(Date.now() / 1000) - MAX_LOOKBACK_DAYS * 86400;
  if (untilTs < minUntilTs) untilTs = minUntilTs;

  // ── Check hourly cache first ──
  // An empty cached array is a real answer ("no trades in the window",
  // cached below on a clean fetch) — honoring it avoids refetching
  // inactive traders on every visit. Errors never land in the cache;
  // polyApi throws on any non-200.
  const cached = getTradeCache(address);
  if (cached) {
    let oldest = cached.length > 0 ? cached[0].timestamp : 0;
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
  // data-api enforces a max activity limit of 500 and 400s above it (same
  // cap the Rust pipeline documents) — limit=1000 made EVERY first-page
  // fetch fail deterministically, so trader profiles showed 0 trades while
  // positions (a different endpoint) loaded fine.
  const PAGE = 500;
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
        slug: t.slug ? String(t.slug) : undefined,
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
  // Drop the last page's spillover past the 30-day ceiling before caching
  // so the stored array (and its per-CID index copies) stays bounded.
  // Empty results are cached too — see the cache-first read above.
  const trimmed = out.filter((t) => t.timestamp >= minUntilTs * 1000);
  setTradeCache(address, trimmed);

  return trimmed;
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
    slug: t.slug ? String(t.slug) : undefined,
    conditionId: String(t.conditionId || t.asset || ""),
    side,
    price,
    size,
    pnl: Number(t.pnl || 0),
    timestamp,
    outcome: t.outcome as string | undefined,
  };
}

// Minimum wall-clock between two /activity syncs of the SAME trader, shared
// by every caller in the tab. The watched traders are polled from several
// places at once — the live engine's cycle, the console's background curve
// refresh, the LIVE fills panel — and each used to pull its own copy, so a
// 30s cadence in three places was three times the data-api budget. One sync
// per trader per 30s: callers that ask sooner get the cache the last sync
// left behind. Mirrors MIN_POLL_MINUTES here and `default_min_interval_ms`
// in api/src/live_engine.rs.
export const TRADER_SYNC_MIN_MS = 30_000;

// addr → epoch ms of the last COMPLETED sync (success or failure; a failed
// fetch still spent the request budget).
const lastTraderSyncAt = new Map<string, number>();
// addr → in-flight sync, so two callers landing in the same tick share one
// request instead of racing two identical page walks.
const traderSyncInFlight = new Map<string, Promise<PolymarketTrade[]>>();

// Freshest series we can hand back without touching the network: whichever
// of the caller's baseline and the trade cache carries the newer trade.
function freshestKnownTrades(
  address: string,
  existing: PolymarketTrade[],
): PolymarketTrade[] {
  const cached = getTradeCache(address);
  if (!cached) return existing;
  const newestOf = (ts: PolymarketTrade[]) =>
    ts.reduce((m, t) => (t.timestamp > m ? t.timestamp : m), 0);
  return newestOf(cached) >= newestOf(existing) ? cached : existing;
}

// Incremental refresh — paginates /activity from the newest trade backwards
// and stops as soon as it hits a trade we already have cached, then merges
// the fresh window with the existing array (dedupe by tx hash). Avoids the
// 90-day full refetch the live engine was doing every minute, and prevents
// older trades from being silently dropped if Polymarket trims the activity
// feed. `existing` is whatever's already in memory or trade cache; pass `[]`
// to force a full backfill (delegates to fetchWalletTradesUntil).
//
// Rate-gated by TRADER_SYNC_MIN_MS — see above.
export async function fetchWalletTradesIncremental(
  address: string,
  existing: PolymarketTrade[],
  windowUntilTsSec: number,
  maxPages = 5,
): Promise<PolymarketTrade[]> {
  const key = address.toLowerCase();
  const inFlight = traderSyncInFlight.get(key);
  if (inFlight) return inFlight;
  const since = Date.now() - (lastTraderSyncAt.get(key) ?? 0);
  if (since < TRADER_SYNC_MIN_MS) return freshestKnownTrades(address, existing);

  const run = syncWalletTrades(address, existing, windowUntilTsSec, maxPages)
    .finally(() => {
      lastTraderSyncAt.set(key, Date.now());
      traderSyncInFlight.delete(key);
    });
  traderSyncInFlight.set(key, run);
  return run;
}

async function syncWalletTrades(
  address: string,
  existing: PolymarketTrade[],
  windowUntilTsSec: number,
  maxPages: number,
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
  // expects from the bulk fetch path. Trades that have aged past the global
  // 30-day ceiling fall off here so the cache can't grow without bound.
  const floorMs = Date.now() - MAX_LOOKBACK_DAYS * 86400_000;
  const merged = [...fresh, ...existing]
    .filter((t) => t.timestamp >= floorMs)
    .sort((a, b) => b.timestamp - a.timestamp);
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
        slug: t.slug ? String(t.slug) : undefined,
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

export async function fetchPositions(
  address: string,
  opts: { bypassCache?: boolean } = {},
): Promise<PolymarketPosition[]> {
  // Cache is hour-bucketed with no TTL — fine for browsing other traders'
  // historical positions, but lethal for the user's own deposit wallet:
  // stale entries linger up to ~60min and the SELL ALL button submits
  // against sizes that no longer exist on-chain ("balance: 4290, order
  // amount: 32000000"). Callers reading the user's own wallet must pass
  // bypassCache.
  if (!opts.bypassCache) {
    const cached = getCached<PolymarketPosition[]>(address, "positions");
    if (cached) return cached;
  }

  // Polymarket data API: /positions?user=<address>&sizeThreshold=.1
  // limit was 100, which silently truncated big traders — the profile
  // showed exactly "100 positions" for anyone holding more. 500 is the
  // data-api's max per page (same cap as /activity), so paginate with
  // offset until a short page. 2000 is a sanity ceiling, not a target.
  const positions: Record<string, unknown>[] = [];
  for (let offset = 0; offset < 2000; offset += 500) {
    const raw = await polyApi("positions", {
      user: address,
      sizeThreshold: ".1",
      limit: "500",
      offset: String(offset),
    }) as unknown;
    if (!Array.isArray(raw) || raw.length === 0) break;
    positions.push(...(raw as Record<string, unknown>[]));
    if (raw.length < 500) break;
  }

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
        negRisk: Boolean(p.negativeRisk ?? p.negRisk ?? p.neg_risk ?? false),
        redeemable: Boolean(p.redeemable ?? false),
      };
    })
    .filter((p) => p.conditionId && p.size > 0);

  if (!opts.bypassCache) setCache(address, "positions", result);
  return result;
}

// ── Closed (fully exited) positions with realized P&L ───────────
//
// data-api /closed-positions — one row per position the wallet has fully
// exited (sold and/or redeemed), with the realized P&L already computed
// upstream. This is what lets the UI show past trades' outcomes without
// reconstructing FIFO from the whole activity feed.
export interface ClosedPosition {
  conditionId: string;
  tokenId: string;      // CTF outcome token id (data-api `asset`)
  market: string;
  outcome: string;
  totalBought: number;  // shares accumulated over the position's life
  avgPrice: number;     // average entry
  curPrice: number;     // settlement/current price (1 = resolved winner, 0 = loser)
  realizedPnl: number;  // USDC realized over the position's life
  timestamp: number;    // ms — when the position closed
}

export async function fetchClosedPositions(
  address: string,
  maxRows = 1000,
): Promise<ClosedPosition[]> {
  // The data-api silently caps limit at 50 — page until a short page.
  const PAGE = 50;
  const out: ClosedPosition[] = [];
  const safe = (n: unknown, fallback = 0): number => {
    const v = Number(n);
    return Number.isFinite(v) ? v : fallback;
  };
  for (let offset = 0; offset < maxRows; offset += PAGE) {
    const raw = await polyApi("closed-positions", {
      user: address,
      limit: String(PAGE),
      offset: String(offset),
    }) as unknown;
    if (!Array.isArray(raw) || raw.length === 0) break;
    for (const p of raw as Record<string, unknown>[]) {
      const ts = safe(p.timestamp);
      out.push({
        conditionId: String(p.conditionId || ""),
        tokenId: String(p.asset || ""),
        market: String(p.title || p.slug || ""),
        outcome: String(p.outcome || ""),
        totalBought: safe(p.totalBought),
        avgPrice: safe(p.avgPrice),
        curPrice: safe(p.curPrice),
        realizedPnl: safe(p.realizedPnl),
        timestamp: ts > 1e12 ? ts : ts * 1000,
      });
    }
    if (raw.length < PAGE) break;
  }
  return out.filter((p) => p.conditionId);
}

// ── Trader Sharpe / EV stats (powers copy-engine top-N sampling) ────
//
// For each candidate copy trade we score:
//   score = (trader.roi_30d / trader.stdev_30d) * trade_notional
//         = trader_sharpe * notional
//
// `roi` is the trader's realized return on cash deployed in the
// window. `stdev` is the stdev of *per-closed-trade* fractional return
// (realized / entry_notional). Sharpe = roi / stdev — unitless,
// rewards consistency over lucky single hits.
//
// SELLs without a matching in-window BUY ("hasBasis = false") are
// dropped: we can't compute a real return for them and the
// `realized = 0` placeholder would deflate stdev.
//
// `sampleSize` is the closed-trade count. Below ~3 the stdev is too
// noisy to be meaningful — callers should treat sharpe as 0 (i.e.
// skip the trader) until more closed trades land.
export async function fetchTraderRoiStats(
  address: string,
  windowDays: number = 30,
): Promise<TraderRoiStats> {
  const windowMs = windowDays * 86400 * 1000;
  const cutoffMs = Date.now() - windowMs;
  const untilSec = Math.floor(cutoffMs / 1000);

  const trades = await fetchWalletTradesUntil(address, untilSec);
  const positions = await fetchPositions(address).catch(() => [] as PolymarketPosition[]);

  // FIFO with the window cutoff so we don't seed avg-prices from
  // positions opened before the window — keeps stats representative
  // of recent behavior.
  const annotated = computeFifoTrades(trades, positions, cutoffMs);
  const inWindow = annotated.filter((t) => t.timestamp >= cutoffMs);

  let cashDeployed = 0;
  for (const t of inWindow) {
    if (t.side === "BUY") cashDeployed += t.price * t.size;
  }

  // Per-trade fractional return on closed SELLs only.
  const returns: number[] = [];
  for (const t of inWindow) {
    if (t.side !== "SELL" || !t.hasBasis || !t.buyPrice) continue;
    const entryNotional = t.buyPrice * t.size;
    if (entryNotional <= 0) continue;
    returns.push(t.realized / entryNotional);
  }

  return {
    address: address.toLowerCase(),
    windowDays,
    ...statsFromReturns(returns),
    cashDeployed,
    syncedAt: Date.now(),
  };
}
